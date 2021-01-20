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
Sensor BLL
"""
from __future__ import absolute_import
import time
import logging
from peewee import JOIN
from ioc import Injectable, Inject, INJECTED, Singleton
from gateway.base_controller import BaseController
from gateway.events import GatewayEvent
from gateway.pubsub import PubSub
from gateway.dto import SensorDTO
from gateway.models import Sensor, Room
from serial_utils import CommunicationTimedOutException

if False:  # MYPY
    from typing import List, Tuple

logger = logging.getLogger("openmotics")


@Injectable.named('sensor_controller')
@Singleton
class SensorController(BaseController):

    @Inject
    def __init__(self, master_controller=INJECTED):
        super(SensorController, self).__init__(master_controller)
        self._master_supported_sensor_types = [Sensor.Types.TEMPERATURE, Sensor.Types.HUMIDITY, Sensor.Types.BRIGHTNESS]

    def _sync_orm(self):
        # type: () -> bool

        if self._sync_running:
            logger.info('ORM sync (Sensor): Already running')
            return False
        self._sync_running = True

        try:
            try:
                logger.info('ORM sync (Sensor)')
                start = time.time()

                ids = []
                for dto in self._master_controller.load_sensors():
                    id_ = dto.id
                    ids.append(id_)

                    # TODO: Issue here is that the code can't know whether a master-driven sensor has e.g. no humidity
                    #       sensor connected, or it's just temporarily unavailable. For now, each master-driven sensor
                    #       will have an ORM object foreach sensor type
                    found_types = []
                    for sensor in Sensor.select().where((Sensor.external_id == str(id_)) &
                                                        (Sensor.source == 'master')):
                        if sensor.type not in self._master_supported_sensor_types or sensor.type in found_types:
                            sensor.delete_instance()
                        else:
                            sensor.name = dto.name
                            sensor.offset = 0
                            if sensor.type == Sensor.Types.TEMPERATURE:
                                sensor.offset = dto.offset
                            sensor.save()
                        found_types.append(sensor.type)
                    for sensor_type in self._master_supported_sensor_types:
                        if sensor_type not in found_types:
                            offset = 0
                            if sensor_type == Sensor.Types.TEMPERATURE:
                                offset = dto.offset
                            Sensor.create(external_id=str(id_),
                                          source='master',
                                          type=sensor_type,
                                          name=dto.name,
                                          offset=offset)

                duration = time.time() - start
                logger.info('ORM sync (Sensor): completed after {0:.1f}s'.format(duration))
            except CommunicationTimedOutException as ex:
                logger.error('ORM sync (Sensor): Failed: {0}'.format(ex))
            except Exception:
                logger.exception('ORM sync (Sensor): Failed')

            if self._sync_dirty:
                gateway_event = GatewayEvent(GatewayEvent.Types.CONFIG_CHANGE, {'type': 'sensor'})
                self._pubsub.publish_gateway_event(PubSub.GatewayTopics.CONFIG, gateway_event)
        finally:
            self._sync_running = False
        return True

    def load_sensor(self, sensor_id):  # type: (int) -> SensorDTO
        sensor = Sensor.select(Room) \
                       .join_from(Sensor, Room, join_type=JOIN.LEFT_OUTER) \
                       .where(Sensor.number == sensor_id) \
                       .get()  # type: Sensor  # TODO: Load dict
        sensor_dto = self._master_controller.load_sensor(sensor_id=sensor_id)
        sensor_dto.room = sensor.room.number if sensor.room is not None else None
        return sensor_dto

    def load_sensors(self):  # type: () -> List[SensorDTO]
        sensor_dtos = []
        for sensor_ in list(Sensor.select(Sensor, Room)
                                  .join_from(Sensor, Room, join_type=JOIN.LEFT_OUTER)):  # TODO: Load dicts
            sensor_dto = self._master_controller.load_sensor(sensor_id=sensor_.number)
            sensor_dto.room = sensor_.room.number if sensor_.room is not None else None
            sensor_dtos.append(sensor_dto)
        return sensor_dtos

    def save_sensors(self, sensors):  # type: (List[Tuple[SensorDTO, List[str]]]) -> None
        sensors_to_save = []
        for sensor_dto, fields in sensors:
            sensor_ = Sensor.get_or_none(number=sensor_dto.id)  # type: Sensor
            if sensor_ is None:
                logger.info('Ignored saving non-existing Sensor {0}'.format(sensor_dto.id))
            if 'room' in fields:
                if sensor_dto.room is None:
                    sensor_.room = None
                elif 0 <= sensor_dto.room <= 100:
                    sensor_.room, _ = Room.get_or_create(number=sensor_dto.room)
                sensor_.save()
            sensors_to_save.append((sensor_dto, fields))
        self._master_controller.save_sensors(sensors_to_save)
