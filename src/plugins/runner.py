import cherrypy
import logging
import subprocess
import sys
import time
import traceback
import ujson as json
from threading import Thread, Lock
from Queue import Queue, Empty, Full
from toolbox import PluginIPCStream

logger = logging.getLogger("openmotics")


class PluginRunner:

    def __init__(self, name, runtime_path, plugin_path, logger, command_timeout=5):
        self.runtime_path = runtime_path
        self.plugin_path = plugin_path
        self.command_timeout = command_timeout

        self._logger = logger
        self._cid = 0
        self._proc = None
        self._running = False
        self._process_running = False
        self._command_lock = Lock()
        self._response_queue = Queue()
        self._stream = None

        self.name = name
        self.version = None
        self.interfaces = None

        self._receivers = []
        self._exposes = []
        self._metric_collectors = []
        self._metric_receivers = []

        self._async_command_thread = None
        self._async_command_queue = None

        self._commands_executed = 0
        self._commands_failed = 0

        self.__collector_runs = {}

    def start(self):
        if self._running:
            raise Exception('PluginRunner is already running')

        self.logger('[Runner] Starting')

        python_executable = sys.executable
        if python_executable is None or len(python_executable) == 0:
            python_executable = '/usr/bin/python'

        self._proc = subprocess.Popen([python_executable, "runtime.py", "start", self.plugin_path],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                      cwd=self.runtime_path)
        self._process_running = True

        self._commands_executed = 0
        self._commands_failed = 0

        self._stream = PluginIPCStream(stream=self._proc.stdout,
                                       logger=lambda message, ex: self.logger('{0}: {1}'.format(message, ex)),
                                       command_receiver=self._process_command)
        self._stream.start()

        start_out = self._do_command('start', timeout=180)
        self.name = start_out['name']
        self.version = start_out['version']
        self.interfaces = start_out['interfaces']

        self._receivers = start_out['receivers']
        self._exposes = start_out['exposes']
        self._metric_collectors = start_out['metric_collectors']
        self._metric_receivers = start_out['metric_receivers']

        exception = start_out.get('exception')
        if exception is not None:
            raise RuntimeError(exception)

        self._async_command_queue = Queue(1000)
        self._async_command_thread = Thread(target=self._perform_async_commands,
                                            name='PluginRunner {0} async thread'.format(self.plugin_path))
        self._async_command_thread.daemon = True
        self._async_command_thread.start()

        self._running = True

    def logger(self, message):
        self._logger(message)
        logger.info('Plugin {0} - {1}'.format(self.name, message))

    def get_webservice(self, webinterface):
        class Service:
            def __init__(self, runner):
                self.runner = runner
                # Set the user controller, required to check the auth token
                self._user_controller = webinterface._user_controller

            def _cp_dispatch(self, vpath):
                method = vpath.pop()
                for exposed in self.runner._exposes:
                    if exposed['name'] == method:
                        cherrypy.request.params['method'] = method
                        cherrypy.response.headers['Content-Type'] = exposed['content_type']
                        if exposed['auth'] is True:
                            cherrypy.request.hooks.attach('before_handler',
                                                          cherrypy.tools.authenticated.callable)
                        return self

                return None

            @cherrypy.expose
            def index(self, method, *args, **kwargs):
                try:
                    return self.runner.request(method, args=args, kwargs=kwargs)
                except Exception as ex:
                    cherrypy.response.headers["Content-Type"] = "application/json"
                    cherrypy.response.status = 500
                    return json.dumps({"success": False, "msg": str(ex)})

        return Service(self)

    def is_running(self):
        return self._running

    def stop(self):
        if self._process_running:
            self._running = False

            self.logger('[Runner] Sending stop command')
            try:
                self._do_command('stop')
            except Exception as exception:
                self.logger('[Runner] Exception during stopping plugin: {0}'.format(exception))
            time.sleep(0.1)

            self._stream.stop()
            self._process_running = False

            if self._proc.poll() is None:
                self.logger('[Runner] Terminating process')
                try:
                    self._proc.terminate()
                except Exception as exception:
                    self.logger('[Runner] Exception during terminating plugin: {0}'.format(exception))
                time.sleep(0.5)

                if self._proc.poll() is None:
                    self.logger('[Runner] Killing process')
                    try:
                        self._proc.kill()
                    except Exception as exception:
                        self.logger('[Runner] Exception during killing plugin: {0}'.format(exception))

    def process_input_status(self, input_event):
        event_json = input_event.serialize()
        self._do_async('input_status', {'event': event_json}, should_filter=True)

    def process_output_status(self, status):
        self._do_async('output_status', {'status': status}, should_filter=True)

    def process_light_status(self, status):
        self._do_async('light_status', {'status': status}, should_filter=True)

    def process_shutter_status(self, status):
        self._do_async('shutter_status', status, should_filter=True)

    def process_event(self, code):
        self._do_async('receive_events', {'code': code}, should_filter=True)

    def collect_metrics(self):
        for mc in self._metric_collectors:
            try:
                now = time.time()
                (name, interval) = (mc['name'], mc['interval'])

                if self.__collector_runs.get(name, 0) < now - interval:
                    self.__collector_runs[name] = now
                    metrics = self._do_command('collect_metrics', {'name': name})['metrics']
                    for metric in metrics:
                        if metric is None:
                            continue
                        metric['source'] = self.name
                        yield metric
            except Exception as exception:
                self.logger('[Runner] Exception while collecting metrics {0}: {1}'.format(exception, traceback.format_exc()))

    def get_metric_receivers(self):
        return self._metric_receivers

    def distribute_metrics(self, method, metrics):
        self._do_async('distribute_metrics', {'name': method,
                                              'metrics': metrics})

    def get_metric_definitions(self):
        return self._do_command('get_metric_definitions')['metric_definitions']

    def request(self, method, args=None, kwargs=None):
        if args is None:
            args = []
        if kwargs is None:
            kwargs = {}
        ret = self._do_command('request', {'method': method,
                                           'args': args,
                                           'kwargs': kwargs})
        if ret['success']:
            return ret['response']
        else:
            raise Exception('{0}: {1}'.format(ret['exception'], ret['stacktrace']))

    def remove_callback(self):
        self._do_command('remove_callback')

    def _process_command(self, response):
        if not self._process_running:
            return
        exit_code = self._proc.poll()
        if exit_code is not None:
            self.logger('[Runner] Stopped with exit code {0}'.format(exit_code))
            self._process_running = False
            return

        if response['cid'] == 0:
            self._handle_async_response(response)
        elif response['cid'] == self._cid:
            self._response_queue.put(response)
        else:
            self.logger('[Runner] Received message with unknown cid: {0}'.format(response))

    def _handle_async_response(self, response):
        if response['action'] == 'logs':
            self.logger(response['logs'])
        else:
            self.logger('[Runner] Unkown async message: {0}'.format(response))

    def _do_async(self, action, fields, should_filter=False):
        if (should_filter and action not in self._receivers) or not self._process_running:
            return

        try:
            self._async_command_queue.put({'action': action, 'fields': fields}, block=False)
        except Full:
            self.logger('Async action cannot be queued, queue is full')

    def _perform_async_commands(self):
        while self._process_running:
            try:
                # Give it a timeout in order to check whether the plugin is not stopped.
                command = self._async_command_queue.get(block=True, timeout=10)
                self._do_command(command['action'], command['fields'])
            except Empty:
                pass
            except Exception as exception:
                self.logger('[Runner] Failed to perform async command: {0}'.format(exception))

    def _do_command(self, action, fields=None, timeout=None):
        if fields is None:
            fields = {}
        self._commands_executed += 1
        if timeout is None:
            timeout = self.command_timeout

        if not self._process_running:
            raise Exception('Plugin was stopped')

        with self._command_lock:
            command = self._create_command(action, fields)
            self._proc.stdin.write(PluginIPCStream.write(command))
            self._proc.stdin.flush()

            try:
                response = self._response_queue.get(block=True, timeout=timeout)
                while response['cid'] != self._cid:
                    response = self._response_queue.get(block=False)
                exception = response.get('_exception')
                if exception is not None:
                    raise RuntimeError(exception)
                return response
            except Empty:
                self.logger('[Runner] No response within {0}s ({1})'.format(timeout, action))
                self._commands_failed += 1
                raise Exception('Plugin did not respond')

    def _create_command(self, action, fields=None):
        if fields is None:
            fields = {}
        self._cid += 1
        command = {'cid': self._cid,
                   'action': action}
        command.update(fields)
        return command

    def error_score(self):
        if self._commands_executed == 0:
            return 0
        else:
            score = float(self._commands_failed) / self._commands_executed
            self._commands_failed = 0
            self._commands_executed = 0
            return score

    def get_queue_length(self):
        if self._async_command_queue is None:
            return 0
        return self._async_command_queue.qsize()


class RunnerWatchdog:

    def __init__(self, plugin_runner, threshold=0.25, check_interval=60):
        self._plugin_runner = plugin_runner
        self._threshold = threshold
        self._check_interval = check_interval
        self._stopped = False

    def stop(self):
        self._stopped = True

    def start(self):
        thread = Thread(target=self.run, name='RunnerWatchdog for {0}'.format(self._plugin_runner.plugin_path))
        thread.daemon = True
        thread.start()

    def run(self):
        while not self._stopped:
            try:
                score = self._plugin_runner.error_score()
                if score > self._threshold:
                    self._plugin_runner.logger('[Watchdog] Stopping unhealthy runner')
                    self._plugin_runner.stop()
                if not self._plugin_runner.is_running():
                    self._plugin_runner.logger('[Watchdog] Starting stopped runner')
                    self._plugin_runner.start()
            except Exception as e:
                self._plugin_runner.logger('[Watchdog] Exception in watchdog: {0}'.format(e))

            time.sleep(self._check_interval)
