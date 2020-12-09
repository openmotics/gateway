# Copyright (C) 2016 OpenMotics BV
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
""" The OpenMotics plugin controller. """

from __future__ import absolute_import

import logging
import os
import pkgutil
import traceback
from datetime import datetime
from threading import Lock, Timer

import six

import constants
from gateway.events import GatewayEvent
from gateway.models import Config, Plugin
from ioc import INJECTED, Inject, Injectable, Singleton
from plugins.runner import PluginRunner, RunnerWatchdog

if False:  # MYPY
    from typing import Dict, List, Optional
    from gateway.output_controller import OutputController
    from gateway.shutter_controller import ShutterController
    from gateway.webservice import WebInterface

logger = logging.getLogger('openmotics')


@Injectable.named('plugin_controller')
@Singleton
class PluginController(object):
    """ The controller keeps track of all plugins in the system. """

    DEPENDENCIES_TIMER = 30.0

    @Inject
    def __init__(self,
                 web_interface=INJECTED, output_controller=INJECTED,
                 shutter_controller=INJECTED,
                 runtime_path=None,
                 plugins_path=None,
                 plugin_config_path=None):
        # type: (WebInterface, OutputController, ShutterController, str, str, str) -> None
        self._webinterface = web_interface
        self._output_controller = output_controller
        self._shuttercontroller = shutter_controller
        self._runtime_path = runtime_path or constants.get_plugin_runtime_dir()
        self._plugins_path = plugins_path or constants.get_plugin_dir()
        self._plugin_config_path = constants.get_plugin_config_dir()

        self._stopped = True
        self._logs = {}  # type: Dict[str, List[str]]
        self._runners = {}  # type: Dict[str, PluginRunner]
        self._runner_watchdogs = {}  # type: Dict[str, RunnerWatchdog]
        self._dependencies_timer = None  # type: Optional[Timer]
        self._dependencies_lock = Lock()

        self._metrics_controller = None
        self._metrics_collector = None
        self._web_service = None

    def start(self):
        # type: () -> None
        """ Start the plugins and expose them via the webinterface. """
        if not self._stopped:
            logger.error('The PluginController is already running')
            return

        # TODO query the orm instead, used now to initialize already installed plugins.
        objects = pkgutil.iter_modules([self._plugins_path])  # (module_loader, name, ispkg)
        package_names = [o[1] for o in objects if o[2]]

        self._runners = {}
        self._runner_watchdogs = {}
        runners = []
        for package_name in package_names:
            runner = self._init_plugin_runner(package_name)
            if runner is not None:
                runners.append(runner)
        for runner in runners:
            self._start_plugin_runner(runner, runner.name)

    def stop(self):
        # type: () -> None
        for runner_name in list(self._runners.keys()):
            self._destroy_plugin_runner(runner_name)
        self._stopped = True

    def set_metrics_controller(self, metrics_controller):
        """ Sets the metrics controller """
        self._metrics_controller = metrics_controller

    def set_metrics_collector(self, metrics_collector):
        """ Sets the metrics collector """
        self._metrics_collector = metrics_collector

    def set_webservice(self, web_service):
        """ Sets the web service """
        self._web_service = web_service

    def _runner_state_changed(self, runner_name, state):
        runner = self._runners.get(runner_name)
        if runner is None:
            return
        if state == PluginRunner.State.RUNNING:
            PluginController._update_orm(runner.name, runner.version)
        with self._dependencies_lock:
            if self._dependencies_timer is not None:
                self._dependencies_timer.cancel()
            self._dependencies_timer = Timer(PluginController.DEPENDENCIES_TIMER, self._update_dependencies)
            self._dependencies_timer.start()

    def _init_plugin_runner(self, plugin_name):
        """ Initializes a single plugin runner """
        try:
            if plugin_name in self._runners.keys():
                self.log(plugin_name, '[Runner] Could not init plugin', 'Multiple plugins with the same name found')
                return
            plugin_path = os.path.join(self._plugins_path, plugin_name)
            runner = PluginRunner(name=plugin_name,
                                  runtime_path=self._runtime_path,
                                  plugin_path=plugin_path,
                                  logger=self.get_logger(plugin_name),
                                  state_callback=self._runner_state_changed)
            self._runners[runner.name] = runner
            self._runner_watchdogs[runner.name] = RunnerWatchdog(runner)
            return runner
        except Exception as exception:
            self.log(plugin_name, '[Runner] Could not initialize plugin', exception)

    def _start_plugin_runner(self, runner, runner_name):
        # type: (PluginRunner, str) -> None
        """ Starts a single plugin runner """
        watchdog = None
        try:
            logger.info('Plugin {0}: {1}'.format(runner_name, 'Starting...'))
            watchdog = self._runner_watchdogs.get(runner_name)
            if watchdog is not None:
                success = watchdog.start()  # The plugin will be started by the watchdog
                if success:
                    PluginController._update_orm(runner.name, runner.version)
                    logger.info('Plugin {0}: {1}'.format(runner_name, 'Starting... Done'))
                else:
                    logger.error('Plugin {0}: {1}'.format(runner_name, 'Starting... Failed'))
        except Exception as exception:
            logger.exception('Plugin {0}: {1}'.format(runner_name, 'Starting... Failed'))
            try:
                if watchdog is not None:
                    watchdog.stop()
                runner.stop()
            except Exception:
                pass  # Try as best as possible to stop the plugin
            self.log(runner.name, '[Runner] Could not start plugin', exception, traceback.format_exc())

    def start_plugin(self, plugin_name):
        """ Request to start a runner """
        runner = self._runners.get(plugin_name)
        if runner is None:
            return False
        if not runner.is_running():
            self._start_plugin_runner(runner, plugin_name)
        return runner.is_running()

    def _stop_plugin_runner(self, runner_name):
        """ Stops a single plugin runner """
        runner = self._runners.get(runner_name)
        if runner is None:
            return
        try:
            logger.info('Plugin {0}: {1}'.format(runner.name, 'Stopping...'))
            watchdog = self._runner_watchdogs.get(runner_name)
            if watchdog is not None:
                watchdog.stop()
            runner.stop()
            logger.info('Plugin {0}: {1}'.format(runner.name, 'Stopping... Done'))
        except Exception as exception:
            self.log(runner.name, '[Runner] Could not stop plugin', exception)

    def stop_plugin(self, plugin_name):
        """ Request to stop a runner """
        runner = self._runners.get(plugin_name)
        if runner is None:
            return False
        self._stop_plugin_runner(runner.name)
        return runner.is_running()

    def _destroy_plugin_runner(self, runner_name):
        """ Removes a runner """
        self._stop_plugin_runner(runner_name)

        self._logs.pop(runner_name, None)
        self._runners.pop(runner_name, None)
        self._runner_watchdogs.pop(runner_name, None)

    def _update_dependencies(self):
        """ When a runner is added/removed, this call updates all code that needs to know about plugins """
        if self._webinterface is not None and self._web_service is not None:
            self._web_service.update_tree(self._get_cherrypy_mounts())
        if self._metrics_collector is not None:
            self._metrics_collector.set_plugin_intervals(self._get_metric_receivers())
        if self._metrics_controller is not None:
            self._metrics_controller.set_plugin_definitions(self._get_metric_definitions())

    @staticmethod
    def _update_orm(name, version):
        # type: (str, str) -> None
        try:
            plugin, _ = Plugin.get_or_create(name=name, defaults={'version': version})
            if plugin.version != version:
                plugin.version = version
                plugin.save()
        except Exception as ex:
            logger.error('Could not store Plugin version: {0}'.format(ex))

    def get_plugins(self):
        # type: () -> List[PluginRunner]
        """
        Get a list of all installed plugins.
        """
        plugins = []
        for plugin_orm in list(Plugin.select()):
            plugin = self._runners.get(plugin_orm.name)
            if plugin:
                plugins.append(plugin)
            else:
                logger.warning('missing runner for plugin {}'.format(plugin_orm.name))
        return plugins

    def _get_plugin(self, name):
        """
        Get a plugin by name, None if it the plugin is not installed.

        :rtype: plugins.runner.PluginRunner
        """
        return self._runners.get(name)

    def install_plugin(self, md5, package_data):
        """ Install a new plugin. """
        from tempfile import mkdtemp
        from shutil import rmtree
        from subprocess import call
        import hashlib

        # Check if the md5 sum matches the provided md5 sum
        hasher = hashlib.md5()
        hasher.update(package_data)
        calculated_md5 = hasher.hexdigest()

        if calculated_md5 != md5.strip():
            raise Exception('The provided md5sum ({0}) does not match the actual md5 of the package data ({1}).'.format(md5, calculated_md5))

        tmp_dir = mkdtemp()
        try:
            # Extract the package_data
            with open('{0}/package.tgz'.format(tmp_dir), "wb") as tgz:
                tgz.write(package_data)

            retcode = call('cd {0}; mkdir new_package; tar xzf package.tgz -C new_package/'.format(tmp_dir),
                           shell=True)
            if retcode != 0:
                raise Exception('The package data (tgz format) could not be extracted.')

            # Create an __init__.py file, if it does not exist
            init_path = '{0}/new_package/__init__.py'.format(tmp_dir)
            if not os.path.exists(init_path):
                with open(init_path, 'w'):
                    # Create an empty file
                    pass

            # Check if the package contains a valid plugin
            _logger = self.get_logger('new_package')
            runner = PluginRunner(name=None,
                                  runtime_path=self._runtime_path,
                                  plugin_path='{0}/new_package'.format(tmp_dir),
                                  logger=_logger)
            runner.start()
            runner.stop()
            name, version = runner.name, runner.version
            self._logs.pop('new_pacakge', None)

            def parse_version(version_string):
                """ Parse the version from a string "x.y.z" to a tuple(x, y, z). """
                if version_string is None:
                    return 0, 0, 0  # A stopped plugin doesn't announce it's version, so make sure we can update stopped plugins
                return tuple([int(x) for x in version_string.split('.')])

            # Check if a newer version of the package is already installed
            installed_plugin = self._get_plugin(name)
            if installed_plugin is not None:
                if parse_version(version) <= parse_version(installed_plugin.version):
                    raise Exception('A newer version of plugins {0} is already installed (current version = {1}, to installed = {2}).'.format(name, installed_plugin.version, version))
                else:
                    # Remove the old version of the plugin
                    self._destroy_plugin_runner(name)
                    retcode = call('cd {0}; rm -R {1}'.format(self._plugins_path, name),
                                   shell=True)
                    if retcode != 0:
                        raise Exception('The old version of the plugin could not be removed.')

            # Check if the package directory exists, this can only be the case if a previous
            # install failed or if the plugin has gone corrupt: remove it!
            plugin_path = '{0}/{1}'.format(self._plugins_path, name)
            if os.path.exists(plugin_path):
                rmtree(plugin_path)

            # Install the package
            retcode = call('cd {0}; mv new_package {1}'.format(tmp_dir, plugin_path), shell=True)
            if retcode != 0:
                raise Exception('The package could not be installed.')

            runner = self._init_plugin_runner(name)
            if runner is None:
                raise Exception('Could not initialize plugin.')
            self._start_plugin_runner(runner, name)

            return 'Plugin successfully installed'
        finally:
            rmtree(tmp_dir)

    def remove_plugin(self, name):
        """
        Remove a plugin, this removes the plugin package and configuration.
        It also calls the remove function on the plugin to cleanup other files written by the
        plugin.
        """
        from shutil import rmtree

        plugin = self._get_plugin(name)

        # Check if the plugin in installed
        if plugin is None:
            Plugin.delete().where(Plugin.name == name).execute()
            raise Exception('Plugin \'{0}\' is not installed.'.format(name))

        # Execute the on_remove callbacks
        try:
            plugin.remove_callback()
        except Exception as exception:
            logger.error('Exception while removing plugin \'{0}\': {1}'.format(name, exception))

        # Stop the plugin process
        self._destroy_plugin_runner(name)
        self._update_dependencies()

        # Remove the plugin package
        plugin_path = '{0}/{1}'.format(self._plugins_path, name)
        try:
            rmtree(plugin_path)
        except Exception as exception:
            raise Exception('Error while removing package for plugin \'{0}\': {1}'.format(name, exception))

        # Remove the plugin configuration
        conf_file = '{0}/pi_{1}.conf'.format(self._plugin_config_path, name)
        if os.path.exists(conf_file):
            os.rename(conf_file, '{}.bak'.format(conf_file))

        # Finally remove database entry.
        Plugin.delete().where(Plugin.name == name).execute()

        return {'msg': 'Plugin successfully removed'}

    def _iter_running_runners(self):
        """
        :rtype: list of plugins.runner.PluginRunner
        """
        for runner_name in list(self._runners.keys()):
            runner = self._runners.get(runner_name)
            if runner is not None and runner.is_running():
                yield runner

    def process_observer_event(self, event):
        if event.type == GatewayEvent.Types.INPUT_CHANGE:
            # Should be called when the input status changes, notifies all plugins.
            for runner in self._iter_running_runners():
                runner.process_input_status(event)
        if event.type == GatewayEvent.Types.OUTPUT_CHANGE:
            # TODO: deprecate old versions that use state and move to events
            states = [(state.id, state.dimmer) for state in self._output_controller.get_output_statuses() if state.status]
            for runner in self._iter_running_runners():
                runner.process_output_status(data=states, action_version=1)  # send states as action version 1
                runner.process_output_status(data=event, action_version=2)   # send event as action version 2
        if event.type == GatewayEvent.Types.SHUTTER_CHANGE:
            # TODO: deprecate old versions that use state and move to events
            states = self._shuttercontroller.get_states()
            status = states['status']
            details = states['detail']
            for runner in self._iter_running_runners():
                runner.process_shutter_status(data=status, action_version=1)  # send states as action version 1
                runner.process_shutter_status(data=(status, details), action_version=2)  # send event as action version 2
                runner.process_shutter_status(data=event, action_version=3)  # send event as action version 3
        if event.type == GatewayEvent.Types.VENTILATION_CHANGE:
            for runner in self._iter_running_runners():
                runner.process_ventilation_status(data=event)
        if event.type == GatewayEvent.Types.THERMOSTAT_CHANGE:
            for runner in self._iter_running_runners():
                runner.process_thermostat_status(data=event)
        if event.type == GatewayEvent.Types.THERMOSTAT_GROUP_CHANGE:
            for runner in self._iter_running_runners():
                runner.process_thermostat_group_status(data=event)

    def process_event(self, code):
        """ Should be called when an event is triggered, notifies all plugins. """
        for runner in self._iter_running_runners():
            runner.process_event(code)

    def _request(self, name, method, args=None, kwargs=None):
        """ Allows to execute a programmatorical http request to the plugin """
        runner = self._runners.get(name)
        if runner is not None:
            return runner.request(method, args=args, kwargs=kwargs)

    def collect_metrics(self):
        """ Collects all metrics from all plugins """
        for runner in self._iter_running_runners():
            for metric in runner.collect_metrics():
                if metric is None:
                    continue
                else:
                    yield metric

    def distribute_metrics(self, metrics):
        """ Enqueues all metrics in a separate queue per plugin """
        rates = {'total': 0}
        rate_keys = []
        # Preprocess rate keys
        for metric in metrics:
            rate_key = '{0}.{1}'.format(metric['source'].lower(), metric['type'].lower())
            if rate_key not in rates:
                rates[rate_key] = 0
            rate_keys.append(rate_key)
        # Distribute
        for runner in self._iter_running_runners():
            for receiver in runner.get_metric_receivers():
                receiver_metrics = []
                try:
                    sources = self._metrics_controller.get_filter('source', receiver['source'])
                    metric_types = self._metrics_controller.get_filter('metric_type', receiver['metric_type'])
                    for index, metric in enumerate(metrics):
                        if metric['source'] in sources and metric['type'] in metric_types:
                            receiver_metrics.append(metric)
                            rates[rate_keys[index]] += 1
                            rates['total'] += 1
                    runner.distribute_metrics(receiver['name'], receiver_metrics)
                except Exception as ex:
                    self.log(runner.name, 'Exception while distributing metrics', ex, traceback.format_exc())
        return rates

    def _get_cherrypy_mounts(self):
        mounts = []
        cors_enabled = Config.get_entry('cors_enabled', False)
        for runner in self._iter_running_runners():
            mounts.append({'root': runner.get_webservice(self._webinterface),
                           'script_name': '/plugins/{0}'.format(runner.name),
                           'config': {'/': {'tools.sessions.on': False,
                                            'tools.trailing_slash.on': False,
                                            'tools.cors.on': cors_enabled}}})
        return mounts

    def _get_metric_receivers(self):
        receivers = []
        for runner in self._iter_running_runners():
            receivers.extend(runner.get_metric_receivers())
        return receivers

    def _get_metric_definitions(self):
        """ Loads all metric definitions of all plugins """
        definitions = {}
        for runner in self._iter_running_runners():
            definitions[runner.name] = runner.get_metric_definitions()
        return definitions

    def log(self, plugin, msg, exception, stacktrace=None):
        """ Append an exception to the log for the plugins. This log can be retrieved using get_logs. """
        logs = self._logs.setdefault(plugin, [])
        logger.error('Plugin {0}: {1} ({2})'.format(plugin, msg, exception))
        if stacktrace is None:
            logs.append('{0} - {1}: {2}'.format(datetime.now(), msg, exception))
        else:
            logs.append('{0} - {1}: {2}\n{3}'.format(datetime.now(), msg, exception, stacktrace))
        if len(logs) > 100:
            logs.pop(0)

    def get_logger(self, plugin_name):
        """ Get a logger for a plugin. """
        logs = self._logs.setdefault(plugin_name, [])

        def log(msg):
            """ Log function for the given plugin."""
            logs.append('{0} - {1}'.format(datetime.now(), msg))
            if len(logs) > 100:
                logs.pop(0)

        return log

    def get_logs(self):
        """ Get the logs for all plugins. Returns a dict where the keys are the plugin names and the value is a string. """
        return dict((plugin, '\n'.join(entries)) for plugin, entries in six.iteritems(self._logs))
