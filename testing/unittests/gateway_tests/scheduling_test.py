# Copyright (C) 2017 OpenMotics BV
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
Tests for the scheduling module.
"""
from __future__ import absolute_import
import os
import unittest

import pytz
from croniter import croniter
import xmlrunner
import time
import fakesleep
from mock import Mock
from datetime import datetime, timedelta
from threading import Lock, Semaphore
from ioc import SetTestMode, SetUpTestInjections
from gateway.webservice import WebInterface
from gateway.scheduling import SchedulingController


class SchedulingControllerTest(unittest.TestCase):
    RETURN_DATA = {}

    @classmethod
    def setUpClass(cls):
        SetTestMode()
        fakesleep.monkey_patch()

    @classmethod
    def tearDownClass(cls):
        fakesleep.monkey_restore()

    def setUp(self):
        self._db = "test.schedule.{0}.db".format(time.time())
        SchedulingControllerTest.RETURN_DATA = {}
        if os.path.exists(self._db):
            os.remove(self._db)

    def tearDown(self):
        SchedulingControllerTest.RETURN_DATA = {}
        if os.path.exists(self._db):
            os.remove(self._db)

    def _get_controller(self):
        def _do_group_action(group_action_id):
            SchedulingControllerTest.RETURN_DATA['do_group_action'] = group_action_id
            return {}

        def _do_basic_action(action_type, action_number):
            SchedulingControllerTest.RETURN_DATA['do_basic_action'] = (action_type, action_number)
            return {}

        gateway_api = Mock()
        gateway_api.get_timezone = lambda: 'Europe/Brussels'
        gateway_api.do_basic_action = _do_basic_action

        group_action_controller = Mock()
        group_action_controller.do_group_action = _do_group_action

        SetUpTestInjections(scheduling_db=self._db,
                            scheduling_db_lock=Lock(),
                            gateway_api=gateway_api,
                            user_controller=None,
                            maintenance_controller=None,
                            message_client=None,
                            configuration_controller=None,
                            thermostat_controller=None,
                            shutter_controller=Mock(),
                            output_controller=Mock(),
                            room_controller=Mock(),
                            input_controller=Mock(),
                            sensor_controller=Mock(),
                            pulse_counter_controller=Mock(),
                            frontpanel_controller=Mock(),
                            group_action_controller=group_action_controller,
                            module_controller=Mock())
        controller = SchedulingController()
        SetUpTestInjections(scheduling_controller=controller)
        controller.set_webinterface(WebInterface())
        return controller

    def test_base_validation(self):
        controller = self._get_controller()
        with self.assertRaises(RuntimeError):
            # Must have a name
            controller._validate(None, None, None, None, None, None, None)
        with self.assertRaises(RuntimeError):
            # Unaccepted type
            controller._validate('test', time.time(), 'FOO', None, None, None, None)
        with self.assertRaises(RuntimeError):
            # Duration too short
            controller._validate('test', time.time(), 'GROUP_ACTION', None, None, 10, None)
        with self.assertRaises(RuntimeError):
            # End when not repeating
            controller._validate('test', time.time(), 'GROUP_ACTION', None, None, None, 1)
        with self.assertRaises(RuntimeError):
            # Invalid repeat string
            controller._validate('test', time.time(), 'GROUP_ACTION', None, 'foo', None, None)

    def test_group_action(self):
        start = time.time()
        semaphore = Semaphore(0)
        controller = self._get_controller()
        controller.set_unittest_semaphore(semaphore)
        # New controller is empty
        self.assertEqual(len(controller.schedules), 0)
        with self.assertRaises(RuntimeError) as ctx:
            # Doesn't support duration
            controller.add_schedule('group_action', start + 120, 'GROUP_ACTION', None, None, 1000, None)
        self.assertEqual(str(ctx.exception), 'A schedule of type GROUP_ACTION does not have a duration. It is a one-time trigger')
        with self.assertRaises(RuntimeError) as ctx:
            # Incorrect argument
            controller.add_schedule('group_action', start + 120, 'GROUP_ACTION', 'foo', None, None, None)
        self.assertEqual(str(ctx.exception), 'The arguments of a GROUP_ACTION schedule must be an integer, representing the Group Action to be executed')
        controller.add_schedule('group_action', start + 120, 'GROUP_ACTION', 1, None, None, None)
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'group_action')
        self.assertEqual(controller.schedules[0].status, 'ACTIVE')
        controller.start()
        semaphore.acquire()
        self.assertEqual(SchedulingControllerTest.RETURN_DATA['do_group_action'], 1)
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'group_action')
        self.assertEqual(controller.schedules[0].status, 'COMPLETED')
        controller.stop()

    def test_basic_action(self):
        start = time.time()
        semaphore = Semaphore(0)
        controller = self._get_controller()
        controller.set_unittest_semaphore(semaphore)
        self.assertEqual(len(controller.schedules), 0)
        with self.assertRaises(RuntimeError) as ctx:
            # Doesn't support duration
            controller.add_schedule('basic_action', start + 120, 'BASIC_ACTION', None, None, 1000, None)
        self.assertEqual(str(ctx.exception), 'A schedule of type BASIC_ACTION does not have a duration. It is a one-time trigger')
        invalid_arguments_error = 'The arguments of a BASIC_ACTION schedule must be of type dict with arguments `action_type` and `action_number`'
        with self.assertRaises(RuntimeError) as ctx:
            # Incorrect argument
            controller.add_schedule('basic_action', start + 120, 'BASIC_ACTION', 'foo', None, None, None)
        self.assertEqual(str(ctx.exception), invalid_arguments_error)
        with self.assertRaises(RuntimeError) as ctx:
            # Incorrect argument
            controller.add_schedule('basic_action', start + 120, 'BASIC_ACTION', {'action_type': 1}, None, None, None)
        self.assertEqual(str(ctx.exception), invalid_arguments_error)
        controller.add_schedule('basic_action', start + 120, 'BASIC_ACTION', {'action_type': 1, 'action_number': 2}, None, None, None)
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'basic_action')
        self.assertEqual(controller.schedules[0].status, 'ACTIVE')
        controller.start()
        semaphore.acquire()
        self.assertEqual(SchedulingControllerTest.RETURN_DATA['do_basic_action'], (1, 2))
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'basic_action')
        self.assertEqual(controller.schedules[0].status, 'COMPLETED')
        controller.stop()

    def test_local_api(self):
        start = time.time()
        semaphore = Semaphore(0)
        controller = self._get_controller()
        controller.set_unittest_semaphore(semaphore)
        self.assertEqual(len(controller.schedules), 0)
        with self.assertRaises(RuntimeError) as ctx:
            # Doesn't support duration
            controller.add_schedule('local_api', start + 120, 'LOCAL_API', None, None, 1000, None)
        self.assertEqual(str(ctx.exception), 'A schedule of type LOCAL_API does not have a duration. It is a one-time trigger')
        invalid_arguments_error = 'The arguments of a LOCAL_API schedule must be of type dict with arguments `name` and `parameters`'
        with self.assertRaises(RuntimeError) as ctx:
            # Incorrect argument
            controller.add_schedule('local_api', start + 120, 'LOCAL_API', 'foo', None, None, None)
        self.assertEqual(str(ctx.exception), invalid_arguments_error)
        with self.assertRaises(RuntimeError) as ctx:
            # Incorrect argument
            controller.add_schedule('local_api', start + 120, 'LOCAL_API', {'name': 1}, None, None, None)
        self.assertEqual(str(ctx.exception), invalid_arguments_error)
        with self.assertRaises(RuntimeError) as ctx:
            # Not a valid call
            controller.add_schedule('local_api', start + 120, 'LOCAL_API', {'name': 'foo', 'parameters': {}}, None, None, None)
        self.assertEqual(str(ctx.exception), 'The arguments of a LOCAL_API schedule must specify a valid and (plugin_)exposed call')
        with self.assertRaises(Exception) as ctx:
            # Not a valid call
            controller.add_schedule('local_api', start + 120, 'LOCAL_API', {'name': 'do_basic_action',
                                                                            'parameters': {'action_type': 'foo', 'action_number': 4}}, None, None, None)
        self.assertEqual(str(ctx.exception), 'could not convert string to float: foo')
        controller.add_schedule('local_api', start + 120, 'LOCAL_API', {'name': 'do_basic_action',
                                                                        'parameters': {'action_type': 3, 'action_number': 4}}, None, None, None)
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'local_api')
        self.assertEqual(controller.schedules[0].status, 'ACTIVE')
        controller.start()
        semaphore.acquire()
        self.assertEqual(SchedulingControllerTest.RETURN_DATA['do_basic_action'], (3, 4))
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'local_api')
        self.assertEqual(controller.schedules[0].status, 'COMPLETED')
        controller.stop()

    def test_two_actions(self):
        start = time.time()
        controller = self._get_controller()
        controller.add_schedule('basic_action', start + 120, 'BASIC_ACTION', {'action_type': 1, 'action_number': 2}, None, None, None)
        controller.add_schedule('group_action', start + 120, 'GROUP_ACTION', 1, None, None, None)
        self.assertEqual(len(controller.schedules), 2)
        self.assertEqual(sorted(s.name for s in controller.schedules), ['basic_action', 'group_action'])
        for s in controller.schedules:
            if s.name == 'group_action':
                controller.remove_schedule(s.id)
        self.assertEqual(len(controller.schedules), 1)
        self.assertEqual(controller.schedules[0].name, 'basic_action')

    def test_schedule_is_due(self):
        start = time.time()
        start1 = start + timedelta(days=10).total_seconds()
        end1 = start + timedelta(days=10).total_seconds()
        controller = self._get_controller()
        controller.add_schedule('group_action', start1, 'GROUP_ACTION', 1, '0 0 */3 * *', None, end1)
        schedule1 = controller.schedules[0]
        self.assertIsNone(schedule1.next_execution)
        timezone1 = pytz.timezone(controller.schedules[0].timezone)
        start1_datetime = datetime.fromtimestamp(start1, timezone1)
        cron = croniter(schedule1.repeat, start1_datetime)
        next_execution1 = cron.get_next(ret_type=float)
        self.assertEqual(schedule1.is_due, False)
        self.assertEqual(schedule1.next_execution, next_execution1)

        start2 = start - timedelta(days=10).total_seconds()
        end2 = start + timedelta(days=10).total_seconds()
        controller.add_schedule('group_action', start2, 'GROUP_ACTION', 1, '0 0 * * *', None, end2)
        schedule2 = controller.schedules[1]
        timezone2 = pytz.timezone(schedule2.timezone)
        now = datetime.now(timezone2)
        cron = croniter(schedule2.repeat, now)
        next_execution2 = cron.get_next(ret_type=float)
        self.assertIsNone(schedule2.next_execution)
        self.assertEqual(schedule2.is_due, False)
        self.assertEqual(schedule2.next_execution, next_execution2)


if __name__ == "__main__":
    unittest.main(testRunner=xmlrunner.XMLTestRunner(output='../gw-unit-reports'))
