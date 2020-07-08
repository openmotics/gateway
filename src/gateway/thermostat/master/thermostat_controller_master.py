# Copyright (C) 2020 OpenMotics BV
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

import logging
import time

from bus.om_bus_events import OMBusEvents
from gateway.daemon_thread import DaemonThread, DaemonThreadWait
from gateway.dto import ThermostatDTO
from gateway.events import GatewayEvent
from gateway.maintenance_communicator import InMaintenanceModeException
from gateway.observer import Observer
from gateway.thermostat.master.thermostat_status_master import \
    ThermostatStatusMaster
from gateway.thermostat.thermostat_controller import ThermostatController
from ioc import INJECTED, Inject, Injectable, Singleton
from master.classic import master_api
from master.classic.eeprom_models import CoolingPumpGroupConfiguration, \
    GlobalRTD10Configuration, GlobalThermostatConfiguration, \
    PumpGroupConfiguration, RTD10CoolingConfiguration, \
    RTD10HeatingConfiguration
from master.classic.master_communicator import CommunicationTimedOutException
from toolbox import Toolbox

if False:  # MYPY
    from typing import Any, List, Dict, Optional, Tuple
    from bus.om_bus_client import MessageClient
    from gateway.dto import OutputStateDTO
    from gateway.hal.master_controller import MasterController
    from gateway.output_controller import OutputController
    from master.classic.eeprom_controller import EepromController
    from master.classic.master_communicator import MasterCommunicator

logger = logging.getLogger("openmotics")


@Injectable.named('thermostat_controller')
@Singleton
class ThermostatControllerMaster(ThermostatController):
    @Inject
    def __init__(self, message_client=INJECTED, output_controller=INJECTED,
                 master_communicator=INJECTED, eeprom_controller=INJECTED,
                 master_controller=INJECTED):
        # type: (Optional[MessageClient], OutputController, MasterCommunicator, EepromController, MasterController) -> None
        super(ThermostatControllerMaster, self).__init__(message_client, output_controller)
        self._master_communicator = master_communicator
        self._eeprom_controller = eeprom_controller
        self._master_controller = master_controller

        self._monitor_thread = DaemonThread(name='ThermostatControllerMaster monitor',
                                            target=self._monitor,
                                            interval=30, delay=10)

        self._thermostat_status = ThermostatStatusMaster(on_thermostat_change=self._thermostat_changed,
                                                         on_thermostat_group_change=self._thermostat_group_changed)
        self._thermostats_original_interval = 30
        self._thermostats_interval = self._thermostats_original_interval
        self._thermostats_last_updated = 0
        self._thermostats_restore = 0
        self._thermostats_config = {}  # type: Dict[int, ThermostatDTO]

    def start(self):
        # type: () -> None
        self._monitor_thread.start()

    def stop(self):
        # type: () -> None
        self._monitor_thread.stop()

    def _thermostat_changed(self, thermostat_id, status):
        """ Executed by the Thermostat Status tracker when an output changed state """
        if self._message_client is not None:
            self._message_client.send_event(OMBusEvents.THERMOSTAT_CHANGE, {'id': thermostat_id})
        location = {'room_id': Toolbox.denonify(self._thermostats_config[thermostat_id].room, 255)}
        for callback in self._event_subscriptions:
            callback(GatewayEvent(event_type=GatewayEvent.Types.THERMOSTAT_CHANGE,
                                  data={'id': thermostat_id,
                                        'status': {'preset': status['preset'],
                                                   'current_setpoint': status['current_setpoint'],
                                                   'actual_temperature': status['actual_temperature'],
                                                   'output_0': status['output_0'],
                                                   'output_1': status['output_1']},
                                        'location': location}))

    def _thermostat_group_changed(self, status):
        if self._message_client is not None:
            self._message_client.send_event(OMBusEvents.THERMOSTAT_CHANGE, {'id': None})
        for callback in self._event_subscriptions:
            callback(GatewayEvent(event_type=GatewayEvent.Types.THERMOSTAT_GROUP_CHANGE,
                                  data={'id': 0,
                                        'status': {'state': status['state'],
                                                   'mode': status['mode']},
                                        'location': {}}))

    @staticmethod
    def check_basic_action(ret_dict):
        """ Checks if the response is 'OK', throws a ValueError otherwise. """
        if ret_dict['resp'] != 'OK':
            raise ValueError('Basic action did not return OK.')

    def increase_interval(self, object_type, interval, window):
        """ Increases a certain interval to a new setting for a given amount of time """
        if object_type == Observer.Types.THERMOSTATS:
            self._thermostats_interval = interval
            self._thermostats_restore = time.time() + window

    def invalidate_cache(self, object_type=None):
        """
        Triggered when an external service knows certain settings might be changed in the background.
        For example: maintenance mode or module discovery
        """
        if object_type is None or object_type == Observer.Types.THERMOSTATS:
            self._thermostats_last_updated = 0

    ################################
    # New API
    ################################

    def get_current_preset(self, thermostat_number):
        raise NotImplementedError()

    def set_current_preset(self, thermostat_number, preset_name):
        raise NotImplementedError()

    def set_current_setpoint(self, thermostat_number, heating_temperature, cooling_temperature):
        raise NotImplementedError()

    ################################
    # Legacy API
    ################################

    def load_heating_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        return self._master_controller.load_heating_thermostat(thermostat_id)

    def load_heating_thermostats(self):  # type: () -> List[ThermostatDTO]
        return self._master_controller.load_heating_thermostats()

    def save_heating_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        self._master_controller.save_heating_thermostats(thermostats)
        self.invalidate_cache(Observer.Types.THERMOSTATS)

    def load_cooling_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        return self._master_controller.load_cooling_thermostat(thermostat_id)

    def load_cooling_thermostats(self):  # type: () -> List[ThermostatDTO]
        return self._master_controller.load_cooling_thermostats()

    def save_cooling_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        self._master_controller.save_cooling_thermostats(thermostats)
        self.invalidate_cache(Observer.Types.THERMOSTATS)

    def v0_get_cooling_pump_group_configuration(self, pump_group_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        """
        Get a specific cooling_pump_group_configuration defined by its id.

        :param pump_group_id: The id of the cooling_pump_group_configuration
        :type pump_group_id: Id
        :param fields: The field of the cooling_pump_group_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        return self._eeprom_controller.read(CoolingPumpGroupConfiguration, pump_group_id, fields).serialize()

    def v0_get_cooling_pump_group_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        """
        Get all cooling_pump_group_configurations.

        :param fields: The field of the cooling_pump_group_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        return [o.serialize() for o in self._eeprom_controller.read_all(CoolingPumpGroupConfiguration, fields)]

    def v0_set_cooling_pump_group_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one cooling_pump_group_configuration.

        :param config: The cooling_pump_group_configuration to set
        :type config: cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        self._eeprom_controller.write(CoolingPumpGroupConfiguration.deserialize(config))

    def v0_set_cooling_pump_group_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple cooling_pump_group_configurations.

        :param config: The list of cooling_pump_group_configurations to set
        :type config: list of cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        self._eeprom_controller.write_batch([CoolingPumpGroupConfiguration.deserialize(o) for o in config])

    def v0_get_global_rtd10_configuration(self, fields=None):
        # type: (Optional[List[str]]) -> Dict[str,Any]
        """
        Get the global_rtd10_configuration.

        :param fields: The field of the global_rtd10_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: global_rtd10_configuration dict: contains 'output_value_cooling_16' (Byte), 'output_value_cooling_16_5' (Byte), 'output_value_cooling_17' (Byte), 'output_value_cooling_17_5' (Byte), 'output_value_cooling_18' (Byte), 'output_value_cooling_18_5' (Byte), 'output_value_cooling_19' (Byte), 'output_value_cooling_19_5' (Byte), 'output_value_cooling_20' (Byte), 'output_value_cooling_20_5' (Byte), 'output_value_cooling_21' (Byte), 'output_value_cooling_21_5' (Byte), 'output_value_cooling_22' (Byte), 'output_value_cooling_22_5' (Byte), 'output_value_cooling_23' (Byte), 'output_value_cooling_23_5' (Byte), 'output_value_cooling_24' (Byte), 'output_value_heating_16' (Byte), 'output_value_heating_16_5' (Byte), 'output_value_heating_17' (Byte), 'output_value_heating_17_5' (Byte), 'output_value_heating_18' (Byte), 'output_value_heating_18_5' (Byte), 'output_value_heating_19' (Byte), 'output_value_heating_19_5' (Byte), 'output_value_heating_20' (Byte), 'output_value_heating_20_5' (Byte), 'output_value_heating_21' (Byte), 'output_value_heating_21_5' (Byte), 'output_value_heating_22' (Byte), 'output_value_heating_22_5' (Byte), 'output_value_heating_23' (Byte), 'output_value_heating_23_5' (Byte), 'output_value_heating_24' (Byte)
        """
        return self._eeprom_controller.read(GlobalRTD10Configuration, fields=fields).serialize()

    def v0_set_global_rtd10_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set the global_rtd10_configuration.

        :param config: The global_rtd10_configuration to set
        :type config: global_rtd10_configuration dict: contains 'output_value_cooling_16' (Byte), 'output_value_cooling_16_5' (Byte), 'output_value_cooling_17' (Byte), 'output_value_cooling_17_5' (Byte), 'output_value_cooling_18' (Byte), 'output_value_cooling_18_5' (Byte), 'output_value_cooling_19' (Byte), 'output_value_cooling_19_5' (Byte), 'output_value_cooling_20' (Byte), 'output_value_cooling_20_5' (Byte), 'output_value_cooling_21' (Byte), 'output_value_cooling_21_5' (Byte), 'output_value_cooling_22' (Byte), 'output_value_cooling_22_5' (Byte), 'output_value_cooling_23' (Byte), 'output_value_cooling_23_5' (Byte), 'output_value_cooling_24' (Byte), 'output_value_heating_16' (Byte), 'output_value_heating_16_5' (Byte), 'output_value_heating_17' (Byte), 'output_value_heating_17_5' (Byte), 'output_value_heating_18' (Byte), 'output_value_heating_18_5' (Byte), 'output_value_heating_19' (Byte), 'output_value_heating_19_5' (Byte), 'output_value_heating_20' (Byte), 'output_value_heating_20_5' (Byte), 'output_value_heating_21' (Byte), 'output_value_heating_21_5' (Byte), 'output_value_heating_22' (Byte), 'output_value_heating_22_5' (Byte), 'output_value_heating_23' (Byte), 'output_value_heating_23_5' (Byte), 'output_value_heating_24' (Byte)
        """
        self._eeprom_controller.write(GlobalRTD10Configuration.deserialize(config))

    def v0_get_rtd10_heating_configuration(self, heating_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        """
        Get a specific rtd10_heating_configuration defined by its id.

        :param heating_id: The id of the rtd10_heating_configuration
        :type heating_id: Id
        :param fields: The field of the rtd10_heating_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        return self._eeprom_controller.read(RTD10HeatingConfiguration, heating_id, fields).serialize()

    def v0_get_rtd10_heating_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        """
        Get all rtd10_heating_configurations.

        :param fields: The field of the rtd10_heating_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        return [o.serialize() for o in self._eeprom_controller.read_all(RTD10HeatingConfiguration, fields)]

    def v0_set_rtd10_heating_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one rtd10_heating_configuration.

        :param config: The rtd10_heating_configuration to set
        :type config: rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        self._eeprom_controller.write(RTD10HeatingConfiguration.deserialize(config))

    def v0_set_rtd10_heating_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple rtd10_heating_configurations.

        :param config: The list of rtd10_heating_configurations to set
        :type config: list of rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        self._eeprom_controller.write_batch([RTD10HeatingConfiguration.deserialize(o) for o in config])

    def v0_get_rtd10_cooling_configuration(self, cooling_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        """
        Get a specific rtd10_cooling_configuration defined by its id.

        :param cooling_id: The id of the rtd10_cooling_configuration
        :type cooling_id: Id
        :param fields: The field of the rtd10_cooling_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        return self._eeprom_controller.read(RTD10CoolingConfiguration, cooling_id, fields).serialize()

    def v0_get_rtd10_cooling_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        """
        Get all rtd10_cooling_configurations.

        :param fields: The field of the rtd10_cooling_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        return [o.serialize() for o in self._eeprom_controller.read_all(RTD10CoolingConfiguration, fields)]

    def v0_set_rtd10_cooling_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one rtd10_cooling_configuration.

        :param config: The rtd10_cooling_configuration to set
        :type config: rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        self._eeprom_controller.write(RTD10CoolingConfiguration.deserialize(config))

    def v0_set_rtd10_cooling_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple rtd10_cooling_configurations.

        :param config: The list of rtd10_cooling_configurations to set
        :type config: list of rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        """
        self._eeprom_controller.write_batch([RTD10CoolingConfiguration.deserialize(o) for o in config])

    def v0_get_global_thermostat_configuration(self, fields=None):
        # type: (Optional[List[str]]) -> Dict[str,Any]
        """
        Get the global_thermostat_configuration.

        :param fields: The field of the global_thermostat_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: global_thermostat_configuration dict: contains 'outside_sensor' (Byte), 'pump_delay' (Byte), 'switch_to_cooling_output_0' (Byte), 'switch_to_cooling_output_1' (Byte), 'switch_to_cooling_output_2' (Byte), 'switch_to_cooling_output_3' (Byte), 'switch_to_cooling_value_0' (Byte), 'switch_to_cooling_value_1' (Byte), 'switch_to_cooling_value_2' (Byte), 'switch_to_cooling_value_3' (Byte), 'switch_to_heating_output_0' (Byte), 'switch_to_heating_output_1' (Byte), 'switch_to_heating_output_2' (Byte), 'switch_to_heating_output_3' (Byte), 'switch_to_heating_value_0' (Byte), 'switch_to_heating_value_1' (Byte), 'switch_to_heating_value_2' (Byte), 'switch_to_heating_value_3' (Byte), 'threshold_temp' (Temp)
        """
        return self._eeprom_controller.read(GlobalThermostatConfiguration, fields=fields).serialize()

    def v0_set_global_thermostat_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set the global_thermostat_configuration.

        :param config: The global_thermostat_configuration to set
        :type config: global_thermostat_configuration dict: contains 'outside_sensor' (Byte), 'pump_delay' (Byte), 'switch_to_cooling_output_0' (Byte), 'switch_to_cooling_output_1' (Byte), 'switch_to_cooling_output_2' (Byte), 'switch_to_cooling_output_3' (Byte), 'switch_to_cooling_value_0' (Byte), 'switch_to_cooling_value_1' (Byte), 'switch_to_cooling_value_2' (Byte), 'switch_to_cooling_value_3' (Byte), 'switch_to_heating_output_0' (Byte), 'switch_to_heating_output_1' (Byte), 'switch_to_heating_output_2' (Byte), 'switch_to_heating_output_3' (Byte), 'switch_to_heating_value_0' (Byte), 'switch_to_heating_value_1' (Byte), 'switch_to_heating_value_2' (Byte), 'switch_to_heating_value_3' (Byte), 'threshold_temp' (Temp)
        """
        if 'outside_sensor' in config:
            if config['outside_sensor'] == 255:
                config['threshold_temp'] = 50  # Works around a master issue where the thermostat would be turned off in case there is no outside sensor.
        self._eeprom_controller.write(GlobalThermostatConfiguration.deserialize(config))
        self.invalidate_cache(Observer.Types.THERMOSTATS)

    def v0_get_pump_group_configuration(self, pump_group_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        """
        Get a specific pump_group_configuration defined by its id.

        :param pump_group_id: The id of the pump_group_configuration
        :type pump_group_id: Id
        :param fields: The field of the pump_group_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        return self._eeprom_controller.read(PumpGroupConfiguration, pump_group_id, fields).serialize()

    def v0_get_pump_group_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        """
        Get all pump_group_configurations.

        :param fields: The field of the pump_group_configuration to get. (None gets all fields)
        :type fields: List of strings
        :returns: list of pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        return [o.serialize() for o in self._eeprom_controller.read_all(PumpGroupConfiguration, fields)]

    def v0_set_pump_group_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        """
        Set one pump_group_configuration.

        :param config: The pump_group_configuration to set
        :type config: pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        self._eeprom_controller.write(PumpGroupConfiguration.deserialize(config))

    def v0_set_pump_group_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        """
        Set multiple pump_group_configurations.

        :param config: The list of pump_group_configurations to set
        :type config: list of pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        """
        self._eeprom_controller.write_batch([PumpGroupConfiguration.deserialize(o) for o in config])

    def v0_set_thermostat_mode(self, thermostat_on, cooling_mode=False, cooling_on=False, automatic=None, setpoint=None):
        # type: (bool, bool, bool, Optional[bool], Optional[int]) -> Dict[str,Any]
        """ Set the mode of the thermostats.
        :param thermostat_on: Whether the thermostats are on
        :type thermostat_on: boolean
        :param cooling_mode: Cooling mode (True) of Heating mode (False)
        :type cooling_mode: boolean | None
        :param cooling_on: Turns cooling ON when set to true.
        :type cooling_on: boolean | None
        :param automatic: Indicates whether the thermostat system should be set to automatic
        :type automatic: boolean | None
        :param setpoint: Requested setpoint (integer 0-5)
        :type setpoint: int | None
        :returns: dict with 'status'
        """
        _ = thermostat_on  # Still accept `thermostat_on` for backwards compatibility

        # Figure out whether the system should be on or off
        set_on = False
        if cooling_mode is True and cooling_on is True:
            set_on = True
        if cooling_mode is False:
            # Heating means threshold based
            global_config = self.v0_get_global_thermostat_configuration()
            outside_sensor = global_config['outside_sensor']
            current_temperatures = self._master_controller.get_sensors_temperature()[:32]
            if len(current_temperatures) < 32:
                current_temperatures += [None] * (32 - len(current_temperatures))
            if len(current_temperatures) > outside_sensor:
                current_temperature = current_temperatures[outside_sensor]
                set_on = global_config['threshold_temp'] > current_temperature
            else:
                set_on = True

        # Calculate and set the global mode
        mode = 0
        mode |= (1 if set_on is True else 0) << 7
        mode |= 1 << 6  # multi-tenant mode
        mode |= (1 if cooling_mode else 0) << 4
        if automatic is not None:
            mode |= (1 if automatic else 0) << 3

        self.check_basic_action(self._master_communicator.do_basic_action(
            master_api.BA_THERMOSTAT_MODE, mode
        ))

        # Caclulate and set the cooling/heating mode
        cooling_heating_mode = 0
        if cooling_mode is True:
            cooling_heating_mode = 1 if cooling_on is False else 2

        self.check_basic_action(self._master_communicator.do_basic_action(
            master_api.BA_THERMOSTAT_COOLING_HEATING, cooling_heating_mode
        ))

        # Then, set manual/auto
        if automatic is not None:
            action_number = 1 if automatic is True else 0
            self.check_basic_action(self._master_communicator.do_basic_action(
                master_api.BA_THERMOSTAT_AUTOMATIC, action_number
            ))

        # If manual, set the setpoint if appropriate
        if automatic is False and setpoint is not None and 3 <= setpoint <= 5:
            self.check_basic_action(self._master_communicator.do_basic_action(
                getattr(master_api, 'BA_ALL_SETPOINT_{0}'.format(setpoint)), 0
            ))

        self.invalidate_cache(Observer.Types.THERMOSTATS)
        self.increase_interval(Observer.Types.THERMOSTATS, interval=2, window=10)
        return {'status': 'OK'}

    def v0_set_per_thermostat_mode(self, thermostat_id, automatic, setpoint):
        # type: (int, bool, int) -> Dict[str,Any]
        """ Set the setpoint/mode for a certain thermostat.
        :param thermostat_id: The id of the thermostat.
        :type thermostat_id: Integer [0, 31]
        :param automatic: Automatic mode (True) or Manual mode (False)
        :type automatic: boolean
        :param setpoint: The current setpoint
        :type setpoint: Integer [0, 5]
        :returns: dict with 'status'
        """
        if thermostat_id < 0 or thermostat_id > 31:
            raise ValueError('Thermostat_id not in [0, 31]: %d' % thermostat_id)

        if setpoint < 0 or setpoint > 5:
            raise ValueError('Setpoint not in [0, 5]: %d' % setpoint)

        if automatic:
            self.check_basic_action(self._master_communicator.do_basic_action(
                master_api.BA_THERMOSTAT_TENANT_AUTO, thermostat_id
            ))
        else:
            self.check_basic_action(self._master_communicator.do_basic_action(
                master_api.BA_THERMOSTAT_TENANT_MANUAL, thermostat_id
            ))

            self.check_basic_action(self._master_communicator.do_basic_action(
                getattr(master_api, 'BA_ONE_SETPOINT_{0}'.format(setpoint)), thermostat_id
            ))

        self.invalidate_cache(Observer.Types.THERMOSTATS)
        self.increase_interval(Observer.Types.THERMOSTATS, interval=2, window=10)
        return {'status': 'OK'}

    def v0_set_airco_status(self, thermostat_id, airco_on):
        # type: (int, bool) -> Dict[str,Any]
        """ Set the mode of the airco attached to a given thermostat.
        :param thermostat_id: The thermostat id.
        :type thermostat_id: Integer [0, 31]
        :param airco_on: Turns the airco on if True.
        :type airco_on: boolean.
        :returns: dict with 'status'.
        """
        if thermostat_id < 0 or thermostat_id > 31:
            raise ValueError('thermostat_id not in [0, 31]: %d' % thermostat_id)

        modifier = 0 if airco_on else 100

        self.check_basic_action(self._master_communicator.do_basic_action(
            master_api.BA_THERMOSTAT_AIRCO_STATUS, modifier + thermostat_id
        ))

        return {'status': 'OK'}

    def v0_get_airco_status(self):
        # type: () -> Dict[str,Any]
        """ Get the mode of the airco attached to a all thermostats.
        :returns: dict with ASB0-ASB31.
        """
        return self._master_communicator.do_command(master_api.read_airco_status_bits())

    @staticmethod
    def __check_thermostat(thermostat):
        """ :raises ValueError if thermostat not in range [0, 32]. """
        if thermostat not in range(0, 32):
            raise ValueError('Thermostat not in [0,32]: %d' % thermostat)

    def v0_set_current_setpoint(self, thermostat, temperature):
        # type: (int, float) -> Dict[str,Any]
        """ Set the current setpoint of a thermostat.
        :param thermostat: The id of the thermostat to set
        :type thermostat: Integer [0, 32]
        :param temperature: The temperature to set in degrees Celcius
        :type temperature: float
        :returns: dict with 'thermostat', 'config' and 'temp'
        """
        self.__check_thermostat(thermostat)
        self._master_communicator.do_command(master_api.write_setpoint(), {'thermostat': thermostat,
                                                                           'config': 0,
                                                                           'temp': master_api.Svt.temp(temperature)})

        self.invalidate_cache(Observer.Types.THERMOSTATS)
        self.increase_interval(Observer.Types.THERMOSTATS, interval=2, window=10)
        return {'status': 'OK'}

    def _monitor(self):
        # type: () -> None
        """ Monitors certain system states to detect changes without events """
        try:
            # Refresh if required
            if self._thermostats_last_updated + self._thermostats_interval < time.time():
                self._refresh_thermostats()
            # Restore interval if required
            if self._thermostats_restore < time.time():
                self._thermostats_interval = self._thermostats_original_interval
        except CommunicationTimedOutException:
            logger.error('Got communication timeout during thermostat monitoring, waiting 10 seconds.')
            raise DaemonThreadWait

    def _refresh_thermostats(self):
        """
        Get basic information about all thermostats and pushes it in to the Thermostat Status tracker
        """

        def get_automatic_setpoint(_mode):
            _automatic = bool(_mode & 1 << 3)
            return _automatic, 0 if _automatic else (_mode & 0b00000111)

        try:
            thermostat_info = self._master_communicator.do_command(master_api.thermostat_list())
            thermostat_mode = self._master_communicator.do_command(master_api.thermostat_mode_list())
            aircos = self._master_communicator.do_command(master_api.read_airco_status_bits())
        except InMaintenanceModeException:
            return

        status = {state.id: state for state in self._output_controller.get_output_statuses()}  # type: Dict[int,OutputStateDTO]

        mode = thermostat_info['mode']
        thermostats_on = bool(mode & 1 << 7)
        cooling = bool(mode & 1 << 4)
        automatic, setpoint = get_automatic_setpoint(thermostat_mode['mode0'])

        try:
            if cooling:
                self._thermostats_config = {thermostat.id: thermostat
                                            for thermostat in self.load_cooling_thermostats()}
            else:
                self._thermostats_config = {thermostat.id: thermostat
                                            for thermostat in self.load_heating_thermostats()}
        except InMaintenanceModeException:
            return

        thermostats = []
        for thermostat_id in range(32):
            thermostat_dto = self._thermostats_config[thermostat_id]  # type: ThermostatDTO
            if thermostat_dto.in_use:
                t_mode = thermostat_mode['mode{0}'.format(thermostat_id)]
                t_automatic, t_setpoint = get_automatic_setpoint(t_mode)
                thermostat = {'id': thermostat_id,
                              'act': thermostat_info['tmp{0}'.format(thermostat_id)].get_temperature(),
                              'csetp': thermostat_info['setp{0}'.format(thermostat_id)].get_temperature(),
                              'outside': thermostat_info['outside'].get_temperature(),
                              'mode': t_mode,
                              'automatic': t_automatic,
                              'setpoint': t_setpoint,
                              'name': thermostat_dto.name,
                              'sensor_nr': thermostat_dto.sensor,
                              'airco': aircos['ASB{0}'.format(thermostat_id)]}
                for output in [0, 1]:
                    output_id = getattr(thermostat_dto, 'output{0}'.format(output))
                    output_state_dto = status.get(output_id)
                    if output_id is not None and output_state_dto is not None and output_state_dto.status:
                        thermostat['output{0}'.format(output)] = output_state_dto.dimmer
                    else:
                        thermostat['output{0}'.format(output)] = 0
                thermostats.append(thermostat)

        self._thermostat_status.full_update({'thermostats_on': thermostats_on,
                                             'automatic': automatic,
                                             'setpoint': setpoint,
                                             'cooling': cooling,
                                             'status': thermostats})
        self._thermostats_last_updated = time.time()

    def v0_get_thermostat_status(self):
        # type: () -> Dict[str,Any]
        """ Returns thermostat information """
        self._refresh_thermostats()  # Always return the latest information
        return self._thermostat_status.get_thermostats()
