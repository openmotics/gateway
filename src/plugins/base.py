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

import logging
import os
import pkgutil
import traceback
from gateway.observer import Event
from datetime import datetime
from ioc import Injectable, Inject, INJECTED, Singleton
from plugins.runner import PluginRunner
from models import Plugin

logger = logging.getLogger("openmotics")


@Injectable.named('plugin_controller')
@Singleton
class PluginController(object):
    """ The controller keeps track of all plugins in the system. """

    @Inject
    def __init__(self,
                 web_interface=INJECTED, configuration_controller=INJECTED, observer=INJECTED,
                 runtime_path='/opt/openmotics/python/plugin_runtime',
                 plugins_path='/opt/openmotics/python/plugins',
                 plugin_config_path='/opt/openmotics/etc'):
        """
        :type observer: gateway.observer.Observer
        """
        self.__webinterface = web_interface
        self.__config_controller = configuration_controller
        self.__runtime_path = runtime_path
        self.__plugins_path = plugins_path
        self.__plugin_config_path = plugin_config_path
        self.__observer = observer

        self.__stopped = True
        self.__logs = {}
        self.__runners = {}

        self.__metrics_controller = None
        self.__metrics_collector = None
        self.__web_service = None

    def start(self):
        """ Start the plugins and expose them via the webinterface. """
        if self.__stopped:
            self.__init_runners()
            self.__update_dependencies()
        else:
            logger.error('The PluginController is already running')

    def stop(self):
        for runner_name in self.__runners.keys():
            self.__destroy_plugin_runner(runner_name)
        self.__stopped = True

    def set_metrics_controller(self, metrics_controller):
        """ Sets the metrics controller """
        self.__metrics_controller = metrics_controller

    def set_metrics_collector(self, metrics_collector):
        """ Sets the metrics collector """
        self.__metrics_collector = metrics_collector

    def set_webservice(self, web_service):
        """ Sets the web service """
        self.__web_service = web_service

    def __init_runners(self):
        """ Scan the plugins package for installed plugins in the form of subpackages. """
        objects = pkgutil.iter_modules([self.__plugins_path])  # (module_loader, name, ispkg)
        package_names = [o[1] for o in objects if o[2]]

        self.__runners = {}
        # First initialize all plugin runners, then start them.
        for package_name in package_names:
            self.__init_plugin_runner(package_name)
        for package_name in package_names:
            runner = self.__runners.get(package_name)
            if runner is not None:
                self.__start_plugin_runner(runner, package_name, False)

    def __init_plugin_runner(self, plugin_name):
        """ Initializes a single plugin runner """
        try:
            if plugin_name in self.__runners.keys():
                self.log(plugin_name, '[Runner] Could not init plugin', 'Multiple plugins with the same name found')
                return
            _logger = self.get_logger(plugin_name)
            plugin_path = os.path.join(self.__plugins_path, plugin_name)
            runner = PluginRunner(plugin_name, self.__runtime_path, plugin_path, _logger)
            self.__runners[runner.name] = runner
            return runner
        except Exception as exception:
            self.log(plugin_name, '[Runner] Could not initialize plugin', exception)

    def __start_plugin_runner(self, runner, runner_name, update_dependencies):
        """ Starts a single plugin runner """
        try:
            logger.info('Plugin {0}: {1}'.format(runner_name, 'Starting...'))
            runner.start()
            if update_dependencies:
                self.__update_dependencies()
            logger.info('Plugin {0}: {1}'.format(runner_name, 'Starting... Done'))
        except Exception as exception:
            try:
                runner.stop()
            except Exception:
                pass  # Try as best as possible to stop the plugin
            self.log(runner.name, '[Runner] Could not start plugin', exception)

    def start_plugin(self, plugin_name):
        """ Request to start a runner """
        runner = self.__runners.get(plugin_name)
        if runner is None:
            return False
        if not runner.is_running():
            self.__start_plugin_runner(runner, plugin_name, True)
        return runner.is_running()

    def __stop_plugin_runner(self, runner_name, update_dependencies):
        """ Stops a single plugin runner """
        runner = self.__runners.get(runner_name)
        if runner is None:
            return
        try:
            logger.info('Plugin {0}: {1}'.format(runner.name, 'Stopping...'))
            runner.stop()
            if update_dependencies:
                self.__update_dependencies()
            logger.info('Plugin {0}: {1}'.format(runner.name, 'Stopping... Done'))
        except Exception as exception:
            self.log(runner.name, '[Runner] Could not stop plugin', exception)

    def stop_plugin(self, plugin_name):
        """ Request to stop a runner """
        runner = self.__runners.get(plugin_name)
        if runner is None:
            return False
        self.__stop_plugin_runner(runner.name, True)
        return runner.is_running()

    def __destroy_plugin_runner(self, runner_name):
        """ Removes a runner """
        self.__stop_plugin_runner(runner_name, False)
        self.__logs.pop(runner_name, None)
        self.__runners.pop(runner_name, None)

    def __update_dependencies(self):
        """ When a runner is added/removed, this call updates all code that needs to know about plugins """
        if self.__webinterface is not None and self.__web_service is not None:
            self.__web_service.update_tree(self.__get_cherrypy_mounts())
        if self.__metrics_collector is not None:
            self.__metrics_collector.set_plugin_intervals(self.__get_metric_receivers())
        if self.__metrics_controller is not None:
            self.__metrics_controller.set_plugin_definitions(self.__get_metric_definitions())

    def get_plugins(self):
        """
        Get a list of all installed plugins.

        :rtype: list of plugins.runner.PluginRunner
        """
        return self.__runners.values()

    def __get_plugin(self, name):
        """
        Get a plugin by name, None if it the plugin is not installed.

        :rtype: plugins.runner.PluginRunner
        """
        return self.__runners.get(name)

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
            runner = PluginRunner(None, self.__runtime_path, '{0}/new_package'.format(tmp_dir), _logger)
            runner.start()
            runner.stop()
            name, version = runner.name, runner.version
            self.__logs.pop('new_pacakge', None)

            def parse_version(version_string):
                """ Parse the version from a string "x.y.z" to a tuple(x, y, z). """
                if version_string is None:
                    return 0, 0, 0  # A stopped plugin doesn't announce it's version, so make sure we can update stopped plugins
                return tuple([int(x) for x in version_string.split('.')])

            # Check if a newer version of the package is already installed
            installed_plugin = self.__get_plugin(name)
            if installed_plugin is not None:
                if parse_version(version) <= parse_version(installed_plugin.version):
                    raise Exception('A newer version of plugins {0} is already installed (current version = {1}, to installed = {2}).'.format(name, installed_plugin.version, version))
                else:
                    # Remove the old version of the plugin
                    self.__destroy_plugin_runner(name)
                    retcode = call('cd {0}; rm -R {1}'.format(self.__plugins_path, name),
                                   shell=True)
                    if retcode != 0:
                        raise Exception('The old version of the plugin could not be removed.')

            # Check if the package directory exists, this can only be the case if a previous
            # install failed or if the plugin has gone corrupt: remove it !
            plugin_path = '{0}/{1}'.format(self.__plugins_path, name)
            if os.path.exists(plugin_path):
                rmtree(plugin_path)

            # Install the package
            retcode = call('cd {0}; mv new_package {1}'.format(tmp_dir, plugin_path), shell=True)
            if retcode != 0:
                raise Exception('The package could not be installed.')

            runner = self.__init_plugin_runner(name)
            if runner is None:
                raise Exception('Could not initialize plugin.')
            self.__start_plugin_runner(runner, name, True)
            self.__update_dependencies()

            # save/update plugin in the database
            plugin = Plugin.get_or_create(name=name)
            plugin.version = version
            plugin.save()

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

        plugin = self.__get_plugin(name)

        # Check if the plugin in installed
        if plugin is None:
            raise Exception('Plugin \'{0}\' is not installed.'.format(name))

        # Execute the on_remove callbacks
        try:
            plugin.remove_callback()
        except Exception as exception:
            logger.error('Exception while removing plugin \'{0}\': {1}'.format(name, exception))

        # Stop the plugin process
        self.__destroy_plugin_runner(name)
        self.__update_dependencies()

        # Remove the plugin package
        plugin_path = '{0}/{1}'.format(self.__plugins_path, name)
        try:
            rmtree(plugin_path)
        except Exception as exception:
            raise Exception('Error while removing package for plugin \'{0}\': {1}'.format(name, exception))

        # Remove the plugin configuration
        conf_file = '{0}/pi_{1}.conf'.format(self.__plugin_config_path, name)
        if os.path.exists(conf_file):
            os.remove(conf_file)

        # delete plugin in the database if existing
        plugin = Plugin.get_or_none(name=name)
        if plugin:
            plugin.delete()

        return {'msg': 'Plugin successfully removed'}

    def __iter_running_runners(self):
        """
        :rtype: list of plugins.runner.PluginRunner
        """
        for runner_name in self.__runners.keys():
            runner = self.__runners.get(runner_name)
            if runner is not None and runner.is_running():
                yield runner

    def process_observer_event(self, event):
        if event.type == Event.Types.INPUT_CHANGE:
            # Should be called when the input status changes, notifies all plugins.
            for runner in self.__iter_running_runners():
                runner.process_input_status(event)
        if event.type == Event.Types.OUTPUT_CHANGE:
            # TODO: Implement versioning so a plugin can also receive "normal" events on version 2
            # Should be called when the output status changes, notifies all plugins.
            states = [(output['id'], output['dimmer']) for output in self.__observer.get_outputs()
                      if output['status'] == 1]
            for runner in self.__iter_running_runners():
                runner.process_output_status(states)
        if event.type == Event.Types.LIGHT_CHANGE:
            # Should be called when the light status changes, notifies all plugins.
            for runner in self.__iter_running_runners():
                runner.process_light_status(event)

    def process_shutter_status(self, shutter_status_inst):
        """ Should be called when the shutter status changes, notifies all plugins. """
        for runner in self.__iter_running_runners():
            runner.process_shutter_status(shutter_status_inst)

    def process_event(self, code):
        """ Should be called when an event is triggered, notifies all plugins. """
        for runner in self.__iter_running_runners():
            runner.process_event(code)

    def _request(self, name, method, args=None, kwargs=None):
        """ Allows to execute a programmatorical http request to the plugin """
        runner = self.__runners.get(name)
        if runner is not None:
            return runner.request(method, args=args, kwargs=kwargs)

    def collect_metrics(self):
        """ Collects all metrics from all plugins """
        for runner in self.__iter_running_runners():
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
        for runner in self.__iter_running_runners():
            for receiver in runner.get_metric_receivers():
                receiver_metrics = []
                try:
                    sources = self.__metrics_controller.get_filter('source', receiver['source'])
                    metric_types = self.__metrics_controller.get_filter('metric_type', receiver['metric_type'])
                    for index, metric in enumerate(metrics):
                        if metric['source'] in sources and metric['type'] in metric_types:
                            receiver_metrics.append(metric)
                            rates[rate_keys[index]] += 1
                            rates['total'] += 1
                    runner.distribute_metrics(receiver['name'], receiver_metrics)
                except Exception as ex:
                    self.log(runner.name, 'Exception while distributing metrics', ex, traceback.format_exc())
        return rates

    def __get_cherrypy_mounts(self):
        mounts = []
        cors_enabled = self.__config_controller.get_setting('cors_enabled', False)
        for runner in self.__iter_running_runners():
            mounts.append({'root': runner.get_webservice(self.__webinterface),
                           'script_name': '/plugins/{0}'.format(runner.name),
                           'config': {'/': {'tools.sessions.on': False,
                                            'tools.trailing_slash.on': False,
                                            'tools.cors.on': cors_enabled}}})
        return mounts

    def __get_metric_receivers(self):
        receivers = []
        for runner in self.__iter_running_runners():
            receivers.extend(runner.get_metric_receivers())
        return receivers

    def __get_metric_definitions(self):
        """ Loads all metric definitions of all plugins """
        definitions = {}
        for runner in self.__iter_running_runners():
            definitions[runner.name] = runner.get_metric_definitions()
        return definitions

    def log(self, plugin, msg, exception, stacktrace=None):
        """ Append an exception to the log for the plugins. This log can be retrieved using get_logs. """
        if plugin not in self.__logs:
            self.__logs[plugin] = []

        logger.error('Plugin {0}: {1} ({2})'.format(plugin, msg, exception))
        if stacktrace is None:
            self.__logs[plugin].append('{0} - {1}: {2}'.format(datetime.now(), msg, exception))
        else:
            self.__logs[plugin].append('{0} - {1}: {2}\n{3}'.format(datetime.now(), msg, exception, stacktrace))
        if len(self.__logs[plugin]) > 100:
            self.__logs[plugin].pop(0)

    def get_logger(self, plugin_name):
        """ Get a logger for a plugin. """
        if plugin_name not in self.__logs:
            self.__logs[plugin_name] = []

        def log(msg):
            """ Log function for the given plugin."""
            self.__logs[plugin_name].append('{0} - {1}'.format(datetime.now(), msg))
            if len(self.__logs[plugin_name]) > 100:
                self.__logs[plugin_name].pop(0)

        return log

    def get_logs(self):
        """ Get the logs for all plugins. Returns a dict where the keys are the plugin names and the value is a string. """
        return dict((plugin, '\n'.join(entries)) for plugin, entries in self.__logs.iteritems())
