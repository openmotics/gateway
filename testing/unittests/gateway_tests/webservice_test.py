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
from __future__ import absolute_import

import json
import unittest

import mock

from bus.om_bus_client import MessageClient
from gateway.config import ConfigurationController
from gateway.dto import OutputStateDTO
from gateway.gateway_api import GatewayApi
from gateway.group_action_controller import GroupActionController
from gateway.hal.frontpanel_controller import FrontpanelController
from gateway.input_controller import InputController
from gateway.maintenance_controller import MaintenanceController
from gateway.output_controller import OutputController
from gateway.pulse_counter_controller import PulseCounterController
from gateway.room_controller import RoomController
from gateway.scheduling import SchedulingController
from gateway.sensor_controller import SensorController
from gateway.shutter_controller import ShutterController
from gateway.thermostat.thermostat_controller import ThermostatController
from gateway.users import UserController
from gateway.webservice import WebInterface
from gateway.module_controller import ModuleController
from ioc import SetTestMode, SetUpTestInjections


class WebInterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SetTestMode()

    def setUp(self):
        self.output_controller = mock.Mock(OutputController)
        self.gateway_api = mock.Mock(GatewayApi)
        SetUpTestInjections(configuration_controller=mock.Mock(ConfigurationController),
                            frontpanel_controller=mock.Mock(FrontpanelController),
                            gateway_api=self.gateway_api,
                            group_action_controller=mock.Mock(GroupActionController),
                            input_controller=mock.Mock(InputController),
                            maintenance_controller=mock.Mock(MaintenanceController),
                            message_client=mock.Mock(MessageClient),
                            output_controller=self.output_controller,
                            pulse_counter_controller=mock.Mock(PulseCounterController),
                            room_controller =mock.Mock(RoomController),
                            scheduling_controller=mock.Mock(SchedulingController),
                            sensor_controller=mock.Mock(SensorController),
                            shutter_controller=mock.Mock(ShutterController),
                            thermostat_controller=mock.Mock(ThermostatController),
                            user_controller=mock.Mock(UserController),
                            module_controller=mock.Mock(ModuleController))
        self.web = WebInterface()

    def test_output_status(self):
        with mock.patch.object(self.output_controller, 'get_output_statuses',
                               return_value=[OutputStateDTO(id=0, status=True)]):
            response = self.web.get_output_status()
            assert [{'id': 0, 'status': 1, 'ctimer': 0, 'dimmer': 0, 'locked': False}] == json.loads(response)['status']
