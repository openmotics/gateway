# Copyright (C) 2018 OpenMotics BV
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
PulseCounter BLL
"""
from __future__ import absolute_import
import logging
from peewee import fn, DoesNotExist
from ioc import Injectable, Inject, INJECTED, Singleton
from gateway.base_controller import BaseController
from gateway.dto import PulseCounterDTO
from gateway.models import PulseCounter, Room
from gateway.mappers import PulseCounterMapper

if False:  # MYPY
    from typing import List, Tuple, Dict, Optional

logger = logging.getLogger("openmotics")


@Injectable.named('pulse_counter_controller')
@Singleton
class PulseCounterController(BaseController):

    @Inject
    def __init__(self, master_controller=INJECTED):
        super(PulseCounterController, self).__init__(master_controller)
        self._counts = {}  # type: Dict[int, int]

    def _sync_orm(self):
        if self._sync_running:
            logger.info('ORM sync (PulseCounter): Already running')
            return False
        self._sync_running = True

        logger.info('ORM sync (PulseCounter)')

        try:
            for pulse_counter_dto in self._master_controller.load_pulse_counters():
                pulse_counter_id = pulse_counter_dto.id
                pulse_counter = PulseCounter.get_or_none(number=pulse_counter_id)
                if pulse_counter is None:
                    pulse_counter = PulseCounter(number=pulse_counter_id,
                                                 name='PulseCounter {0}'.format(pulse_counter_id),
                                                 source='master',
                                                 persistent=False)
                    pulse_counter.save()
        finally:
            self._sync_running = False

        logger.info('ORM sync (PulseCounter): completed')
        return True

    def create(self, number, name=None, room_number=None, persistent=False):  # type: (int, str, int, bool) -> PulseCounterDTO
        if number is None:
            raise ValueError('Pulse counter needs a number, not {}'.format(number))
        pulse_counter = PulseCounter.get_or_none(number=number)  # type: PulseCounter
        if pulse_counter:
            raise ValueError('Pulse counter with number {} already exists'.format(number))
        else:
            name = name if name else 'PulseCounter {0}'.format(number)
            pulse_counter = PulseCounter(number=number,
                                         name=name,
                                         source='gateway',
                                         persistent=persistent)

            if room_number and 0 <= room_number <= 100:
                room = Room.get_or_create(number=room_number)
                room, _ = Room.get_or_create(number=room)
            else:
                room = None
            pulse_counter.room = room
            pulse_counter.save()
            return PulseCounterMapper.orm_to_dto(pulse_counter)

    def delete(self, number):  # type: (int) -> None
        pulse_counter = PulseCounter.get_or_none(number=number)  # type: PulseCounter
        if not pulse_counter:
            raise KeyError('Pulse counter with number {} does not exist'.format(number))
        elif pulse_counter.source == 'master':
            raise KeyError('Cannot delete master based pulse counters')
        else:
            pulse_counter.delete()

    def load_pulse_counter(self, pulse_counter_id):
        pulse_counter = PulseCounter.get(number=pulse_counter_id)  # type: PulseCounter
        if pulse_counter.source == 'master':
            pulse_counter_dto = self._master_controller.load_pulse_counter(pulse_counter_id=pulse_counter.number)
            pulse_counter_dto.room = pulse_counter.room.number if pulse_counter.room is not None else None
        else:
            pulse_counter_dto = PulseCounterMapper.orm_to_dto(pulse_counter)
        return pulse_counter_dto

    def load_pulse_counters(self):  # type: () -> List[PulseCounterDTO]
        pulse_counter_dtos = []
        for pulse_counter in PulseCounter.select():
            if pulse_counter.source == 'master':
                pulse_counter_dto = self._master_controller.load_pulse_counter(pulse_counter_id=pulse_counter.number)
                pulse_counter_dto.room = pulse_counter.room.number if pulse_counter.room is not None else None
            else:
                pulse_counter_dto = PulseCounterMapper.orm_to_dto(pulse_counter)
            pulse_counter_dtos.append(pulse_counter_dto)
        return pulse_counter_dtos

    def save_pulse_counters(self, pulse_counters):  # type: (List[Tuple[PulseCounterDTO, List[str]]]) -> None
        master_backed_pulse_counters = []
        for pulse_counter_dto, fields in pulse_counters:
            pulse_counter = PulseCounter.get_or_none(number=pulse_counter_dto.id)  # type: PulseCounter
            if pulse_counter is None:
                raise DoesNotExist('A PulseCounter with id {0} could not be found'.format(pulse_counter_dto.id))
            if pulse_counter.source not in ['master', 'gateway']:
                logger.warning('Trying to save a PulseCounter with unknown source {0}'.format(pulse_counter.source))
                continue
            try:
                pulse_counter = PulseCounterMapper.dto_to_orm(pulse_counter_dto, fields)
                if 'room' in fields:
                    if pulse_counter_dto.room is None:
                        pulse_counter.room = None
                    elif 0 <= pulse_counter_dto.room <= 100:
                        pulse_counter.room, _ = Room.get_or_create(number=pulse_counter_dto.room)
                pulse_counter.save()
                if pulse_counter.source == 'master':  # also save on the master if needed
                    master_backed_pulse_counters.append((pulse_counter_dto, fields))
            except ValueError as e:
                logger.error('Could not save pulse counter with id {0}: {1}'.format(pulse_counter_dto.id, e))
                continue
        # batch save the master pulse counters for performance reasons
        self._master_controller.save_pulse_counters(master_backed_pulse_counters)

    def set_amount_of_pulse_counters(self, amount):  # type: (int) -> int
        _ = self
        # This does not make a lot of sense in an ORM driven implementation, but is for legacy purposes.
        # The legacy implementation heavily depends on the number (legacy id) and the fact that there should be no
        # gaps between them. If there are gaps, legacy upstream code will most likely break.
        # TODO: Fix legacy implementation once the upstream can manage this better

        amount_of_master_pulse_counters = PulseCounter.select().where(PulseCounter.source == 'master').count()
        if amount < amount_of_master_pulse_counters:
            raise ValueError('Amount should be >= {0}'.format(amount_of_master_pulse_counters))

        # Assume amount is 27:
        # - This means n master driven PulseCounters
        # - This means 27-n gateway driven PulseCounters
        # The `number` field will contain 0-(n-1) (zero-based counting), this means that any
        # number above or equal to the amount can be removed (>= n)

        PulseCounter.delete().where(PulseCounter.number >= amount).execute()
        for number in range(amount_of_master_pulse_counters, amount):
            try:
                self.create(number)
            except ValueError as e:
                logger.error("Error creating pulse counter {}: {}".format(number, e))
        return amount

    def get_amount_of_pulse_counters(self):  # type: () -> int
        _ = self
        return PulseCounter.select(fn.Max(PulseCounter.number)).scalar() + 1

    def set_value(self, pulse_counter_id, value):  # type: (int, int) -> int
        pulse_counter = PulseCounter.get(number=pulse_counter_id)
        if pulse_counter.source == 'master':
            raise ValueError('Cannot set pulse counter value for a Master controlled PulseCounter')
        self._counts[pulse_counter_id] = value
        return value

    def get_values(self):  # type: () -> Dict[int, Optional[int]]
        pulse_counter_values = {}  # type: Dict[int, Optional[int]]
        pulse_counter_values.update(self._master_controller.get_pulse_counter_values())
        for pulse_counter in PulseCounter.select().where(PulseCounter.source == 'gateway'):
            pulse_counter_values[pulse_counter.number] = self._counts.get(pulse_counter.number)
        return pulse_counter_values

    def get_persistence(self):  # type: () -> Dict[int, bool]
        _ = self
        return {pulse_counter.number: pulse_counter.persistent for pulse_counter
                in PulseCounter.select()}
