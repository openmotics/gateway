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
Configuration controller
"""

from __future__ import absolute_import
import time
import sqlite3
import logging
import ujson as json
from random import randint
from ioc import Injectable, Inject, Singleton, INJECTED

logger = logging.getLogger("openmotics")


@Injectable.named('configuration_controller')
@Singleton
class ConfigurationController(object):

    @Inject
    def __init__(self, config_db=INJECTED, config_db_lock=INJECTED):
        """
        Constructs a new ConfigController.

        :param config_db: filename of the sqlite database used to store the configuration
        :param config_db_lock: DB lock
        """
        self.__lock = config_db_lock
        self.__connection = sqlite3.connect(config_db,
                                            detect_types=sqlite3.PARSE_DECLTYPES,
                                            check_same_thread=False,
                                            isolation_level=None)
        self.__cursor = self.__connection.cursor()
        self.__check_tables()

    def __execute(self, *args, **kwargs):
        with self.__lock:
            try:
                return self.__cursor.execute(*args, **kwargs)
            except (sqlite3.OperationalError, sqlite3.InterfaceError):
                time.sleep(randint(1, 20) / 10.0)
                return self.__cursor.execute(*args, **kwargs)

    def __check_tables(self):
        """
        Creates tables and execute migrations
        """
        self.__execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, setting TEXT UNIQUE, data TEXT);')
        for key, default_value in {'cloud_enabled': True,
                                   'cloud_endpoint': 'cloud.openmotics.com',
                                   'cloud_endpoint_metrics': 'portal/metrics/',
                                   'cloud_metrics_types': [],
                                   'cloud_metrics_sources': [],
                                   'cloud_metrics_enabled|energy': True,
                                   'cloud_metrics_enabled|counter': True,
                                   'cloud_metrics_batch_size': 50,
                                   'cloud_metrics_min_interval': 300,
                                   'cloud_support': False,
                                   'cors_enabled': False}.items():
            if self.get(key) is None:
                self.set(key, default_value)

    def get(self, key, fallback=None):
        for entry in self.__execute('SELECT data FROM settings WHERE setting=?;', (key.lower(),)):
            return json.loads(entry[0])
        return fallback

    def set(self, key, value):
        self.__execute('INSERT OR REPLACE INTO settings (setting, data) VALUES (?, ?);',
                       (key.lower(), json.dumps(value)))

    def remove(self, key):
        self.__execute('DELETE FROM settings WHERE setting=?;', (key.lower(),))

    def close(self):
        """ Close the database connection. """
        self.__connection.close()
