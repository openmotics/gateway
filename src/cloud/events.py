# Copyright (C) 2019 OpenMotics BV
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
Sends events to the cloud
"""
from __future__ import absolute_import
import logging
import time
from collections import deque
from peewee import DoesNotExist

from cloud.cloud_api_client import APIException, CloudAPIClient
from gateway.daemon_thread import DaemonThread, DaemonThreadWait
from gateway.events import GatewayEvent
from gateway.input_controller import InputController
from ioc import INJECTED, Inject, Injectable, Singleton

logger = logging.getLogger('openmotics')


@Injectable.named('event_sender')
@Singleton
class EventSender(object):

    @Inject
    def __init__(self, cloud_api_client=INJECTED, input_controller=INJECTED):  # type: (CloudAPIClient, InputController) -> None
        self._queue = deque()  # type: deque
        self._stopped = True
        self._cloud_client = cloud_api_client
        self._input_controller = input_controller

        self._events_queue = deque()  # type: deque
        self._events_thread = DaemonThread(name='EventSender loop',
                                           target=self._send_events_loop,
                                           interval=0.1, delay=0.2)

    def start(self):
        # type: () -> None
        self._events_thread.start()

    def stop(self):
        # type: () -> None
        self._events_thread.stop()

    def enqueue_event(self, event):
        logger.debug("@> EventSender | enqueue_event | {}".format(locals()))
        if self._is_enabled(event):
            event.data['timestamp'] = time.time()
            self._queue.appendleft(event)

    def _is_enabled(self, event):
        if event.type in [GatewayEvent.Types.OUTPUT_CHANGE,
                          GatewayEvent.Types.SHUTTER_CHANGE,
                          GatewayEvent.Types.THERMOSTAT_CHANGE,
                          GatewayEvent.Types.THERMOSTAT_GROUP_CHANGE]:
            return True
        elif event.type == GatewayEvent.Types.INPUT_CHANGE:
            input_id = event.data['id']
            # TODO: Below entry needs to be cached. But caching needs invalidation, so lets fix this
            #       when we have decent cache invalidation events to subscribe on
            try:
                input_ = self._input_controller.load_input(input_id)
            except DoesNotExist:
                return False
            return input_.event_enabled
        else:
            return False

    def _send_events_loop(self):
        # type: () -> None
        try:
            if not self._batch_send_events():
                raise DaemonThreadWait
        except APIException as ex:
            logger.error('Error sending events to the cloud: {}'.format(str(ex)))

    def _batch_send_events(self):
        events = []
        while len(events) < 25:
            try:
                events.append(self._queue.pop())
            except IndexError:
                break
        if len(events) > 0:
            self._cloud_client.send_events(events)
            return True
        return False
