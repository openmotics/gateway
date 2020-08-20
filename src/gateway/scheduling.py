# Copyright (C) 2016 OpenMotics BV
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
The scheduling module contains the SchedulingController, this controller is used for scheduling various actions
"""

from __future__ import absolute_import
import sqlite3
import logging
import time
import pytz
from datetime import datetime
from croniter import croniter
from random import randint
from threading import Thread
from ioc import Injectable, Inject, INJECTED, Singleton
from serial_utils import CommunicationTimedOutException
from gateway.webservice import params_parser
import ujson as json
import six

if False:  # MYPY
    from typing import Dict
    from threading import Lock
    from gateway.group_action_controller import GroupActionController
    from gateway.gateway_api import GatewayApi


logger = logging.getLogger('openmotics')


class Schedule(object):

    timezone = None

    def __init__(self, id, name, start, repeat, duration, end, schedule_type, arguments, status):
        self.id = id
        self.name = name
        self.start = start
        self.repeat = repeat
        self.duration = duration
        self.end = end
        self.schedule_type = schedule_type
        self.arguments = arguments
        self.status = status
        self.last_executed = None
        self.next_execution = None
        self.is_running = False

    @property
    def is_due(self):
        if self.repeat is None:
            # Single-run schedules should start on their set starting time if not yet executed
            if self.last_executed is not None:
                return False
            return self.start <= time.time()
        # Repeating
        now = time.time()
        lower_limit = now - (15 * 60)  # Don't execute a schedule that's overdue for 15 minutes
        execute = self.next_execution is not None and lower_limit <= self.next_execution <= now
        self.next_execution = Schedule._next_execution(max(self.start, now), self.repeat)
        return execute

    @property
    def has_ended(self):
        if self.repeat is None:
            return self.last_executed is not None
        if self.end is not None:
            return self.end < time.time()
        return False

    @staticmethod
    def _next_execution(base_time, repeat):
        timezone = pytz.timezone(Schedule.timezone)
        cron = croniter(repeat, datetime.fromtimestamp(base_time, timezone))
        return cron.get_next(ret_type=float)

    def serialize(self):
        return {'id': self.id,
                'name': self.name,
                'start': self.start,
                'repeat': self.repeat,
                'duration': self.duration,
                'end': self.end,
                'schedule_type': self.schedule_type,
                'arguments': self.arguments,
                'status': self.status,
                'last_executed': self.last_executed,
                'next_execution': self.next_execution}


@Injectable.named('scheduling_controller')
@Singleton
class SchedulingController(object):
    """
    The SchedulingController controls schedules and executes them. Based on their type, they can trigger different
    behavior.

    Supported types:
    * MIGRATION: Migrates the Master's schedule to here
    * GROUP_ACTION: Executes a Group Action
      * Required arguments: json encoded Group Action id
    * BASIC_ACTION: Executes a Basic Action
      * Required arguments: {'action_type': <action type>,
                             'action_number': <action number>}
    * LOCAL_API: Executes a local API call
      * Required arguments: {'name': '<name of the call>',
                             'parameters': {<kwargs for the call>}}

    Supported repeats:
    * None: Single execution at start time
    * String: Cron format, docs at https://github.com/kiorky/croniter
    """

    @Inject
    def __init__(self, scheduling_db=INJECTED, scheduling_db_lock=INJECTED, gateway_api=INJECTED, group_action_controller=INJECTED):
        # type: (str, Lock, GatewayApi, GroupActionController) -> None
        """
        Constructs a new ConfigController.

        :param scheduling_db: filename of the sqlite database used to store the scheduling
        :param gateway_api: GatewayAPI
        """
        self._gateway_api = gateway_api
        self._group_action_controller = group_action_controller
        self._web_interface = None

        self._lock = scheduling_db_lock
        self._connection = sqlite3.connect(scheduling_db,
                                           detect_types=sqlite3.PARSE_DECLTYPES,
                                           check_same_thread=False,
                                           isolation_level=None)
        self._cursor = self._connection.cursor()
        self._check_tables()
        self._schedules = {}  # type: Dict[int, Schedule]
        self._stop = False
        self._processor = None

        Schedule.timezone = gateway_api.get_timezone()

        self._load_schedule()

    def set_webinterface(self, web_interface):
        self._web_interface = web_interface

    @property
    def schedules(self):
        return list(self._schedules.values())

    def _execute(self, *args, **kwargs):
        with self._lock:
            try:
                return self._cursor.execute(*args, **kwargs)
            except sqlite3.OperationalError:
                time.sleep(randint(1, 20) / 10.0)
                return self._cursor.execute(*args, **kwargs)

    def _check_tables(self):
        """
        Creates tables and execute migrations
        """
        self._execute('CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY, name TEXT, start INTEGER, '
                      'repeat TEXT, duration INTEGER, end INTEGER, type TEXT, arguments TEXT, status TEXT);')

    def _load_schedule(self):
        for row in self._execute('SELECT id, name, start, repeat, duration, end, type, arguments, status FROM schedules;'):
            schedule_id = row[0]
            self._schedules[schedule_id] = Schedule(id=schedule_id,
                                                    name=row[1],
                                                    start=row[2],
                                                    repeat=json.loads(row[3]) if row[3] is not None else None,
                                                    duration=row[4],
                                                    end=row[5],
                                                    schedule_type=row[6],
                                                    arguments=json.loads(row[7]) if row[7] is not None else None,
                                                    status=row[8])

    def _update_schedule_status(self, schedule_id, status):
        self._execute('UPDATE schedules SET status = ? WHERE id = ?;', (status, schedule_id))
        self._schedules[schedule_id].status = status

    def remove_schedule(self, schedule_id):
        self._execute('DELETE FROM schedules WHERE id = ?;', (schedule_id,))
        self._schedules.pop(schedule_id, None)

    def add_schedule(self, name, start, schedule_type, arguments, repeat, duration, end):
        self._validate(name, start, schedule_type, arguments, repeat, duration, end)
        self._execute('INSERT INTO schedules (name, start, repeat, duration, end, type, arguments, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                      (name,
                       start,
                       json.dumps(repeat) if repeat is not None else None,
                       duration,
                       end,
                       schedule_type,
                       json.dumps(arguments) if arguments is not None else None,
                       'ACTIVE'))
        self._load_schedule()

    def start(self):
        self._stop = False
        self._processor = Thread(target=self._process, name='SchedulingController processor')
        self._processor.daemon = True
        self._processor.start()

    def stop(self):
        self._stop = True

    def _process(self):
        while self._stop is False:
            for schedule_id in list(self._schedules.keys()):
                schedule = self._schedules.get(schedule_id)
                if schedule is None:
                    continue
                if schedule.status == 'ACTIVE' and schedule.is_due:
                    thread = Thread(target=self._execute_schedule, args=(schedule,),
                                    name='SchedulingController executor')
                    thread.daemon = True
                    thread.start()
            now = time.time()
            time.sleep(now - now % 60 + 60 - now)  # Wait for the next minute mark

    def _execute_schedule(self, schedule):
        """
        :param schedule: Schedule to execute
        :type schedule: gateway.scheduling.Schedule
        """
        if schedule.is_running:
            return
        try:
            schedule.is_running = True
            # Execute
            if schedule.schedule_type == 'GROUP_ACTION':
                self._group_action_controller.do_group_action(schedule.arguments)
            elif schedule.schedule_type == 'BASIC_ACTION':
                self._gateway_api.do_basic_action(**schedule.arguments)
            elif schedule.schedule_type == 'LOCAL_API':
                func = getattr(self._web_interface, schedule.arguments['name'])
                func(**schedule.arguments['parameters'])
            else:
                logger.warning('Did not process schedule {0}'.format(schedule.name))

            # Cleanup or prepare for next run
            schedule.last_executed = time.time()
            if schedule.has_ended:
                self._update_schedule_status(schedule.id, 'COMPLETED')
        except CommunicationTimedOutException:
            logger.error('Got error while executing schedule: CommunicationTimedOutException')
        except Exception as ex:
            logger.error('Got error while executing schedule: {0}'.format(ex))
            schedule.last_executed = time.time()
        finally:
            schedule.is_running = False

    def _validate(self, name, start, schedule_type, arguments, repeat, duration, end):
        if name is None or not isinstance(name, six.string_types) or name.strip() == '':
            raise RuntimeError('A schedule must have a name')
        # Check whether the requested type is valid
        accepted_types = ['GROUP_ACTION', 'BASIC_ACTION', 'LOCAL_API']
        if schedule_type not in accepted_types:
            raise RuntimeError('Unknown schedule type. Allowed: {0}'.format(', '.join(accepted_types)))
        # Check duration/repeat/end combinations
        if repeat is None:
            if end is not None:
                raise RuntimeError('No `end` is allowed when it is a non-repeated schedule')
        else:
            if not croniter.is_valid(repeat):
                raise RuntimeError('Invalid `repeat`. Should be a cron-style string. See croniter documentation')
        if duration is not None and duration <= 60:
            raise RuntimeError('If a duration is specified, it should be at least more than 60s')
        # Type specifc checks
        if schedule_type == 'BASIC_ACTION':
            if duration is not None:
                raise RuntimeError('A schedule of type BASIC_ACTION does not have a duration. It is a one-time trigger')
            if not isinstance(arguments, dict) or 'action_type' not in arguments or not isinstance(arguments['action_type'], int) or \
                    'action_number' not in arguments or not isinstance(arguments['action_number'], int) or len(arguments) != 2:
                raise RuntimeError('The arguments of a BASIC_ACTION schedule must be of type dict with arguments `action_type` and `action_number`')
        elif schedule_type == 'GROUP_ACTION':
            if duration is not None:
                raise RuntimeError('A schedule of type GROUP_ACTION does not have a duration. It is a one-time trigger')
            if not isinstance(arguments, int) or arguments < 0 or arguments > 254:
                raise RuntimeError('The arguments of a GROUP_ACTION schedule must be an integer, representing the Group Action to be executed')
        elif schedule_type == 'LOCAL_API':
            if duration is not None:
                raise RuntimeError('A schedule of type LOCAL_API does not have a duration. It is a one-time trigger')
            if not isinstance(arguments, dict) or 'name' not in arguments or 'parameters' not in arguments or not isinstance(arguments['parameters'], dict):
                raise RuntimeError('The arguments of a LOCAL_API schedule must be of type dict with arguments `name` and `parameters`')
            func = getattr(self._web_interface, arguments['name']) if hasattr(self._web_interface, arguments['name']) else None
            if func is None or not callable(func) or not hasattr(func, 'plugin_exposed') or getattr(func, 'plugin_exposed') is False:
                raise RuntimeError('The arguments of a LOCAL_API schedule must specify a valid and (plugin_)exposed call')
            check = getattr(func, 'check')
            if check is not None:
                params_parser(arguments['parameters'], check)
