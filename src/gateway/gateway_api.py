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
"""
The GatewayApi defines high level functions, these are used by the interface
and call the master_api to complete the actions.
"""

from __future__ import absolute_import

import glob
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading

from six.moves.configparser import ConfigParser

import constants
from bus.om_bus_events import OMBusEvents
from gateway.hal.master_controller import MasterController
from ioc import INJECTED, Inject, Injectable, Singleton
from platform_utils import System
from power import power_api
from power.power_api import RealtimePower
from serial_utils import CommunicationTimedOutException

if False:  # MYPY:
    from typing import Any, Dict, List, Optional, Tuple, TypeVar, Union
    from power.power_communicator import PowerCommunicator
    from power.power_store import PowerStore
    from power.power_controller import PowerController, P1Controller
    from bus.om_bus_client import MessageClient
    from gateway.config import ConfigurationController
    from gateway.observer import Observer
    from gateway.watchdog import Watchdog

    T = TypeVar('T', bound=Union[int, float])

logger = logging.getLogger('openmotics')


def convert_nan(number, default):  # type: (T, Optional[T]) -> Optional[T]
    """ Convert nan to a default value """
    if math.isnan(number):
        logger.warning('Got an unexpected NaN')
    return default if math.isnan(number) else number


def check_basic_action(ret_dict):
    """ Checks if the response is 'OK', throws a ValueError otherwise. """
    if ret_dict['resp'] != 'OK':
        raise ValueError('Basic action did not return OK.')


@Injectable.named('gateway_api')
@Singleton
class GatewayApi(object):
    """ The GatewayApi combines master_api functions into high level functions. """

    @Inject
    def __init__(self,
                 master_controller=INJECTED, power_store=INJECTED, power_communicator=INJECTED,
                 power_controller=INJECTED, p1_controller=INJECTED, message_client=INJECTED,
                 observer=INJECTED, configuration_controller=INJECTED):
        # type: (MasterController, PowerStore, PowerCommunicator, PowerController, P1Controller, MessageClient, Observer, ConfigurationController) -> None
        self.__master_controller = master_controller  # type: MasterController
        self.__config_controller = configuration_controller
        self.__power_store = power_store
        self.__power_communicator = power_communicator
        self.__p1_controller = p1_controller
        self.__power_controller = power_controller
        self.__message_client = message_client
        self.__observer = observer

    def set_plugin_controller(self, plugin_controller):
        """ Set the plugin controller. """
        self.__master_controller.set_plugin_controller(plugin_controller)

    def sync_master_time(self):
        # type: () -> None
        """ Set the time on the master. """
        self.__master_controller.sync_time()

    def set_timezone(self, timezone):
        _ = self  # Not static for consistency
        timezone_file_path = '/usr/share/zoneinfo/' + timezone
        if not os.path.isfile(timezone_file_path):
            raise RuntimeError('Could not find timezone \'' + timezone + '\'')
        if os.path.exists(constants.get_timezone_file()):
            os.remove(constants.get_timezone_file())
        os.symlink(timezone_file_path, constants.get_timezone_file())

    def get_timezone(self):
        try:
            path = os.path.realpath(constants.get_timezone_file())
            if not path.startswith('/usr/share/zoneinfo/'):
                # Reset timezone to default setting
                self.set_timezone('UTC')
                return 'UTC'
            return path[20:]
        except Exception:
            return 'UTC'

    def maintenance_mode_stopped(self):
        # type: () -> None
        """ Called when maintenance mode is stopped """
        self.__master_controller.invalidate_caches()
        if self.__message_client is not None:
            self.__message_client.send_event(OMBusEvents.DIRTY_EEPROM, None)

    def get_status(self):
        # TODO: implement gateway status too (e.g. plugin status)
        return self.__master_controller.get_status()

    def get_master_online(self):
        # type: () -> bool
        return self.__master_controller.get_master_online()

    def get_master_version(self):
        return self.__master_controller.get_firmware_version()

    def get_main_version(self):
        """ Gets reported main version """
        _ = self
        config = ConfigParser()
        config.read(constants.get_config_file())
        return str(config.get('OpenMotics', 'version'))

    def reset_master(self):
        # type: () -> Dict[str,Any]
        self.__master_controller.cold_reset()
        return {}

    def update_master_firmware(self, hex_filename):
        self.__master_controller.update_master(hex_filename)

    def update_slave_firmware(self, module_type, hex_filename):
        self.__master_controller.update_slave_modules(module_type, hex_filename)

    # Master module functions

    def module_discover_start(self, timeout=900):  # type: (int) -> None
        """ Start the module discover mode on the master. """
        self.__master_controller.module_discover_start(timeout)

    def module_discover_stop(self):  # type: () -> None
        """ Stop the module discover mode on the master. """
        self.__master_controller.module_discover_stop()
        if self.__message_client is not None:
            self.__message_client.send_event(OMBusEvents.DIRTY_EEPROM, None)

    def module_discover_status(self):  # type: () -> bool
        """ Gets the status of the module discover mode on the master. """
        return self.__master_controller.module_discover_status()

    def get_module_log(self):  # type: () -> List[Dict[str, Any]]
        """
        Get the log statements from the module discovery mode. This returns the current log
        messages and clear the log messages.
        """
        return self.__master_controller.get_module_log()

    def get_modules(self):
        # TODO: do we want to include non-master managed "modules" ? e.g. plugin outputs
        return self.__master_controller.get_modules()

    def flash_leds(self, led_type, led_id):
        return self.__master_controller.flash_leds(led_type, led_id)

    # Output functions

    def set_output_status(self, output_id, is_on, dimmer=None, timer=None):  # type: (int, bool, Optional[int], Optional[int]) -> None
        """
        Set the status, dimmer and timer of an output.
        :param output_id: The id of the output to set
        :param is_on: Whether the output should be on
        :param dimmer: The dimmer value to set, None if unchanged
        :param timer: The timer value to set, None if unchanged
        """
        # TODO: work with output controller
        # TODO: implement output controller and let her handle routing to either master or e.g. plugin based outputs
        self.__master_controller.set_output(output_id=output_id, state=is_on, dimmer=dimmer, timer=timer)

    def set_all_lights_off(self):  # type: () -> None
        # TODO: work with output controller
        # TODO: also switch other lights (e.g. from plugins)
        return self.__master_controller.set_all_lights_off()

    def set_all_lights_floor_off(self, floor):  # type: (int) -> None
        # TODO: work with output controller
        # TODO: also switch other lights (e.g. from plugins)
        return self.__master_controller.set_all_lights_floor_off(floor)

    def set_all_lights_floor_on(self, floor):  # type: (int) -> None
        # TODO: work with output controller
        # TODO: also switch other lights (e.g. from plugins)
        return self.__master_controller.set_all_lights_floor_on(floor)

    # Input functions

    def get_input_status(self):
        """
        Get a list containing the status of the Inputs.
        :returns: A list is a dicts containing the following keys: id, status.
        """
        # TODO: work with input controller
        inputs = self.__observer.get_inputs()
        return [{'id': input_port['id'], 'status': input_port['status']} for input_port in inputs]

    def get_last_inputs(self):
        """ Get the X last pressed inputs during the last Y seconds.
        :returns: a list of tuples (input, output).
        """
        # TODO: work with input controller
        return self.__observer.get_recent()

    # Sensors

    def get_sensors_temperature_status(self):
        """ Get the current temperature of all sensors.

        :returns: list with 32 temperatures, 1 for each sensor. None/null if not connected
        """
        # TODO: work with sensor controller
        # TODO: add other sensors too (e.g. from database <-- plugins)
        values = self.__master_controller.get_sensors_temperature()[:32]
        if len(values) < 32:
            values += [None] * (32 - len(values))
        return values

    def get_sensor_temperature_status(self, sensor_id):
        """ Get the current temperature of all sensors. """
        # TODO: work with sensor controller
        # TODO: add other sensors too (e.g. from database <-- plugins)
        return self.__master_controller.get_sensor_temperature(sensor_id)

    def get_sensors_humidity_status(self):
        """ Get the current humidity of all sensors. """
        # TODO: work with sensor controller
        # TODO: add other sensors too (e.g. from database <-- plugins)
        values = self.__master_controller.get_sensors_humidity()[:32]
        if len(values) < 32:
            values += [None] * (32 - len(values))
        return values

    def get_sensors_brightness_status(self):
        """ Get the current brightness of all sensors. """
        # TODO: work with sensor controller
        # TODO: add other sensors too (e.g. from database <-- plugins)
        values = self.__master_controller.get_sensors_brightness()[:32]
        if len(values) < 32:
            values += [None] * (32 - len(values))
        return values

    def add_virtual_output_module(self):
        # type: () -> str
        # TODO: work with output controller
        return self.__master_controller.add_virtual_output_module()

    def add_virtual_dim_module(self):
        # type: () -> str
        # TODO: work with output controller
        return self.__master_controller.add_virtual_dim_module()

    def add_virtual_input_module(self):
        # type: () -> str
        # TODO: work with input controller
        return self.__master_controller.add_virtual_input_module()

    def set_virtual_sensor(self, sensor_id, temperature, humidity, brightness):
        # TODO: work with sensor controller
        # TODO: add other sensors too (e.g. from database <-- plugins)
        """ Set the temperature, humidity and brightness value of a virtual sensor. """
        return self.__master_controller.set_virtual_sensor(sensor_id, temperature, humidity, brightness)

    # Basic and group actions

    def do_basic_action(self, action_type, action_number):
        return self.__master_controller.do_basic_action(action_type, action_number)

    # Backup and restore functions

    def get_full_backup(self):
        """
        Get a backup (tar) of the master eeprom, the sqlite databases and the plugins

        :returns: Tar containing multiple files: master.eep, config.db, scheduled.db, power.db,
        eeprom_extensions.db, metrics.db and plugins as a string of bytes.
        """
        _ = self  # Not static for consistency

        def backup_sqlite_db(input_db_path, backup_db_path):
            """ Backup an sqlite db provided the path to the db to backup and the backup db. """
            # Connect to database
            connection = sqlite3.connect(input_db_path)
            cursor = connection.cursor()

            # Lock database before making a backup
            cursor.execute('begin immediate')

            # Make new backup file
            shutil.copyfile(input_db_path, backup_db_path)

            # Unlock database
            connection.rollback()

        tmp_dir = tempfile.mkdtemp()
        tmp_sqlite_dir = '{0}/sqlite'.format(tmp_dir)
        os.mkdir(tmp_sqlite_dir)

        try:
            with open('{0}/master.eep'.format(tmp_sqlite_dir), 'w') as eeprom_file:
                eeprom_file.write(self.get_master_backup())

            for filename, source in {'config.db': constants.get_config_database_file(),
                                     'scheduled.db': constants.get_scheduling_database_file(),
                                     'power.db': constants.get_power_database_file(),
                                     'eeprom_extensions.db': constants.get_eeprom_extension_database_file(),
                                     'metrics.db': constants.get_metrics_database_file(),
                                     'gateway.db': constants.get_gateway_database_file()}.items():
                if os.path.exists(source):
                    target = '{0}/{1}'.format(tmp_sqlite_dir, filename)
                    backup_sqlite_db(source, target)

            # Backup plugins
            tmp_plugin_dir = '{0}/{1}'.format(tmp_dir, 'plugins')
            tmp_plugin_content_dir = '{0}/{1}'.format(tmp_plugin_dir, 'content')
            tmp_plugin_config_dir = '{0}/{1}'.format(tmp_plugin_dir, 'config')
            os.mkdir(tmp_plugin_dir)
            os.mkdir(tmp_plugin_content_dir)
            os.mkdir(tmp_plugin_config_dir)

            plugin_dir = constants.get_plugin_dir()
            plugins = [name for name in os.listdir(plugin_dir) if os.path.isdir(os.path.join(plugin_dir, name))]
            for plugin in plugins:
                shutil.copytree(plugin_dir + plugin, '{0}/{1}/'.format(tmp_plugin_content_dir, plugin))

            config_files = constants.get_plugin_configfiles()
            for config_file in glob.glob(config_files):
                shutil.copy(config_file, '{0}/'.format(tmp_plugin_config_dir))

            retcode = subprocess.call('cd {0}; tar cf backup.tar *'.format(tmp_dir), shell=True)
            if retcode != 0:
                raise Exception('The backup tar could not be created.')

            with open('{0}/backup.tar'.format(tmp_dir), 'r') as backup_file:
                return backup_file.read()

        finally:
            shutil.rmtree(tmp_dir)

    def restore_full_backup(self, data):
        """
        Restore a full backup containing the master eeprom and the sqlite databases.

        :param data: The backup to restore.
        :type data: Tar containing multiple files: master.eep, config.db, scheduled.db, power.db,
        eeprom_extensions.db, metrics.db and plugins as a string of bytes.
        :returns: dict with 'output' key.
        """
        import glob
        import shutil
        import tempfile
        import subprocess

        tmp_dir = tempfile.mkdtemp()
        tmp_sqlite_dir = '{0}/sqlite'.format(tmp_dir)
        try:
            with open('{0}/backup.tar'.format(tmp_dir), 'wb') as backup_file:
                backup_file.write(data)

            retcode = subprocess.call('cd {0}; tar xf backup.tar'.format(tmp_dir), shell=True)
            if retcode != 0:
                raise Exception('The backup tar could not be extracted.')

            # Check if the sqlite db's are in a folder or not for backwards compatibility
            src_dir = tmp_sqlite_dir if os.path.isdir(tmp_sqlite_dir) else tmp_dir

            with open('{0}/master.eep'.format(src_dir), 'r') as eeprom_file:
                eeprom_content = eeprom_file.read()
                self.master_restore(eeprom_content)

            for filename, target in {'config.db': constants.get_config_database_file(),
                                     'users.db': constants.get_config_database_file(),
                                     'scheduled.db': constants.get_scheduling_database_file(),
                                     'power.db': constants.get_power_database_file(),
                                     'eeprom_extensions.db': constants.get_eeprom_extension_database_file(),
                                     'metrics.db': constants.get_metrics_database_file(),
                                     'gateway.db': constants.get_gateway_database_file()}.items():
                source = '{0}/{1}'.format(src_dir, filename)
                if os.path.exists(source):
                    shutil.copyfile(source, target)

            # Restore the plugins if there are any
            backup_plugin_dir = '{0}/plugins'.format(tmp_dir)
            backup_plugin_content_dir = '{0}/content'.format(backup_plugin_dir)
            backup_plugin_config_files = '{0}/config/pi_*'.format(backup_plugin_dir)

            if os.path.isdir(backup_plugin_dir):
                plugin_dir = constants.get_plugin_dir()
                plugins = [name for name in os.listdir(backup_plugin_content_dir) if os.path.isdir(os.path.join(backup_plugin_content_dir, name))]
                for plugin in plugins:
                    dest_dir = '{0}{1}'.format(plugin_dir, plugin)
                    if os.path.isdir(dest_dir):
                        shutil.rmtree(dest_dir)
                    shutil.copytree('{0}/{1}/'.format(backup_plugin_content_dir, plugin), '{0}{1}'.format(plugin_dir, plugin))

                config_files = constants.get_plugin_config_dir()
                for config_file in glob.glob(backup_plugin_config_files):
                    shutil.copy(config_file, '{0}/'.format(config_files))

            return {'output': 'Restore complete'}

        finally:
            shutil.rmtree(tmp_dir)
            # Restart the Cherrypy server after 1 second. Lets the current request terminate.
            threading.Timer(1, lambda: os._exit(0)).start()

    def factory_reset(self):
        # type: () -> Dict[str,Any]
        try:
            subprocess.check_output(['python2', 'openmotics_cli.py', 'operator', 'factory-reset'])
        except subprocess.CalledProcessError as exc:
            return {'success': False, 'factory_reset': exc.output.strip()}

        def _restart():
            # type: () -> None
            logger.info('Restarting for factory reset...')
            System.restart_service('openmotics')

        threading.Timer(2, _restart).start()
        return {'factory_reset': 'pending'}

    def get_master_backup(self):
        return self.__master_controller.get_backup()

    def master_restore(self, data):
        return self.__master_controller.restore(data)

    # Error functions

    def master_error_list(self):
        """ Get the error list per module (input and output modules). The modules are identified by
        O1, O2, I1, I2, ...

        :returns: dict with 'errors' key, it contains list of tuples (module, nr_errors).
        """
        return self.__master_controller.error_list()

    def master_last_success(self):
        """ Get the number of seconds since the last successful communication with the master.
        """
        return self.__master_controller.last_success()

    def master_clear_error_list(self):
        return self.__master_controller.clear_error_list()

    def power_last_success(self):
        """ Get the number of seconds since the last successful communication with the power
        modules.
        """
        if self.__power_communicator is None:
            return 0
        return self.__power_communicator.get_seconds_since_last_success()

    # Status led functions

    def set_master_status_leds(self, status):
        self.__master_controller.set_status_leds(status)

    # Inputs

    def get_input_module_type(self, input_module_id):
        """ Gets the module type for a given Input Module ID """
        return self.__master_controller.get_input_module_type(input_module_id)

    # Schedules

    def get_scheduled_action_configuration(self, scheduled_action_id, fields=None):
        # type: (int, Any) -> Dict[str,Any]
        """
        Get a specific scheduled_action_configuration defined by its id.

        :param scheduled_action_id: The id of the scheduled_action_configuration
        :type scheduled_action_id: Id
        :param fields: The field of the scheduled_action_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        """
        return self.__master_controller.load_scheduled_action_configuration(scheduled_action_id, fields=fields)

    def get_scheduled_action_configurations(self, fields=None):
        # type: (Any) -> List[Dict[str,Any]]
        """
        Get all scheduled_action_configurations.

        :param fields: The field of the scheduled_action_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        """
        return self.__master_controller.load_scheduled_action_configurations(fields=fields)

    def set_scheduled_action_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one scheduled_action_configuration.

        :param config: The scheduled_action_configuration to set
        :type config: scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        """
        self.__master_controller.save_scheduled_action_configuration(config)

    def set_scheduled_action_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple scheduled_action_configurations.

        :param config: The list of scheduled_action_configurations to set
        :type config: list of scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        """
        self.__master_controller.save_scheduled_action_configurations(config)

    def get_startup_action_configuration(self, fields=None):
        # type: (Any) -> Dict[str,Any]
        """
        Get the startup_action_configuration.

        :param fields: The field of the startup_action_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: startup_action_configuration dict: contains 'actions' (Actions[100])
        """
        return self.__master_controller.load_startup_action_configuration(fields=fields)

    def set_startup_action_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set the startup_action_configuration.

        :param config: The startup_action_configuration to set
        :type config: startup_action_configuration dict: contains 'actions' (Actions[100])
        """
        self.__master_controller.save_startup_action_configuration(config)

    def get_dimmer_configuration(self, fields=None):
        # type: (Any) -> Dict[str,Any]
        """
        Get the dimmer_configuration.

        :param fields: The field of the dimmer_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: dimmer_configuration dict: contains 'dim_memory' (Byte), 'dim_step' (Byte), 'dim_wait_cycle' (Byte), 'min_dim_level' (Byte)
        """
        return self.__master_controller.load_dimmer_configuration(fields=fields)

    def set_dimmer_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set the dimmer_configuration.

        :param config: The dimmer_configuration to set
        :type config: dimmer_configuration dict: contains 'dim_memory' (Byte), 'dim_step' (Byte), 'dim_wait_cycle' (Byte), 'min_dim_level' (Byte)
        """
        self.__master_controller.save_dimmer_configuration(config)

    def get_can_led_configuration(self, can_led_id, fields=None):
        # type: (int, Any) -> Dict[str,Any]
        """
        Get a specific can_led_configuration defined by its id.

        :param can_led_id: The id of the can_led_configuration
        :type can_led_id: Id
        :param fields: The field of the can_led_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        """
        return self.__master_controller.load_can_led_configuration(can_led_id, fields=fields)

    def get_can_led_configurations(self, fields=None):
        # type: (Any) -> List[Dict[str,Any]]
        """
        Get all can_led_configurations.

        :param fields: The field of the can_led_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        """
        return self.__master_controller.load_can_led_configurations(fields=fields)

    def set_can_led_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one can_led_configuration.

        :param config: The can_led_configuration to set
        :type config: can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        """
        self.__master_controller.save_can_led_configuration(config)

    def set_can_led_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple can_led_configurations.

        :param config: The list of can_led_configurations to set
        :type config: list of can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        """
        self.__master_controller.save_can_led_configurations(config)

    # End of auto generated functions

    def get_configuration_dirty_flag(self):
        # type: () -> bool
        return self.__master_controller.get_configuration_dirty_flag()

    @Inject
    def set_self_recovery(self, active, watchdog=INJECTED):  # type: (bool, Watchdog) -> None
        if active:
            watchdog.start()
        else:
            watchdog.stop()

    # Power functions

    def get_power_modules(self):
        # type: () -> List[Dict[str,Any]]
        """ Get information on the power modules.

        :returns: List of dict depending on the version of the power module. All versions \
        contain 'id', 'name', 'input0', 'input1', 'input2', 'input3', 'input4', 'input5', \
        'input6', 'input7', 'times0', 'times1', 'times2', 'times3', 'times4', 'times5', 'times6', \
        'times7'. For the 8-port power it also contains 'sensor0', 'sensor1', 'sensor2', \
        'sensor3', 'sensor4', 'sensor5', 'sensor6', 'sensor7'. For the 12-port power module also \
        contains 'input8', 'input9', 'input10', 'input11', 'times8', 'times9', 'times10', \
        'times11'.
        """
        if self.__power_store is None:
            return []

        modules = self.__power_store.get_power_modules().values()

        def translate_address(_module):
            """ Translate the address from an integer to the external address format (eg. E1). """
            if _module['version'] == power_api.P1_CONCENTRATOR:
                module_type = 'C'
            else:
                module_type = 'E'
            _module['address'] = '{0}{1}'.format(module_type, _module['address'])
            return _module

        return [translate_address(mod) for mod in modules]

    def set_power_modules(self, modules):
        """ Set information for the power modules.

        :param modules: list of dict depending on the version of the power module. All versions \
        contain 'id', 'name', 'input0', 'input1', 'input2', 'input3', 'input4', 'input5', \
        'input6', 'input7', 'times0', 'times1', 'times2', 'times3', 'times4', 'times5', 'times6', \
        'times7'. For the 8-port power it also contains 'sensor0', 'sensor1', 'sensor2', \
        'sensor3', 'sensor4', 'sensor5', 'sensor6', 'sensor7'. For the 12-port power module also \
        contains 'input8', 'input9', 'input10', 'input11', 'times8', 'times9', 'times10', \
        'times11'.
        :returns: empty dict.
        """
        if self.__power_communicator is None or self.__power_store is None:
            return {}

        for mod in modules:
            self.__power_store.update_power_module(mod)

            version = self.__power_store.get_version(mod['id'])
            addr = self.__power_store.get_address(mod['id'])
            if version == power_api.P1_CONCENTRATOR:
                continue  # TODO: Should raise an exception once the frontends know about the P1C
            elif version == power_api.POWER_MODULE:
                def _check_sid(key):
                    # 2 = 25A, 3 = 50A
                    if mod[key] in [2, 3]:
                        return mod[key]
                    return 2
                self.__power_communicator.do_command(
                    addr, power_api.set_sensor_types(version),
                    *[_check_sid('sensor{0}'.format(i)) for i in range(power_api.NUM_PORTS[version])]
                )
            elif version == power_api.ENERGY_MODULE:
                def _convert_ccf(key):
                    try:
                        if mod[key] == 2:  # 12.5 A
                            return 0.5
                        if mod[key] in [3, 4, 5, 6]:  # 25 A, 50 A, 100 A, 200 A
                            return int(math.pow(2, mod[key] - 3))
                        return mod[key] / 25.0
                    except Exception:
                        # In case of calculation errors, default to 12.5 A
                        return 0.5
                self.__power_communicator.do_command(
                    addr, power_api.set_current_clamp_factor(version),
                    *[_convert_ccf('sensor{0}'.format(i)) for i in range(power_api.NUM_PORTS[version])]
                )

                def _convert_sci(key):
                    if key not in mod:
                        return 0
                    return 1 if mod[key] in [True, 1] else 0
                self.__power_communicator.do_command(
                    addr, power_api.set_current_inverse(version),
                    *[_convert_sci('inverted{0}'.format(i)) for i in range(power_api.NUM_PORTS[version])]
                )
            else:
                raise ValueError('Unknown power api version')

        return dict()

    def get_realtime_power(self):
        # type: () -> Dict[str,List[RealtimePower]]
        """
        Get the realtime power measurement values.
        """
        output = {}
        if self.__power_store is None or self.__power_controller is None:
            return output

        modules = self.__power_store.get_power_modules()
        for module_id in sorted(modules.keys()):
            try:
                version = modules[module_id]['version']
                num_ports = power_api.NUM_PORTS[version]

                volt = [0.0] * num_ports  # TODO: Initialse to None is supported upstream
                freq = [0.0] * num_ports
                current = [0.0] * num_ports
                power = [0.0] * num_ports
                if version in [power_api.POWER_MODULE, power_api.ENERGY_MODULE]:
                    if version == power_api.POWER_MODULE:
                        raw_volt = self.__power_controller.get_module_voltage(modules[module_id])
                        raw_freq = self.__power_controller.get_module_frequency(modules[module_id])

                        volt = [raw_volt[0]] * num_ports
                        freq = [raw_freq[0]] * num_ports
                    else:
                        volt = list(self.__power_controller.get_module_voltage(modules[module_id]))
                        freq = list(self.__power_controller.get_module_frequency(modules[module_id]))

                    current = list(self.__power_controller.get_module_current(modules[module_id]))
                    power = list(self.__power_controller.get_module_power(modules[module_id]))
                elif version == power_api.P1_CONCENTRATOR:
                    statuses = list(self.__p1_controller.get_module_status(modules[module_id]))
                    voltages = list(self.__p1_controller.get_module_voltage(modules[module_id]))
                    currents = list(self.__p1_controller.get_module_current(modules[module_id]))
                    delivered_power = list(self.__p1_controller.get_module_delivered_power(modules[module_id]))
                    received_power = list(self.__p1_controller.get_module_received_power(modules[module_id]))
                    for port, status in enumerate(statuses):
                        try:
                            if status:
                                volt[port] = voltages[port]['phase1'] or 0.0
                                power[port] = ((delivered_power[port] or 0.0) - (received_power[port] or 0.0)) * 1000
                                current[port] = sum(x for x in currents[port].values() if x is not None)
                        except ValueError:
                            pass
                else:
                    raise ValueError('Unknown power api version')

                out = []
                for i in range(num_ports):
                    out.append(RealtimePower(voltage=convert_nan(volt[i], default=0.0),
                                             frequency=convert_nan(freq[i], default=0.0),
                                             current=convert_nan(current[i], default=0.0),
                                             power=convert_nan(power[i], default=0.0)))

                output[str(module_id)] = out
            except CommunicationTimedOutException:
                logger.error('Communication timeout while fetching realtime power from {0}: CommunicationTimedOutException'.format(module_id))
            except Exception as ex:
                logger.exception('Got exception while fetching realtime power from {0}: {1}'.format(module_id, ex))

        return output

    # TODO: rework get_realtime_power or call this there.
    def get_realtime_p1(self):
        # type: () -> List[Dict[str,Any]]
        """
        Get the realtime p1 measurement values.
        """
        if self.__power_store is None or self.__p1_controller is None:
            return []

        modules = self.__power_store.get_power_modules()
        return self.__p1_controller.get_realtime(modules)

    def get_total_energy(self):
        # type: () -> Dict[str,List[List[Optional[int]]]]
        """ Get the total energy (kWh) consumed by the power modules.

        :returns: dict with the module id as key and the following array as value: [day, night].
        """
        output = {}
        if self.__power_controller is None:
            return output

        modules = self.__power_store.get_power_modules()
        for module_id in sorted(modules.keys()):
            try:
                version = modules[module_id]['version']
                num_ports = power_api.NUM_PORTS[version]

                day = [None] * num_ports  # type: List[Optional[int]]
                night = [None] * num_ports  # type: List[Optional[int]]
                if version in [power_api.ENERGY_MODULE, power_api.POWER_MODULE]:
                    day = [convert_nan(entry, default=None)
                           for entry in self.__power_controller.get_module_day_energy(modules[module_id])]
                    night = [convert_nan(entry, default=None)
                             for entry in self.__power_controller.get_module_night_energy(modules[module_id])]
                elif version == power_api.P1_CONCENTRATOR:
                    statuses = self.__p1_controller.get_module_status(modules[module_id])
                    days = self.__p1_controller.get_module_day_energy(modules[module_id])
                    nights = self.__p1_controller.get_module_night_energy(modules[module_id])
                    for port, status in enumerate(statuses):
                        try:
                            if status:
                                day[port] = int((days[port] or 0.0) * 1000)
                                night[port] = int((nights[port] or 0.0) * 1000)
                        except ValueError:
                            pass
                else:
                    raise ValueError('Unknown power api version')

                out = []
                for i in range(num_ports):
                    out.append([day[i], night[i]])

                output[str(module_id)] = out
            except CommunicationTimedOutException:
                logger.error('Communication timeout while fetching total energy from {0}: CommunicationTimedOutException'.format(module_id))
            except Exception as ex:
                logger.exception('Got exception while fetching total energy from {0}: {1}'.format(module_id, ex))

        return output

    def start_power_address_mode(self):
        """ Start the address mode on the power modules.

        :returns: empty dict.
        """
        if self.__power_communicator is not None:
            self.__power_communicator.start_address_mode()
        return {}

    def stop_power_address_mode(self):
        """ Stop the address mode on the power modules.

        :returns: empty dict
        """
        if self.__power_communicator is not None:
            self.__power_communicator.stop_address_mode()
        return dict()

    def in_power_address_mode(self):
        """ Check if the power modules are in address mode

        :returns: dict with key 'address_mode' and value True or False.
        """
        in_address_mode = False
        if self.__power_communicator is not None:
            in_address_mode = self.__power_communicator.in_address_mode()
        return {'address_mode': in_address_mode}

    def set_power_voltage(self, module_id, voltage):
        """ Set the voltage for a given module.

        :param module_id: The id of the power module.
        :param voltage: The voltage to set for the power module.
        :returns: empty dict
        """
        if self.__power_communicator is None or self.__power_controller is None:
            return {}

        addr = self.__power_store.get_address(module_id)
        version = self.__power_store.get_version(module_id)
        if version != power_api.ENERGY_MODULE:
            raise ValueError('Unknown power api version')
        self.__power_communicator.do_command(addr, power_api.set_voltage(), voltage)
        return dict()

    def get_energy_time(self, module_id, input_id=None):
        """ Get a 'time' sample of voltage and current

        :returns: dict with input_id and the voltage and cucrrent time samples
        """
        if self.__power_communicator is None or self.__power_controller is None:
            return {}

        addr = self.__power_store.get_address(module_id)
        version = self.__power_store.get_version(module_id)
        if version != power_api.ENERGY_MODULE:
            raise ValueError('Unknown power api version')
        if input_id is None:
            input_ids = list(range(12))
        else:
            input_id = int(input_id)
            if input_id < 0 or input_id > 11:
                raise ValueError('Invalid input_id (should be 0-11)')
            input_ids = [input_id]
        data = {}
        for input_id in input_ids:
            voltage = list(self.__power_communicator.do_command(addr, power_api.get_voltage_sample_time(version), input_id, 0))
            current = list(self.__power_communicator.do_command(addr, power_api.get_current_sample_time(version), input_id, 0))
            for entry in self.__power_communicator.do_command(addr, power_api.get_voltage_sample_time(version), input_id, 1):
                if entry == float('inf'):
                    break
                voltage.append(entry)
            for entry in self.__power_communicator.do_command(addr, power_api.get_current_sample_time(version), input_id, 1):
                if entry == float('inf'):
                    break
                current.append(entry)
            data[str(input_id)] = {'voltage': voltage,
                                   'current': current}
        return data

    def get_energy_frequency(self, module_id, input_id=None):
        """ Get a 'frequency' sample of voltage and current

        :returns: dict with input_id and the voltage and cucrrent frequency samples
        """
        if self.__power_communicator is None or self.__power_controller is None:
            return {}

        addr = self.__power_store.get_address(module_id)
        version = self.__power_store.get_version(module_id)
        if version != power_api.ENERGY_MODULE:
            raise ValueError('Unknown power api version')
        if input_id is None:
            input_ids = list(range(12))
        else:
            input_id = int(input_id)
            if input_id < 0 or input_id > 11:
                raise ValueError('Invalid input_id (should be 0-11)')
            input_ids = [input_id]
        data = {}
        for input_id in input_ids:
            voltage = self.__power_communicator.do_command(addr, power_api.get_voltage_sample_frequency(version), input_id, 20)
            current = self.__power_communicator.do_command(addr, power_api.get_current_sample_frequency(version), input_id, 20)
            # The received data has a length of 40; 20 harmonics entries, and 20 phase entries. For easier usage, the
            # API calls splits them into two parts so the customers doesn't have to do the splitting.
            data[str(input_id)] = {'voltage': [voltage[:20], voltage[20:]],
                                   'current': [current[:20], current[20:]]}
        return data

    def do_raw_energy_command(self, address, mode, command, data):
        """ Perform a raw energy module command, for debugging purposes.

        :param address: The address of the energy module
        :param mode: 1 char: S or G
        :param command: 3 char power command
        :param data: list of bytes
        :returns: list of bytes
        """
        if self.__power_communicator is None:
            return []

        return self.__power_communicator.do_command(address,
                                                    power_api.raw_command(mode, command, len(data)),
                                                    *data)
