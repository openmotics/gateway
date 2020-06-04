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
Contains Basic Action related code
"""

from __future__ import absolute_import
from master.core.memory_types import MemoryByteField, MemoryWordField

if False:  # MYPY
    from typing import List, Optional, Any


class BasicAction(object):
    def __init__(self, action_type, action, device_nr=None, extra_parameter=None):  # type: (int, int, Optional[int], Optional[int]) -> None
        self._action_type = MemoryByteField.encode(action_type)  # type: List[int]
        self._action = MemoryByteField.encode(action)  # type: List[int]
        self._device_nr = MemoryWordField.encode(device_nr if device_nr is not None else 0)  # type: List[int]
        self._extra_parameter = MemoryWordField.encode(extra_parameter if extra_parameter is not None else 0)  # type: List[int]

    @property
    def action_type(self):  # type: () -> int
        return MemoryByteField.decode(self._action_type)

    @property
    def action(self):  # type: () -> int
        return MemoryByteField.decode(self._action)

    @property
    def device_nr(self):  # type: () -> int
        return MemoryWordField.decode(self._device_nr)

    @property
    def extra_parameter(self):  # type: () -> int
        return MemoryWordField.decode(self._extra_parameter)

    def encode(self):  # type: () -> List[int]
        return self._action_type + self._action + self._device_nr + self._extra_parameter

    @property
    def in_use(self):  # type: () -> bool
        return self._action_type != 255 and self.action != 255

    @staticmethod
    def decode(data):  # type: (List[int]) -> BasicAction
        basic_action = BasicAction(action_type=data[0],
                                   action=data[1])
        basic_action._device_nr = data[2:4]
        basic_action._extra_parameter = data[4:6]
        return basic_action

    def __repr__(self):
        return 'BA({0},{1},{2},{3})'.format(self.action_type, self.action, self.device_nr, self.extra_parameter)

    def __eq__(self, other):  # type: (Any) -> bool
        if not isinstance(other, BasicAction):
            return False
        return self.encode() == other.encode()
