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

import functools
import logging
import subprocess
import time
from datetime import datetime
from threading import Timer, Lock

import six

from gateway.daemon_thread import DaemonThread, DaemonThreadWait
from gateway.dto import GroupActionDTO, InputDTO, OutputDTO, PulseCounterDTO, \
    SensorDTO, ShutterDTO, ShutterGroupDTO, ThermostatDTO, ModuleDTO
from gateway.enums import ShutterEnums
from gateway.hal.mappers_classic import GroupActionMapper, InputMapper, \
    OutputMapper, PulseCounterMapper, SensorMapper, ShutterGroupMapper, \
    ShutterMapper, ThermostatMapper
from gateway.hal.master_controller import CommunicationFailure, \
    MasterController
from gateway.hal.master_event import MasterEvent
from ioc import INJECTED, Inject
from master.classic import eeprom_models, master_api
from master.classic.eeprom_controller import EepromAddress, EepromController
from master.classic.eeprom_models import CanLedConfiguration, \
    CoolingPumpGroupConfiguration, DimmerConfiguration, \
    GlobalRTD10Configuration, GlobalThermostatConfiguration, \
    PumpGroupConfiguration, RTD10CoolingConfiguration, \
    RTD10HeatingConfiguration, ScheduledActionConfiguration, \
    StartupActionConfiguration
from master.classic.inputs import InputStatus
from master.classic.master_communicator import BackgroundConsumer, \
    MasterCommunicator, MasterUnavailable
from master.classic.slave_updater import bootload_modules
from master.classic.validationbits import ValidationBitStatus
from serial_utils import CommunicationTimedOutException
from toolbox import Toolbox

if False:  # MYPY
    from typing import Any, Dict, List, Optional, Tuple
    from serial import Serial
    from gateway.config import ConfigurationController

logger = logging.getLogger("openmotics")


def communication_enabled(f):
    @functools.wraps(f)
    def wrapper(instance, *args, **kwargs):
        if not instance._communication_enabled:
            raise MasterUnavailable()
        return f(instance, *args, **kwargs)
    return wrapper


class MasterClassicController(MasterController):

    @Inject
    def __init__(self,
                 master_communicator=INJECTED,
                 configuration_controller=INJECTED,
                 eeprom_controller=INJECTED):
        # type: (MasterCommunicator, ConfigurationController, EepromController) -> None
        super(MasterClassicController, self).__init__(master_communicator)
        self._master_communicator = master_communicator  # type: MasterCommunicator
        self._config_controller = configuration_controller
        self._eeprom_controller = eeprom_controller
        self._plugin_controller = None  # type: Optional[Any]

        self._input_status = InputStatus(on_input_change=self._input_changed)
        self._validation_bits = ValidationBitStatus(on_validation_bit_change=self._validation_bit_changed)
        self._settings_last_updated = 0.0
        self._time_last_updated = 0.0
        self._synchronization_thread = DaemonThread(name='MasterClassicController synchronization',
                                                    target=self._synchronize,
                                                    interval=30, delay=10)
        self._master_version = None
        self._master_online = False
        self._communication_enabled = True
        self._input_interval = 300
        self._input_last_updated = 0.0
        self._input_config = {}  # type: Dict[int, InputDTO]
        self._output_config = {}  # type: Dict[int, OutputDTO]
        self._shutters_interval = 600
        self._shutters_last_updated = 0.0
        self._shutter_config = {}  # type: Dict[int, ShutterDTO]
        self._validation_bits_interval = 1800
        self._validation_bits_last_updated = 0.0

        self._discover_mode_timer = None  # type: Optional[Timer]
        self._module_log = []  # type: List[Dict[str, Any]]

        self._master_communicator.register_consumer(
            BackgroundConsumer(master_api.output_list(), 0, self._on_master_output_event, True)
        )
        self._master_communicator.register_consumer(
            BackgroundConsumer(master_api.module_initialize(), 0, self._process_module_initialize_message)
        )
        self._module_log_lock = Lock()

    #################
    # Private stuff #
    #################

    def _synchronize(self):
        # type: () -> None
        try:
            if not self._communication_enabled:
                logger.info('synchronization, skipped')
                return

            now = time.time()
            self._get_master_version()
            # Validate communicator checks
            if self._time_last_updated < now - 300:
                self._check_master_time()
                self._time_last_updated = now
            if self._settings_last_updated < now - 900:
                self._check_master_settings()
                self._settings_last_updated = now
            # Refresh if required
            if self._validation_bits_last_updated + self._validation_bits_interval < now:
                self._refresh_validation_bits()
            if self._input_last_updated + self._input_interval < now:
                self._refresh_inputs()
            if self._shutters_last_updated + self._shutters_interval < now:
                self._refresh_shutter_states()
        except CommunicationTimedOutException:
            logger.error('Got communication timeout during synchronization, waiting 10 seconds.')
            self._master_online = False
            raise DaemonThreadWait
        except CommunicationFailure:
            # This is an expected situation
            self._master_online = False
            raise DaemonThreadWait

    def _get_master_version(self):
        # type: () -> None
        initialize = self._master_version is None
        self._master_version = self.get_firmware_version()
        self._master_online = True
        if initialize:
            self._register_version_depending_background_consumers()

    def _register_version_depending_background_consumers(self):
        self._master_communicator.register_consumer(
            BackgroundConsumer(master_api.event_triggered(self._master_version), 0,
                               self._on_master_event, True)
        )
        self._master_communicator.register_consumer(
            BackgroundConsumer(master_api.input_list(self._master_version), 0,
                               self._on_master_input_change)
        )
        self._master_communicator.register_consumer(
            BackgroundConsumer(master_api.shutter_status(self._master_version), 0,
                               self._on_master_shutter_change)
        )

    @communication_enabled
    def _check_master_time(self):
        # type: () -> None
        """
        Validates the master's time with the Gateway time
        """
        status = self._master_communicator.do_command(master_api.status())
        master_time = datetime(1, 1, 1, status['hours'], status['minutes'], status['seconds'])

        now = datetime.now()
        expected_weekday = now.weekday() + 1
        expected_time = now.replace(year=1, month=1, day=1, microsecond=0)

        sync = False
        if abs((master_time - expected_time).total_seconds()) > 180:  # Allow 3 minutes difference
            sync = True
        if status['weekday'] != expected_weekday:
            sync = True

        if sync is True:
            logger.info('Time - master: {0} ({1}) - gateway: {2} ({3})'.format(
                master_time, status['weekday'], expected_time, expected_weekday)
            )
            if expected_time.hour == 0 and expected_time.minute < 15:
                logger.info('Skip setting time between 00:00 and 00:15')
            else:
                self.sync_time()

    @communication_enabled
    def _check_master_settings(self):
        # type: () -> None
        """
        Checks master settings such as:
        * Enable async messages
        * Enable multi-tenancy
        * Enable 32 thermostats
        * Turn on all leds
        """
        eeprom_data = self._master_communicator.do_command(master_api.eeprom_list(),
                                                           {'bank': 0})['data']
        write = False

        if eeprom_data[11] != chr(255):
            logger.info('Disabling async RO messages.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 11, 'data': chr(255)}
            )
            write = True

        if eeprom_data[18] != chr(0):
            logger.info('Enabling async OL messages.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 18, 'data': chr(0)}
            )
            write = True

        if eeprom_data[20] != chr(0):
            logger.info('Enabling async IL messages.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 20, 'data': chr(0)}
            )
            write = True

        if eeprom_data[28] != chr(0):
            logger.info('Enabling async SO messages.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 28, 'data': chr(0)}
            )
            write = True

        thermostat_mode = ord(eeprom_data[14])
        if thermostat_mode & 64 == 0:
            logger.info('Enabling multi-tenant thermostats.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 14, 'data': chr(thermostat_mode | 64)}
            )
            write = True

        if eeprom_data[59] != chr(32):
            logger.info('Enabling 32 thermostats.')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 59, 'data': chr(32)}
            )
            write = True

        if eeprom_data[24] != chr(0):
            logger.info('Disable auto-reset thermostat setpoint')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 24, 'data': chr(0)}
            )
            write = True

        if eeprom_data[13] != chr(0):
            logger.info('Configure master startup mode to: API')
            self._master_communicator.do_command(
                master_api.write_eeprom(),
                {'bank': 0, 'address': 13, 'data': chr(0)}
            )
            write = True

        if write:
            self._master_communicator.do_command(master_api.activate_eeprom(), {'eep': 0})
        self.set_status_leds(True)

    def _on_master_event(self, event_data):  # type: (Dict[str, Any]) -> None
        """ Handle an event triggered by the master. """
        event_type = event_data.get('event_type', 0)
        if event_type == 0:  # None or 0 are both event_type for 'code'
            code = event_data['bytes'][0]
            if self._plugin_controller is not None:
                self._plugin_controller.process_event(code)
        elif event_type == 1:
            bit_nr = int(event_data['bytes'][0])
            value = bool(event_data['bytes'][1])
            self._on_master_validation_bit_change(bit_nr, value)
        else:
            logger.warning('Received unknown master event: {0}'.format(event_data))

    def _on_master_output_event(self, data):
        # type: (Dict[str,Any]) -> None
        """ Triggers when the master informs us of an Output state change """
        # Publish status of all outputs. Since the event from the master contains
        # all outputs that are currently on, the output(s) that changed can't be
        # determined here.
        state = {k: (False, 0) for k, v in self._output_config.items()}
        for output_id, dimmer in data['outputs']:
            state[output_id] = (True, dimmer)
        for output_id, (status, dimmer) in state.items():
            event_data = {'id': output_id, 'status': status, 'dimmer': dimmer}
            self._publish_event(MasterEvent(event_type=MasterEvent.Types.OUTPUT_STATUS, data=event_data))

    #######################
    # Internal management #
    #######################

    def start(self):
        # type: () -> None
        super(MasterClassicController, self).start()
        self._synchronization_thread.start()

    def stop(self):
        # type: () -> None
        self._synchronization_thread.stop()
        super(MasterClassicController, self).stop()

    def set_plugin_controller(self, plugin_controller):
        """
        Set the plugin controller.
        :param plugin_controller: Plugin controller
        :type plugin_controller: plugins.base.PluginController
        """
        self._plugin_controller = plugin_controller

    ##############
    # Public API #
    ##############

    def invalidate_caches(self):
        # type: () -> None
        self._eeprom_controller.invalidate_cache()  # Eeprom can be changed in maintenance mode.
        self._eeprom_controller.dirty = True
        self._input_last_updated = 0.0
        self._shutters_last_updated = 0.0
        self._synchronization_thread.request_single_run()

    def get_master_online(self):
        # type: () -> bool
        return self._master_online

    @communication_enabled
    def get_firmware_version(self):
        out_dict = self._master_communicator.do_command(master_api.status())
        return int(out_dict['f1']), int(out_dict['f2']), int(out_dict['f3'])

    # Memory (eeprom/fram)

    @communication_enabled
    def eeprom_read_page(self, page):
        # TODO: Use eeprom controller
        return self._master_communicator.do_command(master_api.eeprom_list(), {'bank': page})['data']

    def fram_read_page(self, page):
        raise NotImplementedError('A classic master does not support FRAM')

    # Input

    @communication_enabled
    def get_input_module_type(self, input_module_id):
        o = self._eeprom_controller.read(eeprom_models.InputConfiguration, input_module_id * 8, ['module_type'])
        return o.module_type

    def get_inputs_with_status(self):
        # type: () -> List[Dict[str,Any]]
        return self._input_status.get_inputs()

    def get_recent_inputs(self):
        # type: () -> List[int]
        return self._input_status.get_recent()

    @communication_enabled
    def load_input(self, input_id):  # type: (int) -> InputDTO
        classic_object = self._eeprom_controller.read(eeprom_models.InputConfiguration, input_id)
        if classic_object.module_type not in ['i', 'I']:  # Only return 'real' inputs
            raise TypeError('The given id {0} is not an input, but {1}'.format(input_id, classic_object.module_type))
        return InputMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_inputs(self):  # type: () -> List[InputDTO]
        return [InputMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.InputConfiguration)
                if o.module_type in ['i', 'I']]  # Only return 'real' inputs

    @communication_enabled
    def save_inputs(self, inputs):  # type: (List[Tuple[InputDTO, List[str]]]) -> None
        batch = []
        for input_, fields in inputs:
            batch.append(InputMapper.dto_to_orm(input_, fields))
        self._eeprom_controller.write_batch(batch)

    @communication_enabled
    def _refresh_inputs(self):  # type: () -> None
        # 1. refresh input configuration
        self._input_config = {input_configuration.id: input_configuration
                              for input_configuration in self.load_inputs()}
        # 2. poll for latest input status
        try:
            number_of_input_modules = self._master_communicator.do_command(master_api.number_of_io_modules())['in']
            inputs = []
            for i in range(number_of_input_modules):
                # we could be dealing with e.g. a temperature module, skip those
                module_type = self.get_input_module_type(i)
                if module_type not in ['i', 'I']:
                    continue
                result = self._master_communicator.do_command(master_api.read_input_module(self._master_version), {'input_module_nr': i})
                module_status = result['input_status']
                # module_status byte contains bits for each individual input, use mask and bitshift to get status
                for n in range(8):
                    input_nr = i * 8 + n
                    input_status = module_status & (1 << n) != 0
                    data = {'input': input_nr, 'status': input_status}
                    inputs.append(data)
            self._input_status.full_update(inputs)
        except NotImplementedError as e:
            logger.error('Cannot refresh inputs: {}'.format(e))
        self._input_last_updated = time.time()

    def _on_master_input_change(self, data):
        # type: (Dict[str,Any]) -> None
        """ Triggers when the master informs us of an Input state change """
        # Update status tracker
        self._input_status.set_input(data)

    # Outputs

    @communication_enabled
    def set_output(self, output_id, state, dimmer=None, timer=None):
        if output_id is None or output_id < 0 or output_id > 240:
            raise ValueError('Output ID {0} not in range 0 <= id <= 240'.format(output_id))
        if dimmer is not None and dimmer < 0 or dimmer > 100:
            raise ValueError('Dimmer value {0} not in [0, 100]'.format(dimmer))
        if timer is not None and timer not in [150, 450, 900, 1500, 2220, 3120]:
            raise ValueError('Timer value {0} not in [150, 450, 900, 1500, 2220, 3120]'.format(timer))

        if dimmer is not None:
            master_version = self.get_firmware_version()
            if master_version >= (3, 143, 79):
                dimmer = int(0.63 * dimmer)
                self._master_communicator.do_command(
                    master_api.write_dimmer(),
                    {'output_nr': output_id, 'dimmer_value': dimmer}
                )
            else:
                dimmer = int(dimmer) / 10 * 10
                if dimmer == 0:
                    dimmer_action = master_api.BA_DIMMER_MIN
                elif dimmer == 100:
                    dimmer_action = master_api.BA_DIMMER_MAX
                else:
                    dimmer_action = getattr(master_api, 'BA_LIGHT_ON_DIMMER_{0}'.format(dimmer))
                self._master_communicator.do_command(
                    master_api.basic_action(),
                    {'action_type': dimmer_action, 'action_number': output_id}
                )

        if not state:
            self._master_communicator.do_command(
                master_api.basic_action(),
                {'action_type': master_api.BA_LIGHT_OFF, 'action_number': output_id}
            )
            return

        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_LIGHT_ON, 'action_number': output_id}
        )

        if timer is not None:
            timer_action = getattr(master_api, 'BA_LIGHT_ON_TIMER_{0}_OVERRULE'.format(timer))
            self._master_communicator.do_command(
                master_api.basic_action(),
                {'action_type': timer_action, 'action_number': output_id}
            )

    @communication_enabled
    def toggle_output(self, output_id):
        if output_id is None or output_id < 0 or output_id > 240:
            raise ValueError('Output ID {0} not in range 0 <= id <= 240'.format(output_id))

        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_LIGHT_TOGGLE, 'action_number': output_id}
        )

    @communication_enabled
    def load_output(self, output_id):  # type: (int) -> OutputDTO
        classic_object = self._eeprom_controller.read(eeprom_models.OutputConfiguration, output_id)
        output_dto = OutputMapper.orm_to_dto(classic_object)
        self._output_config[output_id] = output_dto
        return output_dto

    @communication_enabled
    def load_outputs(self):  # type: () -> List[OutputDTO]
        output_dtos = [OutputMapper.orm_to_dto(o)
                       for o in self._eeprom_controller.read_all(eeprom_models.OutputConfiguration)]
        self._output_config = {output_dto.id: output_dto for output_dto in output_dtos}
        return output_dtos

    @communication_enabled
    def save_outputs(self, outputs):  # type: (List[Tuple[OutputDTO, List[str]]]) -> None
        batch = []
        for output, fields in outputs:
            batch.append(OutputMapper.dto_to_orm(output, fields))
        self._eeprom_controller.write_batch(batch)
        for output, _ in outputs:
            if output.timer is not None:
                self._master_communicator.do_command(
                    master_api.write_timer(),
                    {'id': output.id, 'timer': output.timer}
                )

    @communication_enabled
    def load_output_status(self):
        # type: () -> List[Dict[str,Any]]
        number_of_outputs = self._master_communicator.do_command(master_api.number_of_io_modules())['out'] * 8
        output_status = []
        for i in range(number_of_outputs):
            state_data = self._master_communicator.do_command(master_api.read_output(), {'id': i})
            # Add output locked status via the validation bits
            state_data['locked'] = self._is_output_locked(state_data['id'])
            output_status.append(state_data)
        return output_status

    def _input_changed(self, input_id, status):
        # type: (int, str) -> None
        """ Executed by the Input Status tracker when an input changed state """
        input_configuration = self._input_config.get(input_id)
        if input_configuration is None:
            # An event was received from an input for which the configuration was not yet loaded. As
            # configuraion should not be loaded inside an event handler, the event is discarded.
            # TODO: Detach input even processing from event handler so it can load the configuration if needed
            return
        event_data = {'id': input_id,
                      'status': status,
                      'location': {'room_id': Toolbox.denonify(input_configuration.room, 255)}}
        self._publish_event(MasterEvent(event_type=MasterEvent.Types.INPUT_CHANGE, data=event_data))

    def _is_output_locked(self, output_id):
        output_dto = self._output_config[output_id]
        if output_dto.lock_bit_id is not None:
            value = self._validation_bits.get_validation_bit(output_dto.lock_bit_id)
            locked = value
        else:
            locked = False
        return locked

    # Shutters

    @communication_enabled
    def shutter_up(self, shutter_id):  # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_SHUTTER_UP, shutter_id)

    @communication_enabled
    def shutter_down(self, shutter_id):  # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_SHUTTER_DOWN, shutter_id)

    @communication_enabled
    def shutter_stop(self, shutter_id):  # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_SHUTTER_STOP, shutter_id)

    @communication_enabled
    def load_shutter(self, shutter_id):  # type: (int) -> ShutterDTO
        classic_object = self._eeprom_controller.read(eeprom_models.ShutterConfiguration, shutter_id)
        return ShutterMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_shutters(self):  # type: () -> List[ShutterDTO]
        return [ShutterMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.ShutterConfiguration)]

    @communication_enabled
    def save_shutters(self, shutters):  # type: (List[Tuple[ShutterDTO, List[str]]]) -> None
        batch = []
        for shutter, fields in shutters:
            batch.append(ShutterMapper.dto_to_orm(shutter, fields))
        self._eeprom_controller.write_batch(batch)

    @communication_enabled
    def _refresh_shutter_states(self):
        self._shutter_config = {shutter.id: shutter for shutter in self.load_shutters()}
        number_of_shutter_modules = self._master_communicator.do_command(master_api.number_of_io_modules())['shutter']
        for module_id in range(number_of_shutter_modules):
            self._update_from_master_state(
                {'module_nr': module_id,
                 'status': self._master_communicator.do_command(master_api.shutter_status(self._master_version),
                                                                {'module_nr': module_id})['status']}
            )
        self._shutters_last_updated = time.time()

    def _on_master_shutter_change(self, data):
        self._update_from_master_state(data)

    def _update_from_master_state(self, data):
        """
        Called with Master event information.
        """
        module_id = data['module_nr']
        new_state = self._interprete_output_states(module_id, data['status'])
        if new_state is None:
            return  # Failsafe for master event handler
        for i in range(4):
            shutter_id = module_id * 4 + i
            event_data = {'id': shutter_id,
                          'status': new_state[i],
                          'location': {'room_id': self._shutter_config[shutter_id].room}}
            self._publish_event(MasterEvent(event_type=MasterEvent.Types.SHUTTER_CHANGE, data=event_data))

    def _interprete_output_states(self, module_id, output_states):
        states = []
        for i in range(4):
            shutter_id = module_id * 4 + i
            if shutter_id not in self._shutter_config:
                return  # Failsafe for master event handler

            # first_up = 0 -> output 0 = up, output 1 = down
            # first_up = 1 -> output 0 = down, output 1 = up
            first_up = 0 if self._shutter_config[shutter_id].up_down_config == 0 else 1

            up = (output_states >> (i * 2 + (1 - first_up))) & 0x1
            down = (output_states >> (i * 2 + first_up)) & 0x1

            if up == 1 and down == 0:
                states.append(ShutterEnums.State.GOING_UP)
            elif down == 1 and up == 0:
                states.append(ShutterEnums.State.GOING_DOWN)
            else:  # Both are off or - unlikely - both are on
                states.append(ShutterEnums.State.STOPPED)

        return states

    @communication_enabled
    def shutter_group_up(self, shutter_group_id):  # type: (int) -> None
        if not (0 <= shutter_group_id <= 30):
            raise ValueError('ShutterGroup ID {0} not in range 0 <= id <= 30'.format(shutter_group_id))
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_SHUTTER_GROUP_UP, 'action_number': shutter_group_id}
        )

    @communication_enabled
    def shutter_group_down(self, shutter_group_id):  # type: (int) -> None
        if not (0 <= shutter_group_id <= 30):
            raise ValueError('ShutterGroup ID {0} not in range 0 <= id <= 30'.format(shutter_group_id))
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_SHUTTER_GROUP_DOWN, 'action_number': shutter_group_id}
        )

    @communication_enabled
    def shutter_group_stop(self, shutter_group_id):  # type: (int) -> None
        if not (0 <= shutter_group_id <= 30):
            raise ValueError('ShutterGroup ID {0} not in range 0 <= id <= 30'.format(shutter_group_id))
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_SHUTTER_GROUP_STOP, 'action_number': shutter_group_id}
        )

    @communication_enabled
    def load_shutter_group(self, shutter_group_id):  # type: (int) -> ShutterGroupDTO
        classic_object = self._eeprom_controller.read(eeprom_models.ShutterGroupConfiguration, shutter_group_id)
        return ShutterGroupMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_shutter_groups(self):  # type: () -> List[ShutterGroupDTO]
        return [ShutterGroupMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.ShutterGroupConfiguration)]

    @communication_enabled
    def save_shutter_groups(self, shutter_groups):  # type: (List[Tuple[ShutterGroupDTO, List[str]]]) -> None
        batch = []
        for shutter_group, fields in shutter_groups:
            batch.append(ShutterGroupMapper.dto_to_orm(shutter_group, fields))
        self._eeprom_controller.write_batch(batch)

    # Thermostats

    @communication_enabled
    def set_thermostat_mode(self, mode):
        # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_THERMOSTAT_MODE, mode)

    @communication_enabled
    def set_thermostat_cooling_heating(self, mode):
        # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_THERMOSTAT_COOLING_HEATING, mode)

    @communication_enabled
    def set_thermostat_automatic(self, action_number):
        # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_THERMOSTAT_AUTOMATIC, action_number)

    @communication_enabled
    def set_thermostat_all_setpoints(self, setpoint):
        # type: (int) -> None
        self._master_communicator.do_basic_action(
            getattr(master_api, 'BA_ALL_SETPOINT_{0}'.format(setpoint)), 0
        )

    @communication_enabled
    def set_thermostat_setpoint(self, thermostat_id, setpoint):
        # type: (int, int) -> None
        self._master_communicator.do_basic_action(
            getattr(master_api, 'BA_ONE_SETPOINT_{0}'.format(setpoint)), thermostat_id
        )

    @communication_enabled
    def write_thermostat_setpoint(self, thermostat_id, temperature):
        # type: (int, float) -> None
        self._master_communicator.do_command(
            master_api.write_setpoint(),
            {'thermostat': thermostat_id,
             'config': 0,
             'temp': master_api.Svt.temp(temperature)}
        )

    @communication_enabled
    def set_thermostat_tenant_auto(self, thermostat_id):
        # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_THERMOSTAT_TENANT_AUTO, thermostat_id)

    @communication_enabled
    def set_thermostat_tenant_manual(self, thermostat_id):
        # type: (int) -> None
        self._master_communicator.do_basic_action(master_api.BA_THERMOSTAT_TENANT_MANUAL, thermostat_id)

    @communication_enabled
    def get_thermostats(self):
        # type: () -> Dict[str,Any]
        return self._master_communicator.do_command(master_api.thermostat_list())

    @communication_enabled
    def get_thermostat_modes(self):
        # type: () -> Dict[str,Any]
        return self._master_communicator.do_command(master_api.thermostat_mode_list())

    @communication_enabled
    def read_airco_status_bits(self):
        # type: () -> Dict[str,Any]
        return self._master_communicator.do_command(master_api.read_airco_status_bits())

    @communication_enabled
    def set_airco_status_bits(self, status_bits):
        # type: (int) -> None
        self._master_communicator.do_basic_action(
            master_api.BA_THERMOSTAT_AIRCO_STATUS, status_bits
        )

    @communication_enabled
    def load_heating_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        classic_object = self._eeprom_controller.read(eeprom_models.ThermostatConfiguration, thermostat_id)
        return ThermostatMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_heating_thermostats(self):  # type: () -> List[ThermostatDTO]
        return [ThermostatMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.ThermostatConfiguration)]

    @communication_enabled
    def save_heating_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        batch = []
        for thermostat, fields in thermostats:
            batch.append(ThermostatMapper.dto_to_orm(thermostat, fields))
        self._eeprom_controller.write_batch(batch)

    @communication_enabled
    def load_cooling_thermostat(self, thermostat_id):  # type: (int) -> ThermostatDTO
        classic_object = self._eeprom_controller.read(eeprom_models.CoolingConfiguration, thermostat_id)
        return ThermostatMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_cooling_thermostats(self):  # type: () -> List[ThermostatDTO]
        return [ThermostatMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.CoolingConfiguration)]

    @communication_enabled
    def save_cooling_thermostats(self, thermostats):  # type: (List[Tuple[ThermostatDTO, List[str]]]) -> None
        batch = []
        for thermostat, fields in thermostats:
            batch.append(ThermostatMapper.dto_to_orm(thermostat, fields))
        self._eeprom_controller.write_batch(batch)

    @communication_enabled
    def get_cooling_pump_group_configuration(self, pump_group_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(CoolingPumpGroupConfiguration, pump_group_id, fields).serialize()

    @communication_enabled
    def get_cooling_pump_group_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(CoolingPumpGroupConfiguration, fields)]

    @communication_enabled
    def set_cooling_pump_group_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(CoolingPumpGroupConfiguration.deserialize(config))

    @communication_enabled
    def set_cooling_pump_group_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([CoolingPumpGroupConfiguration.deserialize(o) for o in config])

    @communication_enabled
    def get_global_rtd10_configuration(self, fields=None):
        # type: (Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(GlobalRTD10Configuration, fields=fields).serialize()

    @communication_enabled
    def set_global_rtd10_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(GlobalRTD10Configuration.deserialize(config))

    @communication_enabled
    def get_rtd10_heating_configuration(self, heating_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(RTD10HeatingConfiguration, heating_id, fields).serialize()

    @communication_enabled
    def get_rtd10_heating_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(RTD10HeatingConfiguration, fields)]

    @communication_enabled
    def set_rtd10_heating_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(RTD10HeatingConfiguration.deserialize(config))

    @communication_enabled
    def set_rtd10_heating_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([RTD10HeatingConfiguration.deserialize(o) for o in config])

    @communication_enabled
    def get_rtd10_cooling_configuration(self, cooling_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(RTD10CoolingConfiguration, cooling_id, fields).serialize()

    @communication_enabled
    def get_rtd10_cooling_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(RTD10CoolingConfiguration, fields)]

    @communication_enabled
    def set_rtd10_cooling_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(RTD10CoolingConfiguration.deserialize(config))

    @communication_enabled
    def set_rtd10_cooling_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([RTD10CoolingConfiguration.deserialize(o) for o in config])

    @communication_enabled
    def get_global_thermostat_configuration(self, fields=None):
        # type: (Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(GlobalThermostatConfiguration, fields=fields).serialize()

    @communication_enabled
    def set_global_thermostat_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        if 'outside_sensor' in config:
            if config['outside_sensor'] == 255:
                config['threshold_temp'] = 50  # Works around a master issue where the thermostat would be turned off in case there is no outside sensor.
        self._eeprom_controller.write(GlobalThermostatConfiguration.deserialize(config))

    @communication_enabled
    def get_pump_group_configuration(self, pump_group_id, fields=None):
        # type: (int, Optional[List[str]]) -> Dict[str,Any]
        return self._eeprom_controller.read(PumpGroupConfiguration, pump_group_id, fields).serialize()

    @communication_enabled
    def get_pump_group_configurations(self, fields=None):
        # type: (Optional[List[str]]) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(PumpGroupConfiguration, fields)]

    @communication_enabled
    def set_pump_group_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(PumpGroupConfiguration.deserialize(config))

    @communication_enabled
    def set_pump_group_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([PumpGroupConfiguration.deserialize(o) for o in config])

    # Virtual modules

    @communication_enabled
    def add_virtual_output_module(self):
        # type: () -> str
        module = self._master_communicator.do_command(master_api.add_virtual_module(), {'vmt': 'o'})
        self._broadcast_module_discovery()
        return module['resp']

    @communication_enabled
    def add_virtual_dim_module(self):
        # type: () -> str
        module = self._master_communicator.do_command(master_api.add_virtual_module(), {'vmt': 'd'})
        self._broadcast_module_discovery()
        return module['resp']

    @communication_enabled
    def add_virtual_input_module(self):
        # type: () -> str
        module = self._master_communicator.do_command(master_api.add_virtual_module(), {'vmt': 'i'})
        self._broadcast_module_discovery()
        return module['resp']

    # Generic

    @communication_enabled
    def get_status(self):
        """ Get the status of the Master.

        :returns: dict with 'time' (HH:MM), 'date' (DD:MM:YYYY), 'mode', 'version' (a.b.c)
                  and 'hw_version' (hardware version)
        """
        out_dict = self._master_communicator.do_command(master_api.status())
        return {'time': '%02d:%02d' % (out_dict['hours'], out_dict['minutes']),
                'date': '%02d/%02d/%d' % (out_dict['day'], out_dict['month'], out_dict['year']),
                'mode': out_dict['mode'],
                'version': '%d.%d.%d' % (out_dict['f1'], out_dict['f2'], out_dict['f3']),
                'hw_version': out_dict['h']}

    @communication_enabled
    def get_modules(self):
        """ Get a list of all modules attached and registered with the master.

        :returns: Dict with:
        * 'outputs' (list of module types: O,R,D),
        * 'inputs' (list of input module types: I,T,L,C)
        * 'shutters' (List of modules types: S).
        """
        mods = self._master_communicator.do_command(master_api.number_of_io_modules())

        inputs = []
        outputs = []
        shutters = []
        can_inputs = []

        for i in range(mods['in']):
            ret = self._master_communicator.do_command(
                master_api.read_eeprom(),
                {'bank': 2 + i, 'addr': 252, 'num': 1}
            )
            is_can = ret['data'][0] == 'C'
            ret = self._master_communicator.do_command(
                master_api.read_eeprom(),
                {'bank': 2 + i, 'addr': 0, 'num': 1}
            )
            if is_can:
                can_inputs.append(ret['data'][0])
            else:
                inputs.append(ret['data'][0])

        for i in range(mods['out']):
            ret = self._master_communicator.do_command(
                master_api.read_eeprom(),
                {'bank': 33 + i, 'addr': 0, 'num': 1}
            )
            outputs.append(ret['data'][0])

        for shutter in range(mods['shutter']):
            shutters.append('S')

        if len(can_inputs) > 0 and 'C' not in can_inputs:
            can_inputs.append('C')  # First CAN enabled installations didn't had this in the eeprom yet

        return {'outputs': outputs, 'inputs': inputs, 'shutters': shutters, 'can_inputs': can_inputs}

    @staticmethod
    def _format_address(address_bytes):
        return '{0:03}.{1:03}.{2:03}.{3:03}'.format(ord(address_bytes[0]),
                                                    ord(address_bytes[1]),
                                                    ord(address_bytes[2]),
                                                    ord(address_bytes[3]))

    @communication_enabled
    def get_modules_information(self):  # type: () -> List[ModuleDTO]
        """ Gets module information """

        def get_master_version(_module_address):
            try:
                _module_version = self._master_communicator.do_command(master_api.get_module_version(),
                                                                       {'addr': _module_address.bytes},
                                                                       extended_crc=True,
                                                                       timeout=5)
                _firmware_version = '{0}.{1}.{2}'.format(_module_version['f1'], _module_version['f2'], _module_version['f3'])
                return True, _module_version['hw_version'], _firmware_version
            except CommunicationTimedOutException:
                return False, None, None

        information = []
        module_type_lookup = {'c': ModuleDTO.ModuleType.CAN_CONTROL,
                              't': ModuleDTO.ModuleType.SENSOR,
                              'i': ModuleDTO.ModuleType.INPUT,
                              'o': ModuleDTO.ModuleType.OUTPUT,
                              'r': ModuleDTO.ModuleType.SHUTTER,
                              'd': ModuleDTO.ModuleType.DIM_CONTROL}

        no_modules = self._master_communicator.do_command(master_api.number_of_io_modules())
        for i in range(no_modules['in']):
            is_can = self._eeprom_controller.read_address(EepromAddress(2 + i, 252, 1)).bytes == 'C'
            module_address = self._eeprom_controller.read_address(EepromAddress(2 + i, 0, 4))
            module_type_letter = module_address.bytes[0].lower()
            is_virtual = module_address.bytes[0].islower()
            formatted_address = MasterClassicController._format_address(module_address.bytes)
            hardware_type = ModuleDTO.HardwareType.PHYSICAL
            if is_virtual:
                hardware_type = ModuleDTO.HardwareType.VIRTUAL
            elif is_can and module_type_letter != 'c':
                hardware_type = ModuleDTO.HardwareType.EMULATED
            dto = ModuleDTO(source=ModuleDTO.Source.MASTER,
                            address=formatted_address,
                            module_type=module_type_lookup.get(module_type_letter),
                            hardware_type=hardware_type,
                            order=i)
            if hardware_type == ModuleDTO.HardwareType.PHYSICAL:
                dto.online, dto.hardware_version, dto.firmware_version = get_master_version(module_address)
            information.append(dto)

        for i in range(no_modules['out']):
            module_address = self._eeprom_controller.read_address(EepromAddress(33 + i, 0, 4))
            module_type_letter = module_address.bytes[0].lower()
            is_virtual = module_address.bytes[0].islower()
            formatted_address = MasterClassicController._format_address(module_address.bytes)
            dto = ModuleDTO(source=ModuleDTO.Source.MASTER,
                            address=formatted_address,
                            module_type=module_type_lookup.get(module_type_letter),
                            hardware_type=(ModuleDTO.HardwareType.VIRTUAL if is_virtual else
                                           ModuleDTO.HardwareType.PHYSICAL),
                            order=i)
            if not is_virtual:
                dto.online, dto.hardware_version, dto.firmware_version = get_master_version(module_address)
            information.append(dto)

        for i in range(no_modules['shutter']):
            module_address = self._eeprom_controller.read_address(EepromAddress(33 + i, 173, 4))
            module_type_letter = module_address.bytes[0].lower()
            is_virtual = module_address.bytes[0].islower()
            formatted_address = MasterClassicController._format_address(module_address.bytes)
            dto = ModuleDTO(source=ModuleDTO.Source.MASTER,
                            address=formatted_address,
                            module_type=module_type_lookup.get(module_type_letter),
                            hardware_type=(ModuleDTO.HardwareType.VIRTUAL if is_virtual else
                                           ModuleDTO.HardwareType.PHYSICAL),
                            order=i)
            if not is_virtual:
                dto.online, dto.hardware_version, dto.firmware_version = get_master_version(module_address)
            information.append(dto)

        return information

    def replace_module(self, old_address, new_address):  # type: (str, str) -> None
        old_address_bytes = ''.join(chr(int(part)) for part in old_address.split('.'))
        new_address_bytes = ''.join(chr(int(part)) for part in new_address.split('.'))
        no_modules = self._master_communicator.do_command(master_api.number_of_io_modules())

        amount_of_inputs = no_modules['in']
        for i in range(amount_of_inputs):
            eeprom_address = EepromAddress(2 + i, 0, 4)
            module_address = self._eeprom_controller.read_address(eeprom_address).bytes
            if module_address == old_address_bytes:
                new_module_address = self._eeprom_controller.read_address(EepromAddress(2 + amount_of_inputs - 1, 0, 4)).bytes
                if new_module_address == new_address_bytes:
                    self._eeprom_controller.write_address(eeprom_address, new_address_bytes)
                    self._eeprom_controller.write_address(EepromAddress(0, 1, 1), chr(amount_of_inputs - 1))
                    self._eeprom_controller.activate()
                    logger.warn('Replaced {0} by {1}'.format(old_address, new_address))
                    return

        amount_of_outputs = no_modules['out']
        for i in range(amount_of_outputs):
            eeprom_address = EepromAddress(33 + i, 0, 4)
            module_address = self._eeprom_controller.read_address(eeprom_address).bytes
            if module_address == old_address_bytes:
                new_module_address = self._eeprom_controller.read_address(EepromAddress(33 + amount_of_outputs - 1, 0, 4)).bytes
                if new_module_address == new_address_bytes:
                    self._eeprom_controller.write_address(eeprom_address, new_address_bytes)
                    self._eeprom_controller.write_address(EepromAddress(0, 2, 1), chr(amount_of_outputs - 1))
                    self._eeprom_controller.activate()
                    logger.warn('Replaced {0} by {1}'.format(old_address, new_address))
                    return

        amount_of_shutters = no_modules['shutter']
        for i in range(amount_of_shutters):
            eeprom_address = EepromAddress(33 + i, 173, 4)
            module_address = self._eeprom_controller.read_address(eeprom_address).bytes
            if module_address == old_address_bytes:
                new_module_address = self._eeprom_controller.read_address(EepromAddress(33 + amount_of_shutters - 1, 173, 4)).bytes
                if new_module_address == new_address_bytes:
                    self._eeprom_controller.write_address(eeprom_address, new_address_bytes)
                    self._eeprom_controller.write_address(EepromAddress(0, 3, 1), chr(amount_of_shutters - 1))
                    self._eeprom_controller.activate()
                    logger.warn('Replaced {0} by {1}'.format(old_address, new_address))
                    return

        raise RuntimeError('Could not correctly match modules {0} and {1}'.format(old_address, new_address))

    @communication_enabled
    def flash_leds(self, led_type, led_id):
        """ Flash the leds on the module for an output/input/sensor.

        :type led_type: byte
        :param led_type: The module type: output/dimmer (0), input (1), sensor/temperatur (2).
        :type led_id: byte
        :param led_id: The id of the output/input/sensor.
        :returns: dict with 'status' ('OK').
        """
        ret = self._master_communicator.do_command(master_api.indicate(),
                                                   {'type': led_type, 'id': led_id})
        return {'status': ret['resp']}

    @communication_enabled
    def get_backup(self):
        """
        Get a backup of the eeprom of the master.

        :returns: String of bytes (size = 64kb).
        """
        retry = None
        output = ""
        bank = 0
        while bank < 256:
            try:
                output += self._master_communicator.do_command(
                    master_api.eeprom_list(),
                    {'bank': bank}
                )['data']
                bank += 1
            except CommunicationTimedOutException:
                if retry == bank:
                    raise
                retry = bank
                logger.warning('Got timeout reading bank {0}. Retrying...'.format(bank))
                time.sleep(2)  # Doing heavy reads on eeprom can exhaust the master. Give it a bit room to breathe.
        return output

    def factory_reset(self):
        # type: () -> None
        # Wipe master EEPROM
        data = chr(255) * (256 * 256)
        self.restore(data)

    def cold_reset(self):
        # type: () -> None
        """
        Perform a cold reset on the master. Turns the power off, waits 5 seconds and turns the power back on.
        """
        self._master_online = False
        MasterClassicController._set_master_power(False)
        time.sleep(5)
        MasterClassicController._set_master_power(True)

    @Inject
    def update_master(self, hex_filename, controller_serial=INJECTED):
        # type: (str, Serial) -> None
        try:
            self._communication_enabled = False
            self._master_online = False
            self._master_communicator.update_mode_start()

            port = controller_serial.port  # type: ignore
            baudrate = str(controller_serial.baudrate)  # type: ignore

            logger.info('Enter bootloader...')
            # Entering the bootloader requires some correct timing. The AN1310 tool is used to enter bootloader for which the
            # tool only tries for 5 seconds. To in these 5 seconds, the master needs to be booted up so it can enter (and
            # stay) into bootloader.
            MasterClassicController._set_master_power(False)
            time.sleep(3)
            process = subprocess.Popen(['/opt/openmotics/bin/AN1310cl', '-d', port, '-b', baudrate, '-a'],
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE)
            MasterClassicController._set_master_power(True)
            process.wait()

            logger.info('Verify bootloader...')
            tries = 10
            found = False
            response = None
            while tries > 0:
                # It might take a few seconds before the bootloader is active
                try:
                    response = subprocess.check_output(['/opt/openmotics/bin/AN1310cl', '-d', port, '-b', baudrate, '-s'])
                    if 'PIC18F67J11' in str(response):
                        found = True
                        break
                except subprocess.CalledProcessError as ex:
                    response = ex.output  # For debugging purposes down below
                    if ex.returncode != 254:
                        raise
                time.sleep(1)
                tries -= 1
            if not found:
                error_message = 'Could not enter bootloader: {0}'.format(str(response))
                logger.error(error_message)
                raise RuntimeError(error_message)

            logger.info('Flashing...')
            try:
                response = subprocess.check_output(['/opt/openmotics/bin/AN1310cl', '-d', port, '-b', baudrate, '-p ', '-c', hex_filename])
                logger.debug(response)
            except subprocess.CalledProcessError as ex:
                logger.info(ex.output)
                raise

            logger.info('Verifying...')
            try:
                response = subprocess.check_output(['/opt/openmotics/bin/AN1310cl', '-d', port, '-b', baudrate, '-v', hex_filename])
                logger.debug(response)
            except subprocess.CalledProcessError as ex:
                logger.info(ex.output)
                raise

            logger.info('Entering application...')
            try:
                response = subprocess.check_output(['/opt/openmotics/bin/AN1310cl', '-d', port, '-b', baudrate, '-r'])
                logger.debug(response)
            except subprocess.CalledProcessError as ex:
                logger.info(ex.output)
                raise
        finally:
            self._master_communicator.update_mode_stop()
            self._communication_enabled = True

    @Inject
    def update_slave_modules(self, module_type, hex_filename, controller_serial=INJECTED):
        # type: (str, str, Serial) -> None
        self._communication_enabled = False
        try:
            bootload_modules(module_type, hex_filename)
        finally:
            self._communication_enabled = True

    @staticmethod
    def _set_master_power(on):
        with open('/sys/class/gpio/gpio44/direction', 'w') as gpio:
            gpio.write('out')
        with open('/sys/class/gpio/gpio44/value', 'w') as gpio:
            gpio.write('1' if on else '0')

    @communication_enabled
    def reset(self):
        """ Reset the master.

        :returns: emtpy dict.
        """
        self._master_communicator.do_command(master_api.reset())
        return dict()

    def power_cycle_master(self):
        self.cold_reset()
        return dict()

    @communication_enabled
    def power_cycle_bus(self):
        """ Turns the power of both bussed off for 5 seconds """
        self._master_communicator.do_basic_action(master_api.BA_POWER_CYCLE_BUS, 0)

    @communication_enabled
    def restore(self, data):
        """
        Restore a backup of the eeprom of the master.

        :param data: The eeprom backup to restore.
        :type data: string of bytes (size = 64 kb).
        :returns: dict with 'output' key (contains an array with the addresses that were written).
        """
        ret = []
        (num_banks, bank_size, write_size) = (256, 256, 10)

        for bank in range(0, num_banks):
            read = self._master_communicator.do_command(master_api.eeprom_list(),
                                                        {'bank': bank})['data']
            for addr in range(0, bank_size, write_size):
                orig = read[addr:addr + write_size]
                new = data[bank * bank_size + addr: bank * bank_size + addr + len(orig)]
                if new != orig:
                    ret.append('B' + str(bank) + 'A' + str(addr))

                    self._master_communicator.do_command(
                        master_api.write_eeprom(),
                        {'bank': bank, 'address': addr, 'data': new}
                    )

        self._master_communicator.do_command(master_api.activate_eeprom(), {'eep': 0})
        ret.append('Activated eeprom')
        self._eeprom_controller.invalidate_cache()

        return {'output': ret}

    @communication_enabled
    def sync_time(self):
        # type: () -> None
        logger.info('Setting the time on the master.')
        now = datetime.now()
        self._master_communicator.do_command(
            master_api.set_time(),
            {'sec': now.second, 'min': now.minute, 'hours': now.hour,
             'weekday': now.isoweekday(), 'day': now.day, 'month': now.month,
             'year': now.year % 100}
        )

    def get_configuration_dirty_flag(self):
        # type: () -> bool
        dirty = self._eeprom_controller.dirty
        # FIXME: this assumes a full sync will finish after this is called eg.
        # a response timeout clears the dirty state while no sync would started
        # on the remote side.
        self._eeprom_controller.dirty = False
        return dirty

    # Module functions

    def _process_module_initialize_message(self, api_data):
        # type: (Dict[str, Any]) -> None
        """
        Create a log entry when the MI message is received.
        > {'instr': 'E', 'module_nr': 0, 'io_type': 2, 'padding': '', 'literal': '', 'data': 1, 'id': 'I@7%'}
        """
        try:
            code_map = {'N': 'New',
                        'E': 'Existing',
                        'D': 'Duplicate'}
            category_map = {0: 'SHUTTER',
                            1: 'OUTPUT',
                            2: 'INPUT'}
            address = MasterClassicController._format_address(api_data['id'])
            with self._module_log_lock:
                self._module_log.append({'code': code_map.get(api_data['instr'], 'UNKNOWN').upper(),
                                         'module_nr': api_data['module_nr'],
                                         'category': category_map[api_data['io_type']],
                                         'module_type': api_data['id'][0],
                                         'address': address})
            logger.info('Initialize/discovery - {0} module found: {1} ({2})'.format(
                code_map.get(api_data['instr'], 'Unknown'),
                api_data['id'][0],
                address
            ))
        except Exception:
            logger.exception('Could not process initialization message')

    @communication_enabled
    def module_discover_start(self, timeout):  # type: (int) -> None
        def _stop(): self.module_discover_stop()

        self._master_communicator.do_command(master_api.module_discover_start())

        if self._discover_mode_timer is not None:
            self._discover_mode_timer.cancel()
        self._discover_mode_timer = Timer(timeout, _stop)
        self._discover_mode_timer.start()

        with self._module_log_lock:
            self._module_log = []

    @communication_enabled
    def module_discover_stop(self):  # type: () -> None
        if self._discover_mode_timer is not None:
            self._discover_mode_timer.cancel()
            self._discover_mode_timer = None

        self._master_communicator.do_command(master_api.module_discover_stop())
        self._broadcast_module_discovery()

        with self._module_log_lock:
            self._module_log = []

    def module_discover_status(self):  # type: () -> bool
        return self._discover_mode_timer is not None

    def get_module_log(self):  # type: () -> List[Dict[str, Any]]
        with self._module_log_lock:
            (log, self._module_log) = (self._module_log, [])
        return log

    def _broadcast_module_discovery(self):
        # type: () -> None
        self.invalidate_caches()
        self._publish_event(MasterEvent(event_type=MasterEvent.Types.MODULE_DISCOVERY, data={}))

    # Error functions

    @communication_enabled
    def error_list(self):
        """ Get the error list per module (input and output modules). The modules are identified by
        O1, O2, I1, I2, ...

        :returns: dict with 'errors' key, it contains list of tuples (module, nr_errors).
        """
        error_list = self._master_communicator.do_command(master_api.error_list())
        return error_list['errors']

    @communication_enabled
    def last_success(self):
        """ Get the number of seconds since the last successful communication with the master.
        """
        return self._master_communicator.get_seconds_since_last_success()

    @communication_enabled
    def clear_error_list(self):
        """ Clear the number of errors.

        :returns: empty dict.
        """
        self._master_communicator.do_command(master_api.clear_error_list())
        return dict()

    @communication_enabled
    def set_status_leds(self, status):
        """ Set the status of the leds on the master.

        :param status: whether the leds should be on or off.
        :type status: boolean.
        :returns: empty dict.
        """
        on = 1 if status is True else 0
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_STATUS_LEDS, 'action_number': on}
        )
        return dict()

    # (Group)Actions

    @communication_enabled
    def do_basic_action(self, action_type, action_number):  # type: (int, int) -> None
        """
        Execute a basic action.

        :param action_type: The type of the action as defined by the master api.
        :param action_number: The number provided to the basic action, its meaning depends on the action_type.
        """
        if action_type < 0 or action_type > 254:
            raise ValueError('action_type not in [0, 254]: %d' % action_type)

        if action_number < 0 or action_number > 254:
            raise ValueError('action_number not in [0, 254]: %d' % action_number)

        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': action_type,
             'action_number': action_number}
        )

    @communication_enabled
    def do_group_action(self, group_action_id):  # type: (int) -> None
        if group_action_id < 0 or group_action_id > 159:
            raise ValueError('group_action_id not in [0, 160]: %d' % group_action_id)

        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_GROUP_ACTION,
             'action_number': group_action_id}
        )

    @communication_enabled
    def load_group_action(self, group_action_id):  # type: (int) -> GroupActionDTO
        classic_object = self._eeprom_controller.read(eeprom_models.GroupActionConfiguration, group_action_id)
        return GroupActionMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_group_actions(self):  # type: () -> List[GroupActionDTO]
        return [GroupActionMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.GroupActionConfiguration)]

    @communication_enabled
    def save_group_actions(self, group_actions):  # type: (List[Tuple[GroupActionDTO, List[str]]]) -> None
        batch = []
        for group_action, fields in group_actions:
            batch.append(GroupActionMapper.dto_to_orm(group_action, fields))
        self._eeprom_controller.write_batch(batch)

    # Schedules

    @communication_enabled
    def load_scheduled_action_configuration(self, scheduled_action_id, fields=None):
        # type: (int, Any) -> Dict[str,Any]
        return self._eeprom_controller.read(ScheduledActionConfiguration, scheduled_action_id, fields).serialize()

    @communication_enabled
    def load_scheduled_action_configurations(self, fields=None):
        # type: (Any) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(ScheduledActionConfiguration, fields)]

    @communication_enabled
    def save_scheduled_action_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(ScheduledActionConfiguration.deserialize(config))

    @communication_enabled
    def save_scheduled_action_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([ScheduledActionConfiguration.deserialize(o) for o in config])

    @communication_enabled
    def load_startup_action_configuration(self, fields=None):
        # type: (Any) -> Dict[str,Any]
        return self._eeprom_controller.read(StartupActionConfiguration, fields).serialize()

    @communication_enabled
    def save_startup_action_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(StartupActionConfiguration.deserialize(config))

    # Dimmer functions

    @communication_enabled
    def load_dimmer_configuration(self, fields=None):
        # type: (Any) -> Dict[str,Any]
        return self._eeprom_controller.read(DimmerConfiguration, fields).serialize()

    @communication_enabled
    def save_dimmer_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(DimmerConfiguration.deserialize(config))

    # Can Led functions

    @communication_enabled
    def load_can_led_configuration(self, can_led_id, fields=None):
        # type: (int, Any) -> Dict[str,Any]
        return self._eeprom_controller.read(CanLedConfiguration, can_led_id, fields).serialize()

    @communication_enabled
    def load_can_led_configurations(self, fields=None):
        # type: (Any) -> List[Dict[str,Any]]
        return [o.serialize() for o in self._eeprom_controller.read_all(CanLedConfiguration, fields)]

    @communication_enabled
    def save_can_led_configuration(self, config):
        # type: (Dict[str,Any]) -> None
        self._eeprom_controller.write(CanLedConfiguration.deserialize(config))

    @communication_enabled
    def save_can_led_configurations(self, config):
        # type: (List[Dict[str,Any]]) -> None
        self._eeprom_controller.write_batch([CanLedConfiguration.deserialize(o) for o in config])

    # All lights off functions

    @communication_enabled
    def set_all_lights_off(self):
        """ Turn all lights off.

        :returns: empty dict.
        """
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_ALL_LIGHTS_OFF, 'action_number': 0}
        )
        return dict()

    @communication_enabled
    def set_all_lights_floor_off(self, floor):
        """ Turn all lights on a given floor off.

        :returns: empty dict.
        """

        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_LIGHTS_OFF_FLOOR, 'action_number': floor}
        )
        return dict()

    @communication_enabled
    def set_all_lights_floor_on(self, floor):
        """ Turn all lights on a given floor on.

        :returns: empty dict.
        """
        self._master_communicator.do_command(
            master_api.basic_action(),
            {'action_type': master_api.BA_LIGHTS_ON_FLOOR, 'action_number': floor}
        )
        return dict()

    # Sensors

    def get_sensor_temperature(self, sensor_id):
        if sensor_id is None or sensor_id < 0 or sensor_id > 31:
            raise ValueError('Sensor ID {0} not in range 0 <= id <= 31'.format(sensor_id))
        return self.get_sensors_temperature()[sensor_id]

    @communication_enabled
    def get_sensors_temperature(self):
        temperatures = []
        sensor_list = self._master_communicator.do_command(master_api.sensor_temperature_list())
        for i in range(32):
            temperatures.append(sensor_list['tmp{0}'.format(i)].get_temperature())
        return temperatures

    @communication_enabled
    def get_sensor_humidity(self, sensor_id):
        if sensor_id is None or sensor_id < 0 or sensor_id > 31:
            raise ValueError('Sensor ID {0} not in range 0 <= id <= 31'.format(sensor_id))
        return self.get_sensors_humidity()[sensor_id]

    @communication_enabled
    def get_sensors_humidity(self):
        humidities = []
        sensor_list = self._master_communicator.do_command(master_api.sensor_humidity_list())
        for i in range(32):
            humidities.append(sensor_list['hum{0}'.format(i)].get_humidity())
        return humidities

    def get_sensor_brightness(self, sensor_id):
        if sensor_id is None or sensor_id < 0 or sensor_id > 31:
            raise ValueError('Sensor ID {0} not in range 0 <= id <= 31'.format(sensor_id))
        return self.get_sensors_brightness()[sensor_id]

    @communication_enabled
    def get_sensors_brightness(self):
        brightnesses = []
        sensor_list = self._master_communicator.do_command(master_api.sensor_brightness_list())
        for i in range(32):
            brightnesses.append(sensor_list['bri{0}'.format(i)].get_brightness())
        return brightnesses

    @communication_enabled
    def set_virtual_sensor(self, sensor_id, temperature, humidity, brightness):
        if sensor_id is None or sensor_id < 0 or sensor_id > 31:
            raise ValueError('Sensor ID {0} not in range 0 <= id <= 31'.format(sensor_id))

        self._master_communicator.do_command(
            master_api.set_virtual_sensor(),
            {'sensor': sensor_id,
             'tmp': master_api.Svt.temp(temperature),
             'hum': master_api.Svt.humidity(humidity),
             'bri': master_api.Svt.brightness(brightness)}
        )
        return dict()

    @communication_enabled
    def load_sensor(self, sensor_id):  # type: (int) -> SensorDTO
        classic_object = self._eeprom_controller.read(eeprom_models.SensorConfiguration, sensor_id)
        return SensorMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_sensors(self):  # type: () -> List[SensorDTO]
        return [SensorMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.SensorConfiguration)]

    @communication_enabled
    def save_sensors(self, sensors):  # type: (List[Tuple[SensorDTO, List[str]]]) -> None
        batch = []
        for sensor, fields in sensors:
            batch.append(SensorMapper.dto_to_orm(sensor, fields))
        self._eeprom_controller.write_batch(batch)

    # PulseCounters

    @communication_enabled
    def load_pulse_counter(self, pulse_counter_id):  # type: (int) -> PulseCounterDTO
        classic_object = self._eeprom_controller.read(eeprom_models.PulseCounterConfiguration, pulse_counter_id)
        return PulseCounterMapper.orm_to_dto(classic_object)

    @communication_enabled
    def load_pulse_counters(self):  # type: () -> List[PulseCounterDTO]
        return [PulseCounterMapper.orm_to_dto(o)
                for o in self._eeprom_controller.read_all(eeprom_models.PulseCounterConfiguration)]

    @communication_enabled
    def save_pulse_counters(self, pulse_counters):  # type: (List[Tuple[PulseCounterDTO, List[str]]]) -> None
        batch = []
        for pulse_counter, fields in pulse_counters:
            batch.append(PulseCounterMapper.dto_to_orm(pulse_counter, fields))
        self._eeprom_controller.write_batch(batch)

    @communication_enabled
    def get_pulse_counter_values(self):  # type: () -> Dict[int, int]
        out_dict = self._master_communicator.do_command(master_api.pulse_list())
        return {i: out_dict['pv{0}'.format(i)] for i in range(24)}

    # Validation bits

    @communication_enabled
    def load_validation_bits(self):  # type: () -> Optional[Dict[int, bool]]
        if self._master_version is None or self._master_version < (3, 143, 102):
            return None

        number_of_bits = 256
        bytes_per_call = 11

        def load_bits_batch(start_bit):  # type: (int) -> Dict[int, bool]
            batch = {}
            response = self._master_communicator.do_command(master_api.read_user_information(self._master_version),
                                                            {'information_type': 0, 'number': start_bit})
            for byte_index in range(bytes_per_call):
                for bit_index in range(8):
                    bit_nr = start_bit + (byte_index * 8) + bit_index
                    if bit_nr == number_of_bits:
                        return batch  #
                    bit_value = bool(response['data'][byte_index] & (1 << bit_index))
                    batch[bit_nr] = bit_value
            return batch

        bits = {}
        bit_pointer = 0
        while True:
            bits.update(load_bits_batch(bit_pointer))
            bit_pointer = max(*bits.keys()) + 1
            if bit_pointer == 256:
                break
        return bits

    def _refresh_validation_bits(self):
        current_bit_states = self.load_validation_bits()
        if current_bit_states is not None:
            self._validation_bits.full_update(current_bit_states)
        self._validation_bits_last_updated = time.time()

    def _on_master_validation_bit_change(self, bit_nr, value):  # type: (int, bool) -> None
        self._validation_bits.update(bit_nr, value)

    def _validation_bit_changed(self, bit_nr, value):
        # loop over all outputs and update the locked status if the bit_nr is associated with this output
        for output_id, output_dto in six.iteritems(self._output_config):
            if output_dto.lock_bit_id == bit_nr:
                locked = value  # the bit is set, the output is locked
                event_data = {'id': output_id, 'locked': locked}
                self._publish_event(MasterEvent(event_type=MasterEvent.Types.OUTPUT_STATUS, data=event_data))
