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
Output DTO
"""
from gateway.dto.base import BaseDTO

if False:  # MYPY
    from typing import Any, Optional


class VentilationDTO(BaseDTO):
    def __init__(self, id, source, external_id='', name='', type='', vendor='', amount_of_levels=0):
        self.id = id  # type: int
        self.source = source  # type: VentilationSourceDTO
        self.external_id = external_id  # type: str
        self.name = name  # type: str
        self.type = type  # type: str
        self.vendor = vendor  # type: str
        self.amount_of_levels = amount_of_levels  # type: int

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, VentilationDTO):
            return False
        return (self.id == other.id and
                self.source == other.source and
                self.external_id == other.external_id and
                self.name == other.name and
                self.type == other.type and
                self.vendor == other.vendor and
                self.amount_of_levels == other.amount_of_levels)


class VentilationSourceDTO(BaseDTO):
    class Type:
        PLUGIN = 'plugin'

    def __init__(self, id, type='', name=''):
        self.id = id  # type: int
        self.type = type  # type: str
        self.name = name  # type: str

    @property
    def is_plugin(self):
        return self.type == VentilationSourceDTO.Type.PLUGIN

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, VentilationSourceDTO):
            return False
        return (self.id == other.id and
                self.name == other.name and
                self.type == other.type)
