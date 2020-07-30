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
Module Mapper
"""
from __future__ import absolute_import
from gateway.dto import ModuleDTO
from gateway.models import Module

if False:  # MYPY
    from typing import List


class ModuleMapper(object):

    @staticmethod
    def orm_to_dto(orm_object):  # type: (Module) -> ModuleDTO
        return ModuleDTO(source=orm_object.source,
                         address=orm_object.address,
                         module_type=orm_object.module_type,
                         hardware_type=orm_object.hardware_type,
                         firmware_version=orm_object.firmware_version,
                         hardware_version=orm_object.hardware_version,
                         order=orm_object.order)

    @staticmethod
    def dto_to_orm(module_dto, fields):  # type: (ModuleDTO, List[str]) -> Module
        raise NotImplementedError('Updating modules is not supported (represent physical state)')
