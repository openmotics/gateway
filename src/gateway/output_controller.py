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
Output BLL
"""
from __future__ import absolute_import

import copy
import logging
from threading import Lock

from bus.om_bus_client import MessageClient
from bus.om_bus_events import OMBusEvents
from gateway.base_controller import BaseController, SyncStructure
from gateway.daemon_thread import DaemonThread, DaemonThreadWait
from gateway.dto import OutputDTO, OutputStateDTO
from gateway.events import GatewayEvent
from gateway.hal.master_controller import CommunicationFailure
from gateway.hal.master_event import MasterEvent
from gateway.models import Output, Room
from ioc import INJECTED, Inject, Injectable, Singleton
from serial_utils import CommunicationTimedOutException
from toolbox import Toolbox

if False:  # MYPY
    from typing import Any, Callable, Dict, List, Optional, Tuple
    from gateway.hal.master_controller import MasterController

logger = logging.getLogger('openmotics')


@Injectable.named('output_controller')
@Singleton
class OutputController(BaseController):

    SYNC_STRUCTURES = [SyncStructure(Output, 'output')]

    @Inject
    def __init__(self, master_controller=INJECTED, message_client=INJECTED):
        # type: (MasterController, MessageClient) -> None
        super(OutputController, self).__init__(master_controller)
        self._message_client = message_client
        self._cache = OutputStateCache()
        self._event_subscriptions = []  # type: List[Callable[[GatewayEvent],None]]
        self._sync_state_thread = None  # type: Optional[DaemonThread]

    def start(self):
        # type: () -> None
        super(OutputController, self).start()
        self._sync_state_thread = DaemonThread('OutputController sync state',
                                               target=self._sync_state,
                                               interval=600, delay=10)
        self._sync_state_thread.start()

    def stop(self):
        # type: () -> None
        super(OutputController, self).stop()
        if self._sync_state_thread:
            self._sync_state_thread.stop()
            self._sync_state_thread = None

    def subscribe_events(self, callback):
        # type: (Callable[[GatewayEvent],None]) -> None
        self._event_subscriptions.append(callback)

    def _handle_master_event(self, master_event):
        # type: (MasterEvent) -> None
        # logger.debug('@> OutputController | _handle_master_event | {}'.format(locals()))
        super(OutputController, self)._handle_master_event(master_event)
        if master_event.type == MasterEvent.Types.MODULE_DISCOVERY:
            if self._sync_state_thread:
                self._sync_state_thread.request_single_run()
        if master_event.type == MasterEvent.Types.OUTPUT_STATUS:
            # logger.debug('@> OutputController | _handle_master_event | handle_output_status')
            self._handle_output_status(master_event.data)

    def _handle_output_status(self, change_data):
        # type: (Dict[str,Any]) -> None
        # logger.debug('@> OutputController | _handle_output_status | {}'.format(locals()))
        changed = self._cache.handle_change(change_data['id'], change_data)
        if changed:
            # logger.debug('@> OutputController | _publish_output_change | {}'.format(changed))
            self._publish_output_change(changed)

    def _sync_state(self):
        try:
            self.load_outputs()
            for state_data in self._master_controller.load_output_status():
                if 'id' in state_data:
                    changed = self._cache.handle_change(state_data['id'], state_data)
                    if changed:
                        self._publish_output_change(changed)
        except CommunicationTimedOutException:
            logger.error('Got communication timeout during synchronization, waiting 10 seconds.')
            raise DaemonThreadWait
        except CommunicationFailure:
            # This is an expected situation
            raise DaemonThreadWait

    def _publish_output_change(self, output_dto):
        # type: (OutputDTO) -> None
        logger.debug('@> OutputController | _publish_output_change | {}'.format(locals()))
        event_status = {'on': output_dto.state.status, 'locked': output_dto.state.locked}
        if output_dto.module_type in ['d', 'D']:
            event_status['value'] = output_dto.state.dimmer
        event_data = {'id': output_dto.id,
                      'status': event_status,
                      'location': {'room_id': Toolbox.denonify(output_dto.room, 255)}}
        if self._message_client is not None:
            self._message_client.send_event(OMBusEvents.OUTPUT_CHANGE, {'id': output_dto.id})
        for callback in self._event_subscriptions:
            gwe = GatewayEvent(GatewayEvent.Types.OUTPUT_CHANGE, event_data)
            # logger.debug('@> OutputController | _publish_output_change | send gateway event to callbacks | {}'.format(gwe.__repr__()))
            callback(gwe)

    def get_output_status(self, output_id):
        # type: (int) -> OutputStateDTO
        # TODO also support plugins
        output_state_dto = self._cache.get_state().get(output_id)
        if output_state_dto is None:
            raise ValueError('Output with id {} does not exist'.format(output_id))
        return output_state_dto

    def get_output_statuses(self):
        # type: () -> List[OutputStateDTO]
        # TODO also support plugins
        return list(self._cache.get_state().values())

    def load_output(self, output_id):  # type: (int) -> OutputDTO
        output = Output.get(number=output_id)  # type: Output
        output_dto = self._master_controller.load_output(output_id=output.number)
        output_dto.room = output.room.number if output.room is not None else None
        return output_dto

    def load_outputs(self):  # type: () -> List[OutputDTO]
        output_dtos = []
        for output in Output.select():
            output_dto = self._master_controller.load_output(output_id=output.number)
            output_dto.room = output.room.number if output.room is not None else None
            output_dtos.append(output_dto)
        self._cache.update_outputs(output_dtos)
        return output_dtos

    def save_outputs(self, outputs):  # type: (List[Tuple[OutputDTO, List[str]]]) -> None
        outputs_to_save = []
        for output_dto, fields in outputs:
            output = Output.get_or_none(number=output_dto.id)  # type: Output
            if output is None:
                logger.info('Ignored saving non-existing Output {0}'.format(output_dto.id))
            if 'room' in fields:
                if output_dto.room is None:
                    output.room = None
                elif 0 <= output_dto.room <= 100:
                    output.room, _ = Room.get_or_create(number=output_dto.room)
                output.save()
            outputs_to_save.append((output_dto, fields))
        self._master_controller.save_outputs(outputs_to_save)

    def set_all_lights_off(self):
        # type: () -> None
        # TODO: also switch other lights (e.g. from plugins)
        return self._master_controller.set_all_lights_off()

    def set_all_lights_floor_off(self, floor):
        # type: (int) -> None
        # TODO: also switch other lights (e.g. from plugins)
        return self._master_controller.set_all_lights_floor_off(floor=floor)

    def set_all_lights_floor_on(self, floor):
        # type: (int) -> None
        # TODO: also switch other lights (e.g. from plugins)
        return self._master_controller.set_all_lights_floor_on(floor=floor)


class OutputStateCache(object):
    def __init__(self):
        self._cache = {}  # type: Dict[int,OutputDTO]
        self._lock = Lock()
        self._loaded = False

    def get_state(self):
        # type: () -> Dict[int,OutputStateDTO]
        return {x.id: x.state for x in self._cache.values()}

    def update_outputs(self, output_dtos):
        # type: (List[OutputDTO]) -> None
        with self._lock:
            new_state = {}
            for output_dto in output_dtos:
                output_dto = copy.copy(output_dto)
                if output_dto.id in self._cache:
                    output_dto.state = self._cache[output_dto.id].state
                else:
                    output_dto.state = OutputStateDTO(output_dto.id)
                new_state[output_dto.id] = output_dto
            self._cache = new_state
            self._loaded = True

    def handle_change(self, output_id, change_data):
        # type: (int, Dict[str,Any]) -> Optional[OutputDTO]
        """
        Cache output state and detect changes.
        The classic master will send multiple status events when an output changes,
        this deduplicates actual changes based on the cached state.
        """
        with self._lock:
            if not self._loaded:
                return None
            if output_id not in self._cache:
                logger.warning('Received change for unknown output {0}: {1}'.format(output_id, change_data))
                return None
            changed = False
            state = self._cache[output_id].state
            if 'status' in change_data:
                status = bool(change_data['status'])
                changed |= state.status != status
                state.status = status
            if 'ctimer' in change_data:
                state.ctimer = int(change_data['ctimer'])
            if 'dimmer' in change_data:
                dimmer = int(change_data['dimmer'])
                changed |= state.dimmer != dimmer
                state.dimmer = dimmer
            if 'locked' in change_data:
                locked = bool(change_data['locked'])
                changed |= state.locked != locked
                state.locked = locked
            if changed:
                return self._cache[output_id]
            else:
                return None
