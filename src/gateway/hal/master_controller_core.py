# Copyright (C) 2019 OpenMotics BV
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
Module for communicating with the Master
"""
from __future__ import absolute_import

import logging
import time
from datetime import datetime
from threading import Thread, Timer

from peewee import DoesNotExist

from gateway.dto import GroupActionDTO, InputDTO, OutputDTO, PulseCounterDTO, \
    SensorDTO, ShutterDTO, ShutterGroupDTO, ThermostatDTO, ModuleDTO
from gateway.enums import ShutterEnums
from gateway.hal.mappers_core import GroupActionMapper, InputMapper, \
    OutputMapper, SensorMapper, ShutterMapper
from gateway.hal.master_controller import CommunicationFailure, \
    MasterController
from gateway.hal.master_event import MasterEvent
from ioc import INJECTED, Inject
from master.core.core_api import CoreAPI
from master.core.core_communicator import BackgroundConsumer, CoreCommunicator
from master.core.core_updater import CoreUpdater
from master.core.errors import Error
from master.core.events import Event as MasterCoreEvent
from master.core.group_action import GroupActionController
from master.core.memory_file import MemoryFile, MemoryTypes
from master.core.memory_models import GlobalConfiguration, \
    InputConfiguration, InputModuleConfiguration, OutputConfiguration, \
    OutputModuleConfiguration, SensorConfiguration, \
    SensorModuleConfiguration, ShutterConfiguration
from master.core.slave_communicator import SlaveCommunicator
from master.core.ucan_communicator import UCANCommunicator
from serial_utils import CommunicationTimedOutException

if False:  # MYPY
    from typing import Any, Dict, List, Tuple, Optional
    from gateway.dto import OutputStateDTO

logger = logging.getLogger("openmotics")


class MasterCoreController(MasterController):

    @Inject
    def __init__(self, master_communicator=INJECTED, ucan_communicator=INJECTED, slave_communicator=INJECTED, memory_files=INJECTED):
        # type: (CoreCommunicator, UCANCommunicator, SlaveCommunicator, Dict[str,MemoryFile]) -> None
        super(MasterCoreController, self).__init__(master_communicator)
        self._master_communicator = master_communicator  # type: CoreCommunicator
        self._ucan_communicator = ucan_communicator  # type: UCANCommunicator
        self._slave_communicator = slave_communicator  # type: SlaveCommunicator
        self._memory_files = memory_files  # type: Dict[str, MemoryFile]
        self._synchronization_thread = Thread(target=self._synchronize, name='CoreMasterSynchronization')
        self._master_online = False
        self._discover_mode_timer = None  # type: Optional[Timer]
        self._input_state = MasterInputState()
        self._output_states = {}  # type: Dict[int,OutputStateDTO]
        self._sensor_interval = 300
        self._sensor_last_updated = 0.0
        self._sensor_states = {}  # type: Dict[int,Dict[str,None]]
        self._shutters_interval = 600
        self._shutters_last_updated = 0.0
        self._shutter_status = {}  # type: Dict[int, Tuple[bool, bool]]
        self._time_last_updated = 0.0
        self._output_shutter_map = {}  # type: Dict[int, int]

        self._memory_files[MemoryTypes.EEPROM].subscribe_eeprom_change(self._handle_eeprom_change)

        self._master_communicator.register_consumer(
            BackgroundConsumer(CoreAPI.event_information(), 0, self._handle_event)
        )
        self._master_communicator.register_consumer(
            BackgroundConsumer(CoreAPI.error_information(), 0, lambda e: logger.info('Got master error: {0}'.format(Error(e))))
        )
        self._master_communicator.register_consumer(
            BackgroundConsumer(CoreAPI.ucan_module_information(), 0, lambda i: logger.info('Got ucan module information: {0}'.format(i)))
        )

    #################
    # Private stuff #
    #################

    def _handle_eeprom_change(self):
        self._output_shutter_map = {}
        self._shutters_last_updated = 0.0
        self._sensor_last_updated = 0.0
        self._input_last_updated = 0.0
        self._output_last_updated = 0.0
        self._publish_event(MasterEvent(event_type=MasterEvent.Types.EEPROM_CHANGE, data=None))

    def _handle_event(self, data):
        # type: (Dict[str,Any]) -> None
        core_event = MasterCoreEvent(data)
        if core_event.type not in [MasterCoreEvent.Types.LED_BLINK,
                                   MasterCoreEvent.Types.LED_ON,
                                   MasterCoreEvent.Types.BUTTON_PRESS]:
            # Interesting for debug purposes, but not for everything
            logger.info('Got master event: {0}'.format(core_event))
        if core_event.type == MasterCoreEvent.Types.OUTPUT:
            # Update internal state cache
            output_id = core_event.data['output']
            timer_value = core_event.data['timer_value']
            if timer_value is not None:
                timer_value *= core_event.data['timer_factor']
            event_data = {'id': output_id,
                          'status': core_event.data['status'],
                          'dimmer': core_event.data['dimmer_value'],
                          'ctimer': timer_value}
            self._handle_output(output_id, event_data)
        elif core_event.type == MasterCoreEvent.Types.INPUT:
            event = self._input_state.handle_event(core_event)
            self._publish_event(event)
        elif core_event.type == MasterCoreEvent.Types.SENSOR:
            sensor_id = core_event.data['sensor']
            if sensor_id not in self._sensor_states:
                return
            self._sensor_states[sensor_id][core_event.data['type']] = core_event.data['value']

    def _handle_output(self, output_id, event_data):
        # type: (int ,Dict[str,Any]) -> None
        self._publish_event(MasterEvent(MasterEvent.Types.OUTPUT_STATUS, event_data))
        shutter_id = self._output_shutter_map.get(output_id)
        if shutter_id:
            shutter = ShutterConfiguration(shutter_id)
            output_0_on, output_1_on = (None, None)
            if output_id == shutter.outputs.output_0:
                output_0_on = event_data['status']
            if output_id == shutter.outputs.output_1:
                output_1_on = event_data['status']
            self._handle_shutter(shutter, output_0_on, output_1_on)

    def _handle_shutter(self, shutter, output_0_on, output_1_on):
        # type: (ShutterConfiguration, Optional[bool], Optional[bool]) -> None
        if shutter.outputs.output_0 == 255 * 2:
            return
        if output_0_on is None:
            output_0_on = self._shutter_status[shutter.id][0]
        if output_1_on is None:
            output_1_on = self._shutter_status[shutter.id][1]
        if (output_0_on, output_1_on) == self._shutter_status.get(shutter.id, (None, None)):
            logger.error('shutter status did not change')
            return

        output_module = OutputConfiguration(shutter.outputs.output_0).module
        if getattr(output_module.shutter_config, 'set_{0}_direction'.format(shutter.output_set)):
            up, down = output_0_on, output_1_on
        else:
            up, down = output_1_on, output_0_on

        if up == 1 and down == 0:
            state = ShutterEnums.State.GOING_UP
        elif down == 1 and up == 0:
            state = ShutterEnums.State.GOING_DOWN
        else:  # Both are off or - unlikely - both are on
            state = ShutterEnums.State.STOPPED

        event_data = {'id': shutter.id,
                      'status': state,
                      'location': {'room_id': 255}}  # TODO: rooms
        self._publish_event(MasterEvent(event_type=MasterEvent.Types.SHUTTER_CHANGE, data=event_data))
        self._shutter_status[shutter.id] = (output_0_on, output_1_on)

    def _synchronize(self):
        # type: () -> None
        while True:
            try:
                # Refresh if required
                if self._time_last_updated + 300 < time.time():
                    self._check_master_time()
                if self._refresh_input_states():
                    self._set_master_state(True)
                if self._sensor_last_updated + self._sensor_interval < time.time():
                    self._refresh_sensor_states()
                    self._set_master_state(True)
                if self._shutters_last_updated + self._shutters_interval < time.time():
                    self._refresh_shutter_states()
                    self._set_master_state(True)
                time.sleep(1)
            except CommunicationTimedOutException:
                logger.error('Got communication timeout during synchronization, waiting 10 seconds.')
                self._set_master_state(False)
                time.sleep(10)
            except CommunicationFailure:
                # This is an expected situation
                time.sleep(10)
            except Exception as ex:
                logger.exception('Unexpected error during synchronization: {0}'.format(ex))
                time.sleep(10)

    def _set_master_state(self, online):
        if online != self._master_online:
            self._master_online = online

    def _enumerate_io_modules(self, module_type, amount_per_module=8):
        cmd = CoreAPI.general_configuration_number_of_modules()
        module_count = self._master_communicator.do_command(cmd, {})[module_type]
        return range(module_count * amount_per_module)

    def _check_master_time(self):
        # type: () -> None
        date_time = self._master_communicator.do_command(CoreAPI.get_date_time(), {})
        if date_time is None:
            return
        try:
            core_value = datetime(2000 + date_time['year'], date_time['month'], date_time['day'],
                                  date_time['hours'], date_time['minutes'], date_time['seconds'])
            core_weekday = date_time['weekday']
        except ValueError:
            core_value = datetime(2000, 1, 1, 0, 0, 0)
            core_weekday = 0

        now = datetime.now()
        expected_weekday = now.weekday() + 1
        expected_value = now.replace(microsecond=0)

        sync = False
        if abs((core_value - expected_value).total_seconds()) > 180:  # Allow 3 minutes difference
            sync = True
        if core_weekday != expected_weekday:
            sync = True

        if sync is True:
            if expected_value.hour == 0 and expected_value.minute < 15:
                logger.info('Skip setting time between 00:00 and 00:15')
            else:
                logger.info('Time - core: {0} ({1}) - gateway: {2} ({3})'.format(
                    core_value, core_weekday, expected_value, expected_weekday)
                )
                self.sync_time()
        self._time_last_updated = time.time()

    #######################
    # Internal management #
    #######################

    def start(self):
        super(MasterCoreController, self).start()
        self._synchronization_thread.start()
        try:
            self._log_stats()
        except Exception:
            pass

    def set_plugin_controller(self, plugin_controller):
        """ Set the plugin controller. """
        pass  # TODO: implement

    def _log_stats(self):
        def _default_if_255(value, default):
            return value if value != 255 else default

        max_specs = self._master_communicator.do_command(CoreAPI.general_configuration_max_specs(), {})
        general_configuration = GlobalConfiguration()
        logger.info('General core information:')
        logger.info('* Modules:')
        logger.info('  * Output: {0}/{1}'.format(_default_if_255(general_configuration.number_of_output_modules, 0),
                                                 max_specs['output']))
        logger.info('  * Input: {0}/{1}'.format(_default_if_255(general_configuration.number_of_input_modules, 0),
                                                max_specs['input']))
        logger.info('  * Sensor: {0}/{1}'.format(_default_if_255(general_configuration.number_of_sensor_modules, 0),
                                                 max_specs['sensor']))
        logger.info('  * uCAN: {0}/{1}'.format(_default_if_255(general_configuration.number_of_ucan_modules, 0),
                                               max_specs['ucan']))
        logger.info('  * CAN Control: {0}'.format(_default_if_255(general_configuration.number_of_can_control_modules, 0)))
        logger.info('* CAN:')
        logger.info('  * Inputs: {0}'.format(general_configuration.number_of_can_inputs))
        logger.info('  * Sensors: {0}'.format(general_configuration.number_of_can_sensors))
        logger.info('* Scan times:')
        logger.info('  * General bus: {0}ms'.format(_default_if_255(general_configuration.scan_time_rs485_bus, 8)))
        logger.info('  * Sensor modules: {0}ms'.format(_default_if_255(general_configuration.scan_time_rs485_sensor_modules, 50) * 100))
        logger.info('  * CAN Control modules: {0}ms'.format(_default_if_255(general_configuration.scan_time_rs485_can_control_modules, 50) * 100))
        logger.info('* Runtime stats:')
        logger.info('  * Uptime: {0}d {1}h'.format(general_configuration.uptime_hours / 24,
                                                   general_configuration.uptime_hours % 24))
        # noinspection PyStringFormat
        logger.info('  * Started at 20{0}/{1}/{2} {3}:{4}:{5}'.format(*(list(reversed(general_configuration.startup_date)) +
                                                                        general_configuration.startup_time)))

    ##############
    # Public API #
    ##############

    def invalidate_caches(self):
        # type: () -> None
        self._input_last_updated = 0

    def get_firmware_version(self):
        version = self._master_communicator.do_command(CoreAPI.get_firmware_version(), {})['version']
        return tuple(version.split('.'))

    def sync_time(self):
        # type: () -> None
        logger.info('Setting the time on the core.')
        now = datetime.now()
        self._master_communicator.do_command(
            CoreAPI.set_date_time(),
            {'hours': now.hour, 'minutes': now.minute, 'seconds': now.second,
             'weekday': now.isoweekday(), 'day': now.day, 'month': now.month, 'year': now.year % 100}
        )

    # Memory (eeprom/fram)

    def eeprom_read_page(self, page):
        return self._memory_files[MemoryTypes.EEPROM].read_page(page)

    def fram_read_page(self, page):
        return self._memory_files[MemoryTypes.FRAM].read_page(page)

    # Input

    def get_input_module_type(self, input_module_id):
        input_module = InputConfiguration(input_module_id)
        return input_module.module.device_type

    def get_inputs_with_status(self):
        # type: () -> List[Dict[str,Any]]
        return self._input_state.get_inputs()

    def get_recent_inputs(self):
        # type: () -> List[int]
        return self._input_state.get_recent()

    def load_input(self, input_id):  # type: (int) -> InputDTO
        input_ = InputConfiguration(input_id)
        return InputMapper.orm_to_dto(input_)

    def load_inputs(self):  # type: () -> List[InputDTO]
        inputs = []
        for i in self._enumerate_io_modules('input'):
            inputs.append(self.load_input(i))
        return inputs

    def save_inputs(self, inputs):  # type: (List[Tuple[InputDTO, List[str]]]) -> None
        for input_dto, fields in inputs:
            input_ = InputMapper.dto_to_orm(input_dto, fields)
            input_.save()  # TODO: Batch saving - postpone eeprom activate if relevant for the Core

    def _refresh_input_states(self):
        # type: () -> bool
        refresh = self._input_state.should_refresh()
        if refresh:
            cmd = CoreAPI.device_information_list_inputs()
            data = self._master_communicator.do_command(cmd, {})
            if data is not None:
                for event in self._input_state.refresh(data['information']):
                    self._publish_event(event)
        return refresh

    # Outputs

    def set_output(self, output_id, state, dimmer=None, timer=None):
        output = OutputConfiguration(output_id)
        if output.is_shutter:
            # Shutter outputs cannot be controlled
            return
        _ = dimmer, timer  # TODO: Use `dimmer` and `timer`
        action = 1 if state else 0
        self._master_communicator.do_command(CoreAPI.basic_action(), {'type': 0, 'action': action,
                                                                      'device_nr': output_id,
                                                                      'extra_parameter': 0})

    def toggle_output(self, output_id):
        output = OutputConfiguration(output_id)
        if output.is_shutter:
            # Shutter outputs cannot be controlled
            return
        self._master_communicator.do_command(CoreAPI.basic_action(), {'type': 0, 'action': 16,
                                                                      'device_nr': output_id,
                                                                      'extra_parameter': 0})

    def load_output(self, output_id):  # type: (int) -> OutputDTO
        output = OutputConfiguration(output_id)
        if output.is_shutter:
            # Outputs that are used by a shutter are returned as unconfigured (read-only) outputs
            return OutputDTO(id=output.id)
        return OutputMapper.orm_to_dto(output)

    def load_outputs(self):  # type: () -> List[OutputDTO]
        outputs = []
        for i in self._enumerate_io_modules('output'):
            outputs.append(self.load_output(i))
        return outputs

    def save_outputs(self, outputs):  # type: (List[Tuple[OutputDTO, List[str]]]) -> None
        for output_dto, fields in outputs:
            output = OutputMapper.dto_to_orm(output_dto, fields)
            if output.is_shutter:
                # Shutter outputs cannot be changed
                continue
            output.save()  # TODO: Batch saving - postpone eeprom activate if relevant for the Core

    def load_output_status(self):
        # type: () -> List[Dict[str,Any]]
        output_status = []
        for i in self._enumerate_io_modules('output'):
            state_data = self._master_communicator.do_command(CoreAPI.output_detail(), {'device_nr': i})
            output_status.append(state_data)
        return output_status

    # Shutters

    def shutter_up(self, shutter_id):
        self._master_communicator.do_basic_action(action_type=10,
                                                  action=1,
                                                  device_nr=shutter_id)

    def shutter_down(self, shutter_id):
        self._master_communicator.do_basic_action(action_type=10,
                                                  action=2,
                                                  device_nr=shutter_id)

    def shutter_stop(self, shutter_id):
        self._master_communicator.do_basic_action(action_type=10,
                                                  action=0,
                                                  device_nr=shutter_id)

    def load_shutter(self, shutter_id):  # type: (int) -> ShutterDTO
        shutter = ShutterConfiguration(shutter_id)
        shutter_dto = ShutterMapper.orm_to_dto(shutter)
        # Load information that is set on the Output(Module)Configuration
        output_module = OutputConfiguration(shutter.outputs.output_0).module
        if getattr(output_module.shutter_config, 'set_{0}_direction'.format(shutter.output_set)):
            shutter_dto.up_down_config = 1
        else:
            shutter_dto.up_down_config = 0
        return shutter_dto

    def load_shutters(self):  # type: () -> List[ShutterDTO]
        # At this moment, the system expects a given amount of Shutter modules to be physically
        # installed. However, in the Core+, this is not the case as a Shutter isn't a physical module
        # but instead a virtual layer over physical Output modules. For easy backwards compatible
        # implementation, a Shutter will map 1-to-1 to the Outputs with the same ID. This means we only need
        # to emulate such a Shutter module foreach Output module.
        shutters = []
        for shutter_id in self._enumerate_io_modules('output', amount_per_module=4):
            shutters.append(self.load_shutter(shutter_id))
        return shutters

    def save_shutters(self, shutters):  # type: (List[Tuple[ShutterDTO, List[str]]]) -> None
        # TODO: Batch saving - postpone eeprom activate if relevant for the Core
        # TODO: Atomic saving
        for shutter_dto, fields in shutters:
            # Configure shutter
            shutter = ShutterMapper.dto_to_orm(shutter_dto, fields)
            if shutter.timer_down is not None and shutter.timer_up is not None:
                # Shutter is "configured"
                shutter.outputs.output_0 = shutter.id * 2
                self._output_shutter_map[shutter.outputs.output_0] = shutter.id
                self._output_shutter_map[shutter.outputs.output_1] = shutter.id
                is_configured = True
            else:
                self._output_shutter_map.pop(shutter.outputs.output_0, None)
                self._output_shutter_map.pop(shutter.outputs.output_1, None)
                shutter.outputs.output_0 = 255 * 2
                is_configured = False
            shutter.save()
            # Mark related Outputs as "occupied by shutter"
            output_module = OutputConfiguration(shutter_dto.id * 2).module
            setattr(output_module.shutter_config, 'are_{0}_outputs'.format(shutter.output_set), not is_configured)
            setattr(output_module.shutter_config, 'set_{0}_direction'.format(shutter.output_set), shutter_dto.up_down_config == 1)
            output_module.save()

    def _refresh_shutter_states(self):
        status_data = {x['id']: x for x in self.load_output_status()}
        for shutter_id in range(len(status_data) // 2):
            shutter = ShutterConfiguration(shutter_id)
            output_0 = status_data.get(shutter.outputs.output_0)
            output_1 = status_data.get(shutter.outputs.output_1)
            if output_0 and output_1:
                self._output_shutter_map[shutter.outputs.output_0] = shutter.id
                self._output_shutter_map[shutter.outputs.output_1] = shutter.id
                self._handle_shutter(shutter, output_0['status'], output_1['status'])
            else:
                self._shutter_status.pop(shutter.id, None)
                self._output_shutter_map.pop(shutter.outputs.output_0, None)
                self._output_shutter_map.pop(shutter.outputs.output_1, None)
        self._shutters_last_updated = time.time()

    def shutter_group_up(self, shutter_group_id):  # type: (int) -> None
        raise NotImplementedError()  # TODO: Implement once supported by Core(+)

    def shutter_group_down(self, shutter_group_id):  # type: (int) -> None
        raise NotImplementedError()  # TODO: Implement once supported by Core(+)

    def shutter_group_stop(self, shutter_group_id):  # type: (int) -> None
        raise NotImplementedError()  # TODO: Implement once supported by Core(+)

    def load_shutter_group(self, shutter_group_id):  # type: (int) -> ShutterGroupDTO
        return ShutterGroupDTO(id=shutter_group_id)

    def load_shutter_groups(self):  # type: () -> List[ShutterGroupDTO]
        shutter_groups = []
        for i in range(16):
            shutter_groups.append(ShutterGroupDTO(id=i))
        return shutter_groups

    def save_shutter_groups(self, shutter_groups):  # type: (List[Tuple[ShutterGroupDTO, List[str]]]) -> None
        pass  # TODO: Implement when/if ShutterGroups get actual properties

    # Thermostats

    def load_heating_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        raise NotImplementedError()

    def load_heating_thermostats(self):  # type: () -> List[ThermostatDTO]
        raise NotImplementedError()

    def save_heating_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        raise NotImplementedError()

    def load_cooling_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        raise NotImplementedError()

    def load_cooling_thermostats(self):  # type: () -> List[ThermostatDTO]
        raise NotImplementedError()

    def save_cooling_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        raise NotImplementedError()

    # Can Led functions

    def load_can_led_configurations(self, fields=None):
        # type: (Any) -> List[Dict[str,Any]]
        return []  # TODO: implement

    # Sensors

    def get_sensor_temperature(self, sensor_id):
        return self._sensor_states.get(sensor_id, {}).get('TEMPERATURE')

    def get_sensors_temperature(self):
        amount_sensor_modules = self._master_communicator.do_command(CoreAPI.general_configuration_number_of_modules(), {})['sensor']
        temperatures = []
        for sensor_id in range(amount_sensor_modules * 8):
            temperatures.append(self.get_sensor_temperature(sensor_id))
        return temperatures

    def get_sensor_humidity(self, sensor_id):
        return self._sensor_states.get(sensor_id, {}).get('HUMIDITY')

    def get_sensors_humidity(self):
        amount_sensor_modules = self._master_communicator.do_command(CoreAPI.general_configuration_number_of_modules(), {})['sensor']
        humidities = []
        for sensor_id in range(amount_sensor_modules * 8):
            humidities.append(self.get_sensor_humidity(sensor_id))
        return humidities

    def get_sensor_brightness(self, sensor_id):
        # TODO: This is a lux value and must somehow be converted to legacy percentage
        brightness = self._sensor_states.get(sensor_id, {}).get('BRIGHTNESS')
        if brightness is None or brightness in [65535]:
            return None
        return int(float(brightness) / 65535.0 * 100)

    def get_sensors_brightness(self):
        amount_sensor_modules = self._master_communicator.do_command(CoreAPI.general_configuration_number_of_modules(), {})['sensor']
        brightnesses = []
        for sensor_id in range(amount_sensor_modules * 8):
            brightnesses.append(self.get_sensor_brightness(sensor_id))
        return brightnesses

    def load_sensor(self, sensor_id):  # type: (int) -> SensorDTO
        sensor = SensorConfiguration(sensor_id)
        return SensorMapper.orm_to_dto(sensor)

    def load_sensors(self):  # type: () -> List[SensorDTO]
        sensors = []
        for i in self._enumerate_io_modules('sensor'):
            sensors.append(self.load_sensor(i))
        return sensors

    def save_sensors(self, sensors):  # type: (List[Tuple[SensorDTO, List[str]]]) -> None
        for sensor_dto, fields in sensors:
            sensor = SensorMapper.dto_to_orm(sensor_dto, fields)
            sensor.save()  # TODO: Batch saving - postpone eeprom activate if relevant for the Core

    def _refresh_sensor_states(self):
        amount_sensor_modules = self._master_communicator.do_command(CoreAPI.general_configuration_number_of_modules(), {})['sensor']
        for module_nr in range(amount_sensor_modules):
            temperature_values = self._master_communicator.do_command(CoreAPI.sensor_temperature_values(), {'module_nr': module_nr})['values']
            brightness_values = self._master_communicator.do_command(CoreAPI.sensor_brightness_values(), {'module_nr': module_nr})['values']
            humidity_values = self._master_communicator.do_command(CoreAPI.sensor_humidity_values(), {'module_nr': module_nr})['values']
            for i in range(8):
                sensor_id = module_nr * 8 + i
                self._sensor_states[sensor_id] = {'TEMPERATURE': temperature_values[i],
                                                  'BRIGHTNESS': brightness_values[i],
                                                  'HUMIDITY': humidity_values[i]}
        self._sensor_last_updated = time.time()

    def set_virtual_sensor(self, sensor_id, temperature, humidity, brightness):
        raise NotImplementedError()

    # PulseCounters

    def load_pulse_counter(self, pulse_counter_id):  # type: (int) -> PulseCounterDTO
        # TODO: Implement PulseCounters
        raise DoesNotExist('Could not find a PulseCounter with id {0}'.format(pulse_counter_id))

    def load_pulse_counters(self):  # type: () -> List[PulseCounterDTO]
        # TODO: Implement PulseCounters
        return []

    def save_pulse_counters(self, pulse_counters):  # type: (List[Tuple[PulseCounterDTO, List[str]]]) -> None
        # TODO: Implement PulseCounters
        return

    def get_pulse_counter_values(self):  # type: () -> Dict[int, int]
        # TODO: Implement PulseCounters
        return {}

    # (Group)Actions

    def do_basic_action(self, action_type, action_number):  # type: (int, int) -> None
        basic_actions = GroupActionMapper.classic_actions_to_core_actions([action_type, action_number])
        for basic_action in basic_actions:
            self._master_communicator.do_basic_action(action_type=basic_action.action_type,
                                                      action=basic_action.action,
                                                      device_nr=basic_action.device_nr,
                                                      extra_parameter=basic_action.extra_parameter)

    def do_group_action(self, group_action_id):  # type: (int) -> None
        self._master_communicator.do_basic_action(action_type=19, action=0, device_nr=group_action_id)

    def load_group_action(self, group_action_id):  # type: (int) -> GroupActionDTO
        return GroupActionMapper.orm_to_dto(GroupActionController.load_group_action(group_action_id))

    def load_group_actions(self):  # type: () -> List[GroupActionDTO]
        return [GroupActionMapper.orm_to_dto(o)
                for o in GroupActionController.load_group_actions()]

    def save_group_actions(self, group_actions):  # type: (List[Tuple[GroupActionDTO, List[str]]]) -> None
        for group_action_dto, fields in group_actions:
            group_action = GroupActionMapper.dto_to_orm(group_action_dto, fields)
            GroupActionController.save_group_action(group_action, fields)

    # Virtual modules

    def add_virtual_output_module(self):
        raise NotImplementedError()

    def add_virtual_dim_module(self):
        raise NotImplementedError()

    def add_virtual_input_module(self):
        raise NotImplementedError()

    # Module management

    def module_discover_start(self, timeout):  # type: (int) -> None
        def _stop(): self.module_discover_stop()

        self._master_communicator.do_basic_action(action_type=200,
                                                  action=0,
                                                  extra_parameter=0)

        if self._discover_mode_timer is not None:
            self._discover_mode_timer.cancel()
        self._discover_mode_timer = Timer(timeout, _stop)
        self._discover_mode_timer.start()

    def module_discover_stop(self):  # type: () -> None
        if self._discover_mode_timer is not None:
            self._discover_mode_timer.cancel()
            self._discover_mode_timer = None

        self._master_communicator.do_basic_action(action_type=200,
                                                  action=0,
                                                  extra_parameter=255)

        self._publish_event(MasterEvent(event_type=MasterEvent.Types.MODULE_DISCOVERY, data={}))

    def module_discover_status(self):  # type: () -> bool
        return self._discover_mode_timer is not None

    def get_module_log(self):  # type: () -> List[Dict[str, Any]]
        raise NotImplementedError()  # No need to implement. Not used and rather obsolete code anyway

    def get_modules(self):
        def _default_if_255(value, default):
            return value if value != 255 else default

        general_configuration = GlobalConfiguration()

        outputs = []
        nr_of_output_modules = _default_if_255(general_configuration.number_of_output_modules, 0)
        for module_id in range(nr_of_output_modules):
            output_module_info = OutputModuleConfiguration(module_id)
            device_type = output_module_info.device_type
            if device_type == 'o' and output_module_info.address[4:15] in ['000.000.000',
                                                                           '000.000.001',
                                                                           '000.000.002']:
                outputs.append('P')  # Internal output module
            else:
                # Use device_type, except for shutters, which are now kinda output module alike
                outputs.append({'r': 'o',
                                'R': 'O'}.get(device_type, device_type))

        inputs = []
        can_inputs = []
        nr_of_input_modules = _default_if_255(general_configuration.number_of_input_modules, 0)
        nr_of_sensor_modules = _default_if_255(general_configuration.number_of_sensor_modules, 0)
        nr_of_can_controls = _default_if_255(general_configuration.number_of_can_control_modules, 0)
        for module_id in range(nr_of_input_modules):
            input_module_info = InputModuleConfiguration(module_id)
            device_type = input_module_info.device_type
            if device_type == 'i' and input_module_info.address.endswith('000.000.000'):
                inputs.append('J')  # Internal input module
            elif device_type == 'b':
                can_inputs.append('I')  # uCAN input "module"
            elif device_type in ['I', 'i']:
                inputs.append(device_type)  # Slave and virtual input module
        for module_id in range(nr_of_sensor_modules):
            sensor_module_info = SensorModuleConfiguration(module_id)
            device_type = sensor_module_info.device_type
            if device_type == 'T':
                inputs.append('T')
            elif device_type == 's':
                can_inputs.append('T')  # uCAN sensor "module"
        for module_id in range(nr_of_can_controls - 1):
            can_inputs.append('C')
        can_inputs.append('E')

        # i/I/J = Virtual/physical/internal Input module
        # o/O/P = Virtual/physical/internal Ouptut module
        # T = Temperature module
        # C/E = Physical/internal CAN Control
        return {'outputs': outputs, 'inputs': inputs, 'shutters': [], 'can_inputs': can_inputs}

    def get_modules_information(self, address=None):  # type: (Optional[str]) -> List[ModuleDTO]
        logger.warn('Core `get_modules_information` not yet implemented')  # TODO
        return []

    def replace_module(self, old_address, new_address):  # type: (str, str) -> None
        raise NotImplementedError('Module replacement not supported')

    def flash_leds(self, led_type, led_id):
        raise NotImplementedError()

    # Generic

    def power_cycle_bus(self):
        raise NotImplementedError()

    def get_status(self):
        firmware_version = self._master_communicator.do_command(CoreAPI.get_firmware_version(), {})['version']
        bus_mode = self._master_communicator.do_command(CoreAPI.get_slave_bus_mode(), {})['mode']
        date_time = self._master_communicator.do_command(CoreAPI.get_date_time(), {})
        return {'time': '{0:02}:{1:02}'.format(date_time['hours'], date_time['minutes']),
                'date': '{0:02}/{1:02}/20{2:02}'.format(date_time['day'], date_time['month'], date_time['year']),
                'mode': {CoreAPI.SlaveBusMode.INIT: 'I',
                         CoreAPI.SlaveBusMode.LIVE: 'L',
                         CoreAPI.SlaveBusMode.TRANSPARENT: 'T'}[bus_mode],
                'version': firmware_version,
                'hw_version': 1}  # TODO: Hardware version

    def reset(self):
        # type: () -> None
        self._master_communicator.do_basic_action(action_type=254, action=0, timeout=None)

    def cold_reset(self):
        # type: () -> Dict[str,Any]
        _ = self  # Must be an instance method
        gpio_direction = open('/sys/class/gpio/gpio49/direction', 'w')
        gpio_direction.write('out')
        gpio_direction.close()

        def power(master_on):
            """ Set the power on the master. """
            gpio_file = open('/sys/class/gpio/gpio49/value', 'w')
            gpio_file.write('0' if master_on else '1')
            gpio_file.close()

        power(False)
        time.sleep(5)
        power(True)

        return {'status': 'OK'}

    def update(self, hex_filename):
        # type: (str) -> None
        CoreUpdater.update(hex_filename=hex_filename)

    def get_backup(self):
        data = []
        pages, page_length = MemoryFile.SIZES[MemoryTypes.EEPROM]
        for page in range(pages):
            data += self._memory_files[MemoryTypes.EEPROM].read_page(page)
        return ''.join(chr(entry) for entry in data)

    def restore(self, data):
        pages, page_length = MemoryFile.SIZES[MemoryTypes.EEPROM]
        data_structure = {}
        for page in range(pages):
            page_data = [ord(entry) for entry in data[page * page_length:(page + 1) * page_length]]
            if len(page_data) < page_length:
                page_data += [255] * (page_length - len(page_data))
            data_structure[page] = page_data
        self._restore(data_structure)

    def factory_reset(self):
        pages, page_length = MemoryFile.SIZES[MemoryTypes.EEPROM]
        self._restore({page: [255] * page_length for page in range(pages)})

    def _restore(self, data):  # type: (Dict[int, List[int]]) -> None
        pages, page_length = MemoryFile.SIZES[MemoryTypes.EEPROM]
        page_retry = None
        page = 0
        while page < pages:
            try:
                self._memory_files[MemoryTypes.EEPROM].write_page(page, data[page])
                page += 1
            except CommunicationTimedOutException:
                if page_retry == page:
                    raise
                page_retry = page
                time.sleep(10)
        self._memory_files[MemoryTypes.EEPROM].activate()

    def error_list(self):
        return []  # TODO: Implement

    def last_success(self):
        return time.time()  # TODO: Implement

    def clear_error_list(self):
        raise NotImplementedError()

    def set_status_leds(self, status):
        raise NotImplementedError()

    def set_all_lights_off(self):
        raise NotImplementedError()

    def set_all_lights_floor_off(self, floor):
        raise NotImplementedError()

    def set_all_lights_floor_on(self, floor):
        raise NotImplementedError()

    def get_configuration_dirty_flag(self):
        return False  # TODO: Implement


class MasterInputState(object):
    def __init__(self, interval=300):
        # type: (int) -> None
        self._interval = interval
        self._last_updated = 0  # type: float
        self._values = {}  # type: Dict[int,MasterInputValue]

    def get_inputs(self):
        # type: () -> List[Dict[str,Any]]
        return [x.serialize() for x in self._values.values()]

    def get_recent(self):
        # type: () -> List[int]
        sorted_inputs = sorted(list(self._values.values()), key=lambda x: x.changed_at)
        recent_events = [x.input_id for x in sorted_inputs
                         if x.changed_at > time.time() - 10]
        return recent_events[-5:]

    def handle_event(self, core_event):
        # type: (MasterCoreEvent) -> MasterEvent
        value = MasterInputValue.from_core_event(core_event)
        if value.input_id not in self._values:
            self._values[value.input_id] = value
        self._values[value.input_id].update(value)
        return value.master_event()

    def should_refresh(self):
        # type: () -> bool
        return self._last_updated + self._interval < time.time()

    def refresh(self, info):
        # type: (List[int]) -> List[MasterEvent]
        events = []
        for i, byte in enumerate(info):
            for j in range(0, 8):
                current_status = byte >> j & 0x1
                input_id = (i * 8) + j
                if input_id not in self._values:
                    self._values[input_id] = MasterInputValue(input_id, current_status)
                state = self._values[input_id]
                if state.update_status(current_status):
                    events.append(state.master_event())
        self._last_updated = time.time()
        return events


class MasterInputValue(object):
    def __init__(self, input_id, status, changed_at=0):
        # type: (int, int, float) -> None
        self.input_id = input_id
        self.status = status
        self.changed_at = changed_at

    @classmethod
    def from_core_event(cls, event):
        # type: (MasterCoreEvent) -> MasterInputValue
        status = 1 if event.data['status'] else 0
        changed_at = time.time()
        return cls(event.data['input'], status, changed_at=changed_at)

    def serialize(self):
        # type: () -> Dict[str,Any]
        return {'id': self.input_id, 'status': self.status}  # TODO: output?

    def update(self, other_value):
        # type: (MasterInputValue) -> None
        self.update_status(other_value.status)

    def update_status(self, current_status):
        # type: (int) -> bool
        is_changed = self.status != current_status
        if is_changed:
            self.status = current_status
            self.changed_at = time.time()
        return is_changed

    def master_event(self):
        # type: () -> MasterEvent
        return MasterEvent(event_type=MasterEvent.Types.INPUT_CHANGE,
                           data={'id': self.input_id,
                                 'status': bool(self.status),
                                 'location': {'room_id': 255}})  # TODO: missing room

    def __repr__(self):
        # type: () -> str
        return '<MasterInputValue {} {} {}>'.format(self.input_id, self.status, self.changed_at)
