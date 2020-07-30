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
Module serializer
"""
from __future__ import absolute_import
from gateway.api.serializers.base import SerializerToolbox
from gateway.dto import ModuleDTO

if False:  # MYPY
    from typing import Dict, Optional, List, Tuple, Any


class ModuleSerializer(object):
    BYTE_MAX = 255

    @staticmethod
    def serialize(module_dto, fields):  # type: (ModuleDTO, Optional[List[str]]) -> Dict
        data = {'address': module_dto.address}  # type: Dict[str, Any]
        if module_dto.source == ModuleDTO.Source.MASTER:
            category_map = {ModuleDTO.ModuleType.CAN_CONTROL: 'INPUT',
                            ModuleDTO.ModuleType.SENSOR: 'INPUT',
                            ModuleDTO.ModuleType.INPUT: 'INPUT',
                            ModuleDTO.ModuleType.SHUTTER: 'SHUTTER',
                            ModuleDTO.ModuleType.OUTPUT: 'OUTPUT',
                            ModuleDTO.ModuleType.DIM_CONTROL: 'OUTPUT',
                            None: 'UNKNOWN'}
            data.update({'type': chr(int(module_dto.address.split('.')[0])),
                         'module_nr': module_dto.order,
                         'is_can': (module_dto.hardware_type == ModuleDTO.HardwareType.EMULATED or
                                    module_dto.module_type == ModuleDTO.ModuleType.CAN_CONTROL),
                         'is_virtual': module_dto.hardware_type == ModuleDTO.HardwareType.VIRTUAL,
                         'category': category_map.get(module_dto.module_type, 'UNKNOWN')})
            if module_dto.hardware_type == ModuleDTO.HardwareType.PHYSICAL:
                data.update({'firmware': module_dto.firmware_version,
                             'hardware': module_dto.hardware_version})
        else:
            module_type_map = {ModuleDTO.ModuleType.ENERGY: 'E',
                               ModuleDTO.ModuleType.POWER: 'P',
                               ModuleDTO.ModuleType.P1_CONCENTRATOR: 'C',
                               None: 'U'}
            data.update({'type': module_type_map.get(module_dto.module_type, 'U'),
                         'firmware': module_dto.firmware_version,
                         'address': module_dto.address,
                         'id': module_dto.order})
        return SerializerToolbox.filter_fields(data, fields)

    @staticmethod
    def deserialize(api_data):  # type: (Dict) -> Tuple[ModuleDTO, List[str]]
        raise NotImplementedError()  # Not supported
