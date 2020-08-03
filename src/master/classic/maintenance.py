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
The maintenance module contains the MaintenanceCommunicator class.
"""

from __future__ import absolute_import
import time
import logging
from threading import Timer, Thread
from ioc import Inject, INJECTED
from gateway.maintenance_communicator import MaintenanceCommunicator

logger = logging.getLogger('openmotics')


class MaintenanceClassicCommunicator(MaintenanceCommunicator):
    """
    The maintenance communicator handles maintenance communication with the Master
    """

    MAINTENANCE_TIMEOUT = 600

    @Inject
    def __init__(self, master_communicator=INJECTED):
        """
        Construct a MaintenanceCommunicator.

        :param master_communicator: the communication with the master.
        :type master_communicator: master.master_communicator.MasterCommunicator
        """
        self._master_communicator = master_communicator
        self._receiver_callback = None
        self._deactivated_callback = None
        self._deactivated_sent = False
        self._last_maintenance_send_time = 0
        self._maintenance_timeout_timer = None
        self._last_maintenance_send_time = 0
        self._read_data_thread = None
        self._stopped = False

    def start(self):
        pass  # Classis doesn't have a permanent running maintenance

    def stop(self):
        pass  # Classis doesn't have a permanent running maintenance

    def set_receiver(self, callback):
        self._receiver_callback = callback

    def set_deactivated(self, callback):
        self._deactivated_callback = callback

    def is_active(self):
        return self._master_communicator.in_maintenance_mode()

    def activate(self):
        """
        Activates maintenance mode, If no data is send for too long, maintenance mode will be closed automatically.
        """
        logger.info('Activating maintenance mode')
        self._last_maintenance_send_time = time.time()
        self._master_communicator.start_maintenance_mode()
        self._maintenance_timeout_timer = Timer(MaintenanceClassicCommunicator.MAINTENANCE_TIMEOUT, self._check_maintenance_timeout)
        self._maintenance_timeout_timer.start()
        self._stopped = False
        self._read_data_thread = Thread(target=self._read_data, name='Classic maintenance read thread')
        self._read_data_thread.daemon = True
        self._read_data_thread.start()
        self._deactivated_sent = False

    def deactivate(self, join=True):
        logger.info('Deactivating maintenance mode')
        self._stopped = True
        if join and self._read_data_thread is not None:
            self._read_data_thread.join()
            self._read_data_thread = None
        self._master_communicator.stop_maintenance_mode()

        if self._maintenance_timeout_timer is not None:
            self._maintenance_timeout_timer.cancel()
            self._maintenance_timeout_timer = None

        if self._deactivated_callback is not None and self._deactivated_sent is False:
            self._deactivated_callback()
            self._deactivated_sent = True

    def _check_maintenance_timeout(self):
        """
        Checks if the maintenance if the timeout is exceeded, and closes maintenance mode
        if required.
        """
        timeout = MaintenanceClassicCommunicator.MAINTENANCE_TIMEOUT
        if self._master_communicator.in_maintenance_mode():
            current_time = time.time()
            if self._last_maintenance_send_time + timeout < current_time:
                logger.info('Stopping maintenance mode because of timeout.')
                self.deactivate()
            else:
                wait_time = self._last_maintenance_send_time + timeout - current_time
                self._maintenance_timeout_timer = Timer(wait_time, self._check_maintenance_timeout)
                self._maintenance_timeout_timer.start()
        else:
            self.deactivate()

    def write(self, message):
        self._last_maintenance_send_time = time.time()
        self._master_communicator.send_maintenance_data(bytearray(b'{0}\r\n'.format(message.strip())))

    def _read_data(self):
        """ Reads from the serial port and writes to the socket. """
        buffer = ''
        while not self._stopped:
            try:
                data = self._master_communicator.get_maintenance_data()
                if data is None:
                    continue
                buffer += data.decode()
                while '\n' in buffer:
                    message, buffer = buffer.split('\n', 1)
                    if self._receiver_callback is not None:
                        try:
                            self._receiver_callback(message.rstrip())
                        except Exception:
                            logger.exception('Unexpected exception during maintenance callback')
            except Exception:
                logger.exception('Exception in maintenance read thread')
                break
        self.deactivate(join=False)
