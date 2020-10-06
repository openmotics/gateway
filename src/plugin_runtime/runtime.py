from __future__ import absolute_import

import logging
import os
import sys
import time
import traceback
from threading import Thread

import six

from gateway.events import GatewayEvent
from plugin_runtime import base
from plugin_runtime.interfaces import has_interface
from plugin_runtime.utils import check_plugin, get_plugin_class, \
    get_special_methods
from plugin_runtime.web import WebInterfaceDispatcher
from toolbox import PluginIPCReader, PluginIPCWriter, Toolbox

logger = logging.getLogger('openmotics')

if False:  # MYPY
    from typing import Any, Callable, Dict, List, Optional


class PluginRuntime(object):

    SUPPORTED_DECORATOR_VERSIONS = {'input_status': [1, 2],
                                    'output_status': [1, 2],
                                    'shutter_status': [1, 2, 3],
                                    'ventilation_status': [1],
                                    'receive_events': [1],
                                    'background_task': [1],
                                    'on_remove': [1]}

    def __init__(self, path):
        # type: (str) -> None
        self._stopped = False
        self._path = path.rstrip('/')

        self._decorated_methods = {'input_status': [],
                                   'output_status': [],
                                   'shutter_status': [],
                                   'ventilation_status': [],
                                   'receive_events': [],
                                   'background_task': [],
                                   'on_remove': []}  # type: Dict[str,List[Any]]

        self._name = None
        self._version = None
        self._interfaces = []  # type: List[Any]
        self._exposes = []  # type: List[Any]
        self._metric_definitions = []  # type: List[Any]
        self._metric_collectors = []  # type: List[Any]
        self._metric_receivers = []  # type: List[Any]

        self._plugin = None
        self._writer = PluginIPCWriter(os.fdopen(sys.stdout.fileno(), 'wb', 0))
        self._reader = PluginIPCReader(os.fdopen(sys.stdin.fileno(), 'rb', 0),
                                       self._writer.log_exception)

        self._webinterface = WebInterfaceDispatcher(self._writer.log)

    def _init_plugin(self):
        # type: () -> None
        plugin_root = os.path.dirname(self._path)
        plugin_dir = os.path.basename(self._path)

        # Add the plugin and it's eggs to the python path
        sys.path.insert(0, plugin_root)
        for egg_file in os.listdir(self._path):
            if egg_file.endswith('.egg'):
                sys.path.append(os.path.join(self._path, egg_file))

        # Expose plugins.base to the plugin
        sys.modules['plugins'] = sys.modules['__main__']
        sys.modules["plugins.base"] = base

        # Instanciate the plugin class
        plugin_class = get_plugin_class(plugin_dir)
        check_plugin(plugin_class)

        # Set the name, version, interfaces
        self._name = plugin_class.name
        self._version = plugin_class.version
        self._interfaces = plugin_class.interfaces

        # Initialze the plugin
        self._plugin = plugin_class(self._webinterface, self._writer.log)

        for decorator_name, decorated_methods in six.iteritems(self._decorated_methods):
            for decorated_method, decorator_version in get_special_methods(self._plugin, decorator_name):
                # only add if supported, raise if an unsupported version is found
                if decorator_version not in PluginRuntime.SUPPORTED_DECORATOR_VERSIONS[decorator_name]:
                    raise NotImplementedError('Decorator {} version {} is not supported'.format(decorator_name, decorator_version))
                decorated_methods.append(decorated_method)  # add the decorated method to the list

        # Set the exposed methods
        for decorated_method, _ in get_special_methods(self._plugin, 'om_expose'):
            self._exposes.append({'name': decorated_method.__name__,
                                  'auth': decorated_method.om_expose['auth'],
                                  'content_type': decorated_method.om_expose['content_type']})

        # Set the metric collectors
        for decorated_method, _ in get_special_methods(self._plugin, 'om_metric_data'):
            self._metric_collectors.append({'name': decorated_method.__name__,
                                            'interval': decorated_method.om_metric_data['interval']})

        # Set the metric receivers
        for decorated_method, _ in get_special_methods(self._plugin, 'om_metric_receive'):
            self._metric_receivers.append({'name': decorated_method.__name__,
                                           'source': decorated_method.om_metric_receive['source'],
                                           'metric_type': decorated_method.om_metric_receive['metric_type'],
                                           'interval': decorated_method.om_metric_receive['interval']})

        # Set the metric definitions
        if has_interface(plugin_class, 'metrics', '1.0'):
            if hasattr(plugin_class, 'metric_definitions'):
                self._metric_definitions = plugin_class.metric_definitions

    def _start_background_tasks(self):
        # type: () -> None
        """ Start all background tasks. """
        for decorated_method in self._decorated_methods['background_task']:
            thread = Thread(target=self._run_background_task, args=(decorated_method,))
            thread.name = 'Background thread ({0})'.format(decorated_method.__name__)
            thread.daemon = True
            thread.start()

    def get_decorators_in_use(self):
        registered_decorators = {}
        for decorator_name, decorated_methods in six.iteritems(self._decorated_methods):
            decorator_versions_in_use = set()
            for decorated_method in decorated_methods:
                decorator_version = getattr(decorated_method, decorator_name).get('version', 1)
                decorator_versions_in_use.add(decorator_version)
            registered_decorators[decorator_name] = list(decorator_versions_in_use)

        # something in the form of e.g. {'output_status': [1,2], 'input_status': [1]} where 1,2,... are the versions
        return registered_decorators

    def _run_background_task(self, task):
        # type: (Callable[[],None]) -> None
        running = True
        while running:
            try:
                task()
                running = False  # Stop execution if the task returns without exception
            except Exception as exception:
                self._writer.log_exception('background task', exception)
                time.sleep(30)

    def process_stdin(self):
        # type: () -> None
        self._reader.start()
        while not self._stopped:
            command = self._reader.get(block=True)
            if command is None:
                continue

            action = command['action']
            action_version = command['action_version']
            response = {'cid': command['cid'], 'action': action}
            try:
                ret = None
                if action == 'start':
                    ret = self._handle_start()
                elif action == 'stop':
                    ret = self._handle_stop()
                elif action == 'input_status':
                    ret = self._handle_input_status(command['event'])
                elif action == 'output_status':
                    # v1 = state, v2 = event
                    if action_version == 1:
                        ret = self._handle_output_status(command['status'], data_type='status')
                    else:
                        ret = self._handle_output_status(command['event'], data_type='event')
                elif action == 'ventilation_status':
                    ret = self._handle_ventilation_status(command['event'])
                elif action == 'shutter_status':
                    # v1 = state as list, v2 = state as dict, v3 = event
                    if action_version == 1:
                        ret = self._handle_shutter_status(command['status'], data_type='status')
                    elif action_version == 2:
                        ret = self._handle_shutter_status(command['status'], data_type='status_dict')
                    else:
                        ret = self._handle_shutter_status(command['event'], data_type='event')
                elif action == 'receive_events':
                    ret = self._handle_receive_events(command['code'])
                elif action == 'get_metric_definitions':
                    ret = self._handle_get_metric_definitions()
                elif action == 'collect_metrics':
                    ret = self._handle_collect_metrics(command['name'])
                elif action == 'distribute_metrics':
                    ret = self._handle_distribute_metrics(command['name'], command['metrics'])
                elif action == 'request':
                    ret = self._handle_request(command['method'], command['args'], command['kwargs'])
                elif action == 'remove_callback':
                    ret = self._handle_remove_callback()
                elif action == 'ping':
                    pass  # noop
                else:
                    raise RuntimeError('Unknown action: {0}'.format(action))

                if ret is not None:
                    response.update(ret)
            except Exception as exception:
                response['_exception'] = str(exception)
            self._writer.write(response)

    def _handle_start(self):
        # type: () -> Dict[str,Any]
        """ Handles the start command. Cover exceptions manually to make sure as much metadata is returned as possible. """
        data = {}  # type: Dict[str,Any]
        try:
            self._init_plugin()
            self._start_background_tasks()
        except Exception as exception:
            data['exception'] = str(exception)
        data.update({'name': self._name,
                     'version': self._version,
                     'decorators': self.get_decorators_in_use(),
                     'exposes': self._exposes,
                     'interfaces': self._interfaces,
                     'metric_collectors': self._metric_collectors,
                     'metric_receivers': self._metric_receivers})
        return data

    def _handle_stop(self):

        def delayed_stop():
            time.sleep(2)
            os._exit(0)

        stop_thread = Thread(target=delayed_stop)
        stop_thread.daemon = True
        stop_thread.start()

        self._stopped = True

    def _handle_input_status(self, data):
        event = GatewayEvent.deserialize(data)
        # get relevant event details
        input_id = event.data['id']
        status = event.data['status']
        for decorated_method in self._decorated_methods['input_status']:
            decorator_version = decorated_method.input_status.get('version', 1)
            if decorator_version == 1:
                # Backwards compatibility: only send rising edges of the input (no input releases)
                if status:
                    self._writer.with_catch('input status', decorated_method, [(input_id, None)])
            elif decorator_version == 2:
                # Version 2 will send ALL input status changes AND in a dict format
                self._writer.with_catch('input status', decorated_method, [{'input_id': input_id, 'status': status}])
            else:
                error = NotImplementedError('Version {} is not supported for input status decorators'.format(decorator_version))
                self._writer.log_exception('input status', error)

    def _handle_output_status(self, data, data_type='status'):
        event = GatewayEvent.deserialize(data) if data_type == 'event' else None
        for receiver in self._decorated_methods['output_status']:
            decorator_version = receiver.output_status.get('version', 1)
            if decorator_version not in PluginRuntime.SUPPORTED_DECORATOR_VERSIONS['output_status']:
                error = NotImplementedError('Version {} is not supported for output status decorators'.format(decorator_version))
                self._writer.log_exception('output status', error)
            else:
                if decorator_version == 1 and data_type == 'status':
                    self._writer.with_catch('output status', receiver, [data])
                elif decorator_version == 2 and event:
                    self._writer.with_catch('output status', receiver, [event.data])

    def _handle_ventilation_status(self, data):
        event = GatewayEvent.deserialize(data)
        for receiver in self._decorated_methods['ventilation_status']:
            self._writer.with_catch('ventilation status', receiver, [event.data])

    def _handle_shutter_status(self, data, data_type='status'):
        event = GatewayEvent.deserialize(data) if data_type == 'event' else None
        for receiver in self._decorated_methods['shutter_status']:
            decorator_version = receiver.shutter_status.get('version', 1)
            if decorator_version not in PluginRuntime.SUPPORTED_DECORATOR_VERSIONS['shutter_status']:
                error = NotImplementedError('Version {} is not supported for shutter status decorators'.format(decorator_version))
                self._writer.log_exception('shutter status', error)
            else:
                if decorator_version == 1 and data_type == 'status':
                    self._writer.with_catch('shutter status', receiver, [data])
                elif decorator_version == 2 and data_type == 'status_dict':
                    self._writer.with_catch('shutter status', receiver, [data['status'], data['detail']])
                elif decorator_version == 3 and event:
                    self._writer.with_catch('shutter status', receiver, [event.data])

    def _handle_receive_events(self, code):
        for receiver in self._decorated_methods['receive_events']:
            decorator_version = receiver.receive_events.get('version', 1)
            if decorator_version == 1:
                self._writer.with_catch('process event', receiver, [code])
            else:
                error = NotImplementedError('Version {} is not supported for receive events decorators'.format(decorator_version))
                self._writer.log_exception('receive events', error)

    def _handle_remove_callback(self):
        for decorated_method in self._decorated_methods['on_remove']:
            decorator_version = decorated_method.on_remove.get('version', 1)
            if decorator_version == 1:
                try:
                    decorated_method()
                except Exception as exception:
                    self._writer.log_exception('on remove', exception)
            else:
                error = NotImplementedError('Version {} is not supported for shutter status decorators'.format(decorator_version))
                self._writer.log_exception('on remove', error)

    def _handle_get_metric_definitions(self):
        return {'metric_definitions': self._metric_definitions}

    def _handle_collect_metrics(self, name):
        metrics = []
        collect = getattr(self._plugin, name)
        try:
            metrics.extend(list(collect()))
        except Exception as exception:
            self._writer.log_exception('collect metrics', exception)
        return {'metrics': metrics}

    def _handle_distribute_metrics(self, name, metrics):
        receive = getattr(self._plugin, name)
        for metric in metrics:
            self._writer.with_catch('distribute metric', receive, [metric])

    def _handle_request(self, method, args, kwargs):
        func = getattr(self._plugin, method)
        requested_parameters = set(Toolbox.get_parameter_names(func)) - {'self'}
        difference = set(kwargs.keys()) - requested_parameters
        if difference:
            # Analog error message as the default CherryPy behavior
            return {'success': False, 'exception': 'Unexpected query string parameters: {0}'.format(', '.join(difference))}
        difference = requested_parameters - set(kwargs.keys())
        if difference:
            # Analog error message as the default CherryPy behavior
            return {'success': False, 'exception': 'Missing parameters: {0}'.format(', '.join(difference))}
        try:
            return {'success': True, 'response': func(*args, **kwargs)}
        except Exception as exception:
            return {'success': False, 'exception': str(exception), 'stacktrace': traceback.format_exc()}


def start_runtime(plugin_path):
    # type: (str) -> None

    def watch_parent():
        parent = os.getppid()
        # If the parent process gets kills, this process will be attached to init.
        # In that case the plugin should stop running.
        while True:
            if os.getppid() != parent:
                os._exit(1)
            time.sleep(1)

    # Keep an eye on our parent process
    watcher = Thread(target=watch_parent)
    watcher.daemon = True
    watcher.start()

    # Start the runtime
    try:
        runtime = PluginRuntime(path=plugin_path)
        runtime.process_stdin()
    except BaseException as ex:
        writer = PluginIPCWriter(os.fdopen(sys.stdout.fileno(), 'wb', 0))
        writer.log_exception('__main__', ex)
        os._exit(1)

    os._exit(0)
