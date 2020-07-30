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
Module DTO
"""
from gateway.dto.base import BaseDTO

if False:  # MYPY
    from typing import Optional


class ModuleDTO(BaseDTO):

    class Source(object):
        MASTER = 'master'
        GATEWAY = 'gateway'

    class HardwareType(object):
        VIRTUAL = 'virtual'
        PHYSICAL = 'physical'
        EMULATED = 'emulated'

    class ModuleType(object):
        SENSOR = 'sensor'
        INPUT = 'input'
        OUTPUT = 'output'
        SHUTTER = 'shutter'
        DIM_CONTROL = 'dim_control'
        CAN_CONTROL = 'can_control'
        ENERGY = 'energy'
        POWER = 'power'
        P1_CONCENTRATOR = 'p1_concentrator'

    def __init__(self, source, address, module_type, hardware_type, firmware_version=None, hardware_version=None, order=None, online=None):
        self.source = source  # type: str
        self.address = address  # type: str
        self.module_type = module_type  # type: Optional[str]
        self.hardware_type = hardware_type  # type: str
        self.firmware_version = firmware_version  # type: Optional[str]
        self.hardware_version = hardware_version  # type: Optional[str]
        self.order = order  # type: Optional[int]
        self.online = online  # type: Optional[bool]

    def __eq__(self, other):
        if not isinstance(other, ModuleDTO):
            return False
        return (self.source == other.source and
                self.address == other.address and
                self.module_type == other.module_type and
                self.firmware_version == other.firmware_version and
                self.hardware_version == other.hardware_version and
                self.hardware_type == other.hardware_type and
                self.order == other.order)
