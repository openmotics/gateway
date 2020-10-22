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
Module for handling maintenance mode
"""
from __future__ import absolute_import
import logging
import socket
import random
from threading import Thread
from ioc import Injectable, Inject, INJECTED, Singleton
from gateway.maintenance_communicator import InMaintenanceModeException
from platform_utils import System

logger = logging.getLogger("openmotics")


@Injectable.named('maintenance_controller')
@Singleton
class MaintenanceController(object):

    SOCKET_TIMEOUT = 60

    @Inject
    def __init__(self, maintenance_communicator=INJECTED, ssl_private_key=INJECTED, ssl_certificate=INJECTED):
        """
        :type maintenance_communicator: gateway.maintenance_communicator.MaintenanceCommunicator
        """
        self._consumers = {}
        self._privatekey_filename = ssl_private_key
        self._certificate_filename = ssl_certificate
        self._maintenance_communicator = maintenance_communicator
        if self._maintenance_communicator:
            self._maintenance_communicator.set_receiver(self._received_data)
        self._connection = None
        self._server_thread = None

    #######################
    # Internal management #
    #######################

    def start(self):
        # type: () -> None
        if self._maintenance_communicator:
            self._maintenance_communicator.start()

    def stop(self):
        # type: () -> None
        if self._maintenance_communicator:
            self._maintenance_communicator.stop()

    def _received_data(self, message):
        try:
            if self._connection is not None:
                self._connection.sendall('{0}\n'.format(message.rstrip()))
        except Exception:
            logger.exception('Exception forwarding maintenance data to socket connection.')
        for consumer_id, callback in self._consumers.items():
            try:
                callback(message.rstrip())
            except Exception:
                logger.exception('Exception forwarding maintenance data to consumer %s', str(consumer_id))

    def _activate(self):
        if not self._maintenance_communicator.is_active():
            self._maintenance_communicator.activate()

    def _deactivate(self):
        if self._maintenance_communicator.is_active():
            self._maintenance_communicator.deactivate()

    #################
    # Subscriptions #
    #################

    def add_consumer(self, consumer_id, callback):
        self._consumers[consumer_id] = callback
        self._activate()

    def remove_consumer(self, consumer_id):
        self._consumers.pop(consumer_id, None)
        if not self._consumers:
            logger.info('Stopping maintenance mode due to no consumers.')
            self._deactivate()

    ##########
    # Socket #
    ##########

    def open_maintenace_socket(self):
        """
        Opens a TCP/SSL socket, connecting it with the maintenance service
        """
        port = random.randint(6000, 7000)
        self._server_thread = Thread(target=self._run_socket_server, args=[port])
        self._server_thread.daemon = True
        self._server_thread.start()
        return port

    def _run_socket_server(self, port):
        connection_timeout = MaintenanceController.SOCKET_TIMEOUT
        logger.info('Starting maintenance socket on port %s', port)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(connection_timeout)
        sock = System.get_ssl_socket(sock,
                                     private_key_filename=self._privatekey_filename,
                                     certificate_filename=self._certificate_filename)
        sock.bind(('', port))
        sock.listen(1)

        try:
            logger.info('Waiting for maintenance connection.')
            self._connection, address = sock.accept()
            logger.info('Maintenance connection from %s', str(address))
            self._handle_connection()
            logger.info('Maintenance session ended, closing maintenance socket')
            sock.close()
        except socket.timeout:
            logger.info('Maintenance socket timed out, closing.')
            sock.close()
        except Exception:
            logger.exception('Error in maintenance service')
            sock.close()

    def _handle_connection(self):
        """
        Handles one incoming connection.
        """
        try:
            self._connection.settimeout(1)
            self._connection.sendall('Activating maintenance mode, waiting for other actions to complete ...\n')
            self._activate()
            self._connection.sendall('Connected\n')
            while self._maintenance_communicator.is_active():
                try:
                    try:
                        data = self._connection.recv(1024)
                        if not data:
                            logger.info('Stopping maintenance mode due to no data.')
                            break
                        if data.startswith('exit'):
                            logger.info('Stopping maintenance mode due to exit.')
                            break

                        self._maintenance_communicator.write(data)
                    except Exception as exception:
                        if System.handle_socket_exception(self._connection, exception, logger):
                            continue
                        else:
                            logger.exception('Unexpected exception receiving connection data')
                            break
                except Exception:
                    logger.exception('Exception in maintenance mode')
                    break
        except InMaintenanceModeException:
            self._connection.sendall('Maintenance mode already active.\n')
        finally:
            self._deactivate()
            logger.info('Maintenance mode deactivated')
            self._connection.close()
            self._connection = None

    #######
    # I/O #
    #######

    def write(self, message):
        self._maintenance_communicator.write(message)
