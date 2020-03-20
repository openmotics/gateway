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
The observer module contains logic to observe various states of the system. It keeps track of what is changing
"""

import time
import logging
import ujson as json
from ioc import Injectable, Inject, INJECTED, Singleton
from threading import Thread
from platform_utils import Platform
from gateway.hal.master_controller import MasterEvent
from gateway.maintenance_communicator import InMaintenanceModeException
from master.inputs import InputStatus
from master import master_api
from bus.om_bus_events import OMBusEvents

if Platform.get_platform() == Platform.Type.CLASSIC:
    from master.master_communicator import CommunicationTimedOutException
else:
    # TODO: Replace for the Core+
    class CommunicationTimedOutException(Exception):
        pass

logger = logging.getLogger("openmotics")


class Event(object):
    """
    Event object
    """

    class Types(object):
        INPUT_CHANGE = 'INPUT_CHANGE'
        OUTPUT_CHANGE = 'OUTPUT_CHANGE'
        LIGHT_CHANGE = 'LIGHT_CHANGE'
        SHUTTER_CHANGE = 'SHUTTER_CHANGE'
        THERMOSTAT_CHANGE = 'THERMOSTAT_CHANGE'
        THERMOSTAT_GROUP_CHANGE = 'THERMOSTAT_GROUP_CHANGE'
        ACTION = 'ACTION'
        PING = 'PING'
        PONG = 'PONG'

    def __init__(self, event_type, data):
        self.type = event_type
        self.data = data

    def serialize(self):
        return {'type': self.type,
                'data': self.data,
                '_version': 1.0}  # Add version so that event processing code can handle multiple formats

    def __str__(self):
        return json.dumps(self.serialize())

    @staticmethod
    def deserialize(data):
        return Event(event_type=data['type'],
                     data=data['data'])


@Injectable.named('observer')
@Singleton
class Observer(object):
    """
    The Observer gets various (change) events and will also monitor certain datasets to manually detect changes
    """

    # TODO: Needs to be removed and replace by MasterEvents from the MasterController
    class LegacyMasterEvents(object):
        ON_SHUTTER_UPDATE = 'ON_SHUTTER_UPDATE'
        ON_INPUT_CHANGE = 'INPUT_CHANGE'
        ONLINE = 'ONLINE'

    class Types(object):
        THERMOSTATS = 'THERMOSTATS'
        SHUTTERS = 'SHUTTERS'

    @Inject
    def __init__(self, master_communicator=INJECTED, master_controller=INJECTED, message_client=INJECTED, shutter_controller=INJECTED):
        """
        :param master_communicator: Master communicator
        :type master_communicator: master.master_communicator.MasterCommunicator
        :param master_controller: Master controller
        :type master_controller: gateway.master_controller.MasterController
        :param message_client: MessageClient instance
        :type message_client: bus.om_bus_client.MessageClient
        :param shutter_controller: Shutter Controller
        :type shutter_controller: gateway.shutters.ShutterController
        """
        self._master_communicator = master_communicator
        self._master_controller = master_controller
        self._message_client = message_client
        self._gateway_api = None

        self._master_subscriptions = {Observer.LegacyMasterEvents.ON_SHUTTER_UPDATE: [],
                                      Observer.LegacyMasterEvents.ON_INPUT_CHANGE: [],
                                      Observer.LegacyMasterEvents.ONLINE: []}
        self._event_subscriptions = []

        self._input_status = InputStatus(on_input_change=self._input_changed)
        self._shutter_controller = shutter_controller
        self._shutter_controller.set_shutter_changed_callback(self._shutter_changed)

        self._master_controller.subscribe_event(self._master_event)

        self._input_interval = 300
        self._input_last_updated = 0
        self._input_config = {}
        self._shutters_interval = 600
        self._shutters_last_updated = 0
        self._master_online = False
        self._background_consumers_registered = False
        self._master_version = None

        self._thread = Thread(target=self._monitor)
        self._thread.daemon = True

    def set_gateway_api(self, gateway_api):
        """
        Sets the Gateway API instance
        :param gateway_api: Gateway API (business logic)
        :type gateway_api: gateway.gateway_api.GatewayApi
        """
        self._gateway_api = gateway_api

    def subscribe_master(self, event, callback):
        """
        Subscribes a callback to a certain event
        :param event: The event on which to call the callback
        :param callback: The callback to call
        """
        self._master_subscriptions[event].append(callback)

    def subscribe_events(self, callback):
        """
        Subscribes a callback to generic events
        :param callback: the callback to call
        """
        self._event_subscriptions.append(callback)

    def start(self):
        """ Starts the monitoring thread """
        self._ensure_gateway_api()
        if Platform.get_platform() == Platform.Type.CLASSIC:
            self._thread.start()

    def invalidate_cache(self, object_type=None):
        """
        Triggered when an external service knows certain settings might be changed in the background.
        For example: maintenance mode or module discovery
        """
        if object_type is None or object_type == Observer.Types.SHUTTERS:
            self._shutters_last_updated = 0
        self._master_controller.invalidate_caches()

    def _monitor(self):
        """ Monitors certain system states to detect changes without events """
        while True:
            try:
                self._check_master_version()
                # Refresh if required
                if self._shutters_last_updated + self._shutters_interval < time.time():
                    self._refresh_shutters()
                    self._set_master_state(True)
                if self._input_last_updated + self._input_interval < time.time():
                    self._refresh_inputs()
                    self._set_master_state(True)
                self._register_background_consumers()
                time.sleep(1)
            except CommunicationTimedOutException:
                logger.error('Got communication timeout during monitoring, waiting 10 seconds.')
                self._set_master_state(False)
                time.sleep(10)
            except InMaintenanceModeException:
                # This is an expected situation
                time.sleep(10)
            except Exception as ex:
                logger.exception('Unexpected error during monitoring: {0}'.format(ex))
                time.sleep(10)

    def _ensure_gateway_api(self):
        if self._gateway_api is None:
            raise RuntimeError('The observer has no access to the Gateway API yet')

    def _check_master_version(self):
        if self._master_version is None:
            self._master_version = self._gateway_api.get_master_version()
            self._set_master_state(True)

    def _set_master_state(self, online):
        if online != self._master_online:
            self._master_online = online
            # Notify subscribers
            for callback in self._master_subscriptions[Observer.LegacyMasterEvents.ONLINE]:
                callback(online)

    # Handle master "events"

    def _register_background_consumers(self):
        if self._master_version and not self._background_consumers_registered:
            if Platform.get_platform() == Platform.Type.CLASSIC:
                # This import/code will eventually be migrated away to MasterControllers
                from master.master_communicator import BackgroundConsumer
                self._master_communicator.register_consumer(BackgroundConsumer(master_api.input_list(self._master_version), 0, self._on_input))
                self._master_communicator.register_consumer(BackgroundConsumer(master_api.shutter_status(self._master_version), 0, self._on_shutter_update))
                self._background_consumers_registered = True

    # Handle master "events"

    def _on_input(self, data):
        """ Triggers when the master informs us of an Input state change """
        # Update status tracker
        self._input_status.set_input(data)
        # Notify subscribers
        for callback in self._master_subscriptions[Observer.LegacyMasterEvents.ON_INPUT_CHANGE]:
            callback(data)

    def _on_shutter_update(self, data):
        """ Triggers when the master informs us of an Shutter state change """
        # Update status tracker
        self._shutter_controller.update_from_master_state(data)
        # Notify subscribers
        for callback in self._master_subscriptions[Observer.LegacyMasterEvents.ON_SHUTTER_UPDATE]:
            callback(self._shutter_controller.get_states())

    def _master_event(self, master_event):
        """
        Triggers when the MasterController generates events
        :type master_event: gateway.hal.master_controller.MasterEvent
        """
        if master_event.type == MasterEvent.Types.OUTPUT_CHANGE:
            self._message_client.send_event(OMBusEvents.OUTPUT_CHANGE, {'id': master_event.data['id']})
            for callback in self._event_subscriptions:
                callback(Event(event_type=Event.Types.OUTPUT_CHANGE,
                               data=master_event.data))

    # Outputs

    def get_outputs(self):
        """ Returns a list of Outputs with their status """
        # TODO: also include other outputs (e.g. from plugins)
        return self._master_controller.get_output_statuses()

    def get_output(self, output_id):
        # TODO: also address other outputs (e.g. from plugins)
        return self._master_controller.get_output_status(output_id)

    # Inputs

    def get_inputs(self):
        """ Returns a list of Inputs with their status """
        self._ensure_gateway_api()
        return self._input_status.get_inputs()

    def get_recent(self):
        """ Returns a list of recently changed inputs """
        self._ensure_gateway_api()
        return self._input_status.get_recent()

    def _input_changed(self, input_id, status):
        """ Executed by the Input Status tracker when an input changed state """
        for callback in self._event_subscriptions:
            resp_data = {'id': input_id,
                         'status': status,
                         'location': {'room_id': self._input_config[input_id].get('room', 255)}}
            callback(Event(event_type=Event.Types.INPUT_CHANGE, data=resp_data))

    def _refresh_inputs(self):
        """ Refreshes the Input Status tracker """
        # 1. refresh input configuration
        self._input_config = {input_configuration['id']: input_configuration for input_configuration in self._gateway_api.get_input_configurations()}
        # 2. poll for latest input status
        try:
            number_of_input_modules = self._master_communicator.do_command(master_api.number_of_io_modules())['in']
            inputs = []
            for i in xrange(number_of_input_modules):
                # we could be dealing with e.g. a temperature module, skip those
                module_type = self._gateway_api.get_input_module_type(input_module_id=i)
                if module_type not in ['i', 'I']:
                    continue
                result = self._master_communicator.do_command(master_api.read_input_module(self._master_version), {'input_module_nr': i})
                module_status = result['input_status']
                # module_status byte contains bits for each individual input, use mask and bitshift to get status
                for n in xrange(8):
                    input_nr = i * 8 + n
                    input_status = module_status & (1 << n) != 0
                    data = {'input': input_nr, 'status': input_status}
                    inputs.append(data)
            self._input_status.full_update(inputs)
        except NotImplementedError as e:
            logger.error('Cannot refresh inputs: {}'.format(e))
        self._input_last_updated = time.time()

    # Shutters

    def get_shutter_status(self):
        return self._shutter_controller.get_states()

    def _shutter_changed(self, shutter_id, shutter_data, shutter_state):
        """ Executed by the Shutter Status tracker when a shutter changed state """
        for callback in self._event_subscriptions:
            callback(Event(event_type=Event.Types.SHUTTER_CHANGE,
                           data={'id': shutter_id,
                                 'status': {'state': shutter_state},
                                 'location': {'room_id': shutter_data['room']}}))

    def _refresh_shutters(self):
        """ Refreshes the Shutter status tracker """
        number_of_shutter_modules = self._master_communicator.do_command(master_api.number_of_io_modules())['shutter']
        self._shutter_controller.update_config(self._gateway_api.get_shutter_configurations())
        for module_id in xrange(number_of_shutter_modules):
            self._shutter_controller.update_from_master_state(
                {'module_nr': module_id,
                 'status': self._master_communicator.do_command(master_api.shutter_status(self._master_version),
                                                                {'module_nr': module_id})['status']}
            )
        self._shutters_last_updated = time.time()
