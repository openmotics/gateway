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
This module contains logic to handle lights with their state
"""
import logging
import time
from threading import Lock

from peewee import DoesNotExist

from gateway.observer import Event
from ioc import Injectable, Inject, INJECTED, Singleton
from models import Light

logger = logging.getLogger('openmotics')


class LightStatus(object):

    def __init__(self, r, g, b):
        self.r = r
        self.g = g
        self.b = b

class LightStatusTracker(object):

    def __init__(self, r, g, b):
        self._light_status = {}
        self._change_callback = None

    def get(self, id):
        return self._light_status[id]

    def set(self, id, state):
        if isinstance(state, LightStatus):
            self._light_status[id] = state
            if self._change_callback:
                self._change_callback(light_id=id, light_state=state)
        else:
            raise Exception('Not a valid LightStatus')

    def subscribe_changes(self, callback):
        self._change_callback = callback


@Injectable.named('light_controller')
@Singleton
class LightController(object):
    """
    Controls everything related to lights.
    """

    @Inject
    def __init__(self, master_controller=INJECTED):
        """
        Initializes the LightController
        :param master_controller: Master controller
        :type master_controller: gateway.master_controller.MasterController
        """
        self._master_controller = master_controller

        self._config_lock = Lock()
        self._event_subscriptions = []

        self._light_status_tracker = LightStatusTracker()
        self._light_status_tracker.subscribe_changes(self._light_state_changed)

    # Config

    def upsert_light(self, name, type, external_id, plugin):
        try:
            light = Light.get(plugin=plugin, external_id=external_id)
        except DoesNotExist:
            light = Light(plugin=plugin, external_id=external_id)
        light.name = name
        light.type = type
        light.save()
        return light

    def delete_light_by_id(self, light_id):
        Light.delete_by_id(light_id)

    def delete_light_by_external_id(self, external_id, plugin):
        light = Light.get(plugin=plugin, external_id=external_id)
        light.delete()

    # State

    def set_light_status(self, id, percentage):
        # TODO: get some in-memory write-through copy of the Light database objects
        light = Light.get(id=id)
        if not light.plugin:
            state = percentage > 0
            self._master_controller.set_output(light.external_id, state, dimmer=percentage)
            # TODO: make sure the output events get caught back so we can trigger the self._light_changed
        else:
            new_status = LightStatus(r=percentage, g=percentage, b=percentage)
            self._light_status_tracker.set(id, new_status)

    # Events

    def subscribe_events(self, callback):
        """
        Subscribes a callback to generic events
        :param callback: the callback to call
        """
        self._event_subscriptions.append(callback)

    def _light_state_changed(self, light_id, light_state):
        for callback in self._event_subscriptions:
            callback(Event(event_type=Event.Types.LIGHT_CHANGE,
                           data={'id': light_id,
                                 'status': light_state}))
