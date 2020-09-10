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
from gateway.dto.feedback_led import FeedbackLedDTO

if False:  # MYPY
    from typing import Any, Optional


class OutputDTO(BaseDTO):
    def __init__(self, id, name='', module_type='O', timer=None, floor=None, output_type=None,
                 can_led_1=None,  # type: Optional[FeedbackLedDTO]
                 can_led_2=None,  # type: Optional[FeedbackLedDTO]
                 can_led_3=None,  # type: Optional[FeedbackLedDTO]
                 can_led_4=None,  # type: Optional[FeedbackLedDTO]
                 room=None,
                 lock_bit_id=None,
                 state=None):
        self.id = id  # type: int
        self.name = name  # type: str
        self.module_type = module_type  # type: str
        self.timer = timer  # type: Optional[int]
        self.floor = floor  # type: Optional[int]
        self.output_type = output_type  # type: int
        self.room = room  # type: Optional[int]
        self.lock_bit_id = lock_bit_id  # type: Optional[int]
        self.state = state
        if self.state:
            self.state.id = self.id
        self.can_led_1 = can_led_1 or FeedbackLedDTO(id=None, function=FeedbackLedDTO.Functions.UNKNOWN)
        self.can_led_2 = can_led_2 or FeedbackLedDTO(id=None, function=FeedbackLedDTO.Functions.UNKNOWN)
        self.can_led_3 = can_led_3 or FeedbackLedDTO(id=None, function=FeedbackLedDTO.Functions.UNKNOWN)
        self.can_led_4 = can_led_4 or FeedbackLedDTO(id=None, function=FeedbackLedDTO.Functions.UNKNOWN)

    def __eq__(self, other):
        if not isinstance(other, OutputDTO):
            return False
        return (self.id == other.id and
                self.name == other.name and
                self.module_type == other.module_type and
                self.timer == other.timer and
                self.floor == other.floor and
                self.output_type == other.output_type and
                self.room == other.room and
                self.lock_bit_id == other.lock_bit_id and
                self.can_led_1 == other.can_led_1 and
                self.can_led_2 == other.can_led_2 and
                self.can_led_3 == other.can_led_3 and
                self.can_led_4 == other.can_led_4)

    def __str__(self):
        return 'OutputDTO: {}'.format(vars(self))


class OutputStateDTO(BaseDTO):
    def __init__(self, id, status=False, ctimer=0, dimmer=0, locked=False):
        # type: (int, bool, int, int, bool) -> None
        self.id = id  # type: int
        self.status = status
        self.ctimer = ctimer
        self.dimmer = dimmer
        self.locked = locked

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, OutputStateDTO):
            return False
        return (self.id == other.id and
                self.status == other.status and
                self.ctimer == other.ctimer and
                self.dimmer == other.dimmer and
                self.locked == other.locked)
