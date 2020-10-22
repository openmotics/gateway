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
"""
Module for communicating with the Master
"""
from __future__ import absolute_import

import logging

from gateway.dto import GroupActionDTO, InputDTO, ModuleDTO, OutputDTO, \
    PulseCounterDTO, SensorDTO, ShutterDTO, ShutterGroupDTO, ThermostatDTO
from gateway.hal.master_controller import MasterController

if False:  # MYPY
    from typing import Any, Dict, List, Literal, Optional, Tuple
    from plugins.base import PluginController

logger = logging.getLogger('openmotics')


class MasterCommunicator(object):
    def start(self):
        # type: () -> None
        pass

    def stop(self):
        # type: () -> None
        pass


class MasterDummyController(MasterController):
    def __init__(self):
        # type: () -> None
        super(MasterDummyController, self).__init__(MasterCommunicator())

    def set_plugin_controller(self, plugin_controller):
        # type: (PluginController) -> None
        pass

    def get_communicator_health(self):
        # type: () -> Literal['success']
        return 'success'

    def error_list(self):
        # type: () -> List[Tuple[str,int]]
        return []

    def get_modules_information(self, address=None):
        # type: (Optional[str]) -> List[ModuleDTO]
        if address:
            raise NotImplementedError()
        else:
            return []

    def load_inputs(self):  # type: () -> List[InputDTO]
        return []

    def get_inputs_with_status(self):
        # type: () -> List[Dict[str,Any]]
        return []

    def get_recent_inputs(self):
        # type: () -> List[int]
        return []

    def load_outputs(self):  # type: () -> List[OutputDTO]
        return []

    def load_output_status(self):
        # type: () -> List[Dict[str,Any]]
        return []

    def load_shutters(self):
        # type: () -> List[ShutterDTO]
        return []

    def load_shutter_groups(self):
        # type: () -> List[ShutterGroupDTO]
        return []

    def get_thermostats(self):
        # type: () -> Dict[str,Any]
        return {}

    def get_thermostat_modes(self):
        # type: () -> Dict[str,Any]
        return {}

    def read_airco_status_bits(self):
        # type: () -> Dict[str,Any]
        return {}

    def load_sensors(self):
        # type: () -> List[SensorDTO]
        return []

    def get_sensors_temperature(self):
        # type: () -> List[Optional[float]]
        return []

    def get_sensors_humidity(self):
        # type: () -> List[Optional[float]]
        return []

    def get_sensors_brightness(self):
        # type: () -> List[Optional[float]]
        return []

    def load_pulse_counters(self):
        # type: () -> List[PulseCounterDTO]
        return []

    def get_pulse_counter_values(self):
        # type: () -> Dict[int, int]
        return {}

    def load_group_actions(self):
        # type: () -> List[GroupActionDTO]
        return []
