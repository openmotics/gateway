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
PulseCounter Mapper
"""
from __future__ import absolute_import
from gateway.dto import PulseCounterDTO
from gateway.models import PulseCounter

if False:  # MYPY
    from typing import List


class PulseCounterMapper(object):

    @staticmethod
    def orm_to_dto(orm_object):  # type: (PulseCounter) -> PulseCounterDTO
        return PulseCounterDTO(id=orm_object.number,
                               name=orm_object.name,
                               persistent=orm_object.persistent,
                               room=None if orm_object.room is None else orm_object.room.number)

    @staticmethod
    def dto_to_orm(pulse_counter_dto, fields):  # type: (PulseCounterDTO, List[str]) -> PulseCounter
        pc_number = pulse_counter_dto.id
        pulse_counter = PulseCounter.get_or_none(number=pc_number)
        if pulse_counter is None:
            pulse_counter = PulseCounter(number=pc_number,
                                         name='',
                                         source='gateway',
                                         persistent=False)
        if 'name' in fields:
            pulse_counter.name = pulse_counter_dto.name
        if 'persistent' in fields:
            if pulse_counter.source == 'master' and pulse_counter_dto.persistent:
                raise ValueError(
                    'Persistence is not supported on pulse counter with number {} (id: {}, source: {})'.format(pc_number,
                                                                                                               pulse_counter.id,
                                                                                                               pulse_counter.source))
            pulse_counter.persistent = pulse_counter_dto.persistent
        return pulse_counter
