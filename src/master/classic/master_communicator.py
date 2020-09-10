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
Module to communicate with the master.
"""

from __future__ import absolute_import

import logging
import select
import time
from threading import Event, Lock, Thread

from gateway.hal.master_controller import CommunicationFailure
from gateway.maintenance_controller import InMaintenanceModeException
from ioc import INJECTED, Inject
from master.classic import master_api
from master.classic.master_command import Field, printable
from serial_utils import CommunicationTimedOutException
from toolbox import Empty, Queue

logger = logging.getLogger('openmotics')

if False:  # MYPY
    from typing import Any, Dict, List, Optional, TypeVar, Union, Tuple
    from serial import Serial
    from master.classic.master_command import MasterCommandSpec, Result
    T_co = TypeVar('T_co', covariant=True)


class MasterUnavailable(CommunicationFailure):
    pass


class MasterCommunicator(object):
    """
    Uses a serial port to communicate with the master and updates the output state.
    Provides methods to send MasterCommands, Passthrough and Maintenance.
    """

    @Inject
    def __init__(self, controller_serial=INJECTED, init_master=True, verbose=False, passthrough_timeout=0.2):
        # type: (Serial, bool, bool, float) -> None
        """
        :param controller_serial: Serial port to communicate with
        :param init_master: Send an initialization sequence to the master to make sure we are in CLI mode. This can be turned of for testing.
        :param verbose: Print all serial communication to stdout.
        :param passthrough_timeout: The time to wait for an answer on a passthrough message (in sec)
        """
        self.__init_master = init_master
        self.__verbose = verbose

        self.__serial = controller_serial
        self.__serial_write_lock = Lock()
        self.__command_lock = Lock()

        self.__cid = 1

        self.__maintenance_mode = False
        self.__maintenance_queue = Queue()

        self.__update_mode = False

        self.__consumers = []  # type: List[Union[Consumer, BackgroundConsumer]]

        self.__passthrough_enabled = False
        self.__passthrough_mode = False
        self.__passthrough_timeout = passthrough_timeout
        self.__passthrough_queue = Queue()
        self.__passthrough_done = Event()

        self.__last_success = 0.0

        self.__running = False

        self.__read_thread = Thread(target=self.__read, name='MasterCommunicator read thread')
        self.__read_thread.daemon = True

        self.__communication_stats = {'calls_succeeded': [],
                                      'calls_timedout': [],
                                      'bytes_written': 0,
                                      'bytes_read': 0}  # type: Dict[str,Any]
        self.__debug_buffer = {'read': {},
                               'write': {}}  # type: Dict[str, Dict[float,str]]
        self.__debug_buffer_duration = 300

    def start(self):
        # type: () -> None
        """ Start the MasterComunicator, this starts the background read thread. """
        if self.__init_master:
            self._flush_serial_input()

        if not self.__running:
            self.__running = True
            self.__read_thread.start()

    def stop(self):
        # type: () -> None
        self.__running = False
        self.__read_thread.join()

    def update_mode_start(self):
        # type: () -> None
        if self.__maintenance_mode:
            raise InMaintenanceModeException()
        self.__update_mode = True

    def update_mode_stop(self):
        # type: () -> None
        self._flush_serial_input()
        self.__update_mode = False

    def _flush_serial_input(self):
        # type: () -> None
        def _flush():
            """ Try to read from the serial input and discard the bytes read. """
            i = 0
            data = self.__serial.read(1)
            while len(data) > 0 and i < 100:
                data = self.__serial.read(1)
                i += 1

        self.__serial.timeout = 1
        self.__serial.write(bytearray(b' ' * 18 + b'\r\n'))
        _flush()
        self.__serial.write(bytearray(b'exit\r\n'))
        _flush()
        self.__serial.write(bytearray(b' ' * 10))
        _flush()
        self.__serial.timeout = None  # TODO: make non blocking

    def enable_passthrough(self):
        self.__passthrough_enabled = True

    def get_communication_statistics(self):
        return self.__communication_stats

    def get_debug_buffer(self):
        return self.__debug_buffer

    def get_seconds_since_last_success(self):
        """ Get the number of seconds since the last successful communication. """
        if self.__last_success == 0:
            return 0  # No communication - return 0 sec since last success
        else:
            return time.time() - self.__last_success

    def __get_cid(self):
        """ Get a communication id """
        (ret, self.__cid) = (self.__cid, (self.__cid % 255) + 1)
        return ret

    def __write_to_serial(self, data):
        """ Write data to the serial port.

        :param data: the data to write
        :type data: string
        """
        # Don't touch the serial during updates.
        if self.__update_mode:
            raise MasterUnavailable()

        with self.__serial_write_lock:
            if self.__verbose:
                logger.info('Writing to Master serial:   {0}'.format(printable(data)))

            threshold = time.time() - self.__debug_buffer_duration
            self.__debug_buffer['write'][time.time()] = printable(data)
            for t in self.__debug_buffer['write'].keys():
                if t < threshold:
                    del self.__debug_buffer['write'][t]

            self.__serial.write(data)  # TODO: make non blocking
            self.__communication_stats['bytes_written'] += len(data)

    def register_consumer(self, consumer):
        """ Register a customer consumer with the communicator. An instance of :class`Consumer`
        will be removed when consumption is done. An instance of :class`BackgroundConsumer` stays
        active and is thus able to consume multiple messages.

        :param consumer: The consumer to register.
        :type consumer: Consumer or BackgroundConsumer.
        """
        # logger.debug("@> MasterCommunicator | register_consumer | {}".format(locals()))
        self.__consumers.append(consumer)

    def do_basic_action(self, action_type, action_number, timeout=2):
        # type: (int, int, Union[T_co, int]) -> Union[T_co, Dict[str,Any]]
        """
        Sends a basic action to the master with the given action type and action number
        :param action_type: The action type to execute
        :param action_number: The action number to execute
        :returns: dict containing the output fields of the command
        """
        logger.info('BA: Execute {0} {1}'.format(action_type, action_number))
        return self.do_command(
            master_api.basic_action(),
            {'action_type': action_type,
             'action_number': action_number}
        )

    def do_command(self, cmd, fields=None, timeout=2, extended_crc=False):
        # type: (MasterCommandSpec, Optional[Dict[str,Any]], Union[T_co, int], bool) -> Union[T_co, Dict[str, Any]]
        """
        Send a command over the serial port and block until an answer is received.
        If the master does not respond within the timeout period, a CommunicationTimedOutException
        is raised

        :param cmd: specification of the command to execute
        :param fields: an instance of one of the available fields
        :param timeout: maximum allowed time before a CommunicationTimedOutException is raised
        :param extended_crc: indicates whether to include the action in the CRC
        :returns: dict containing the output fields of the command
        """
        if self.__maintenance_mode:
            raise InMaintenanceModeException()

        if fields is None:
            fields = dict()

        cid = self.__get_cid()
        consumer = Consumer(cmd, cid)
        inp = cmd.create_input(cid, fields, extended_crc)

        with self.__command_lock:
            self.__consumers.append(consumer)
            self.__write_to_serial(inp)
            try:
                result = consumer.get(timeout).fields
                if cmd.output_has_crc() and not MasterCommunicator.__check_crc(cmd, result, extended_crc):
                    raise CrcCheckFailedException()
                else:
                    self.__last_success = time.time()
                    self.__communication_stats['calls_succeeded'].append(time.time())
                    self.__communication_stats['calls_succeeded'] = self.__communication_stats['calls_succeeded'][-50:]
                    return result
            except CommunicationTimedOutException:
                if cmd.action != 'FV':
                    # The FV instruction is a direct call to the Slave module. Older versions did not implement this
                    # call, so this call can timeout while it's expected. We don't take those into account.
                    self.__communication_stats['calls_timedout'].append(time.time())
                    self.__communication_stats['calls_timedout'] = self.__communication_stats['calls_timedout'][-50:]
                raise

    @staticmethod
    def __check_crc(cmd, result, extended_crc=False):
        # type: (MasterCommandSpec, Result, bool) -> bool
        """ Calculate the CRC of the data for a certain master command.

        :param cmd: instance of MasterCommandSpec.
        :param result: A dict containing the result of the master command.
        :param extended_crc: Indicates whether the action should be included in the crc
        :returns: boolean
        """
        crc = 0
        if extended_crc:
            crc += cmd.action[0]
            crc += cmd.action[1]
        for field in cmd.output_fields:
            if Field.is_crc(field):
                break
            else:
                for byte in field.encode(result[field.name]):
                    crc += byte
        return result['crc'] == bytearray([67, (crc // 256), (crc % 256)])

    def __passthrough_wait(self):
        """ Waits until the passthrough is done or a timeout is reached. """
        if not self.__passthrough_done.wait(self.__passthrough_timeout):
            logger.info('Timed out on passthrough message')

        self.__passthrough_mode = False
        self.__command_lock.release()

    def __push_passthrough_data(self, data):
        if self.__passthrough_enabled:
            self.__passthrough_queue.put(data)

    def send_passthrough_data(self, data):
        """ Send raw data on the serial port.

        :param data: string of bytes with raw command for the master.
        :raises: :class`InMaintenanceModeException` if master is in maintenance mode.
        """
        if self.__maintenance_mode:
            raise InMaintenanceModeException()

        if not self.__passthrough_mode:
            self.__command_lock.acquire()
            self.__passthrough_done.clear()
            self.__passthrough_mode = True
            passthrough_thread = Thread(target=self.__passthrough_wait)
            passthrough_thread.daemon = True
            passthrough_thread.start()

        self.__write_to_serial(data)

    def get_passthrough_data(self):
        """ Get data that wasn't consumed by do_command.
        Blocks if no data available or in maintenance mode.

        :returns: string containing unprocessed output
        """
        data = self.__passthrough_queue.get()
        if data[-4:] == bytearray(b'\r\n\r\n'):
            self.__passthrough_done.set()
        return data

    def start_maintenance_mode(self):
        """ Start maintenance mode.

        :raises: :class`InMaintenanceModeException` if master is in maintenance mode.
        """
        if self.__maintenance_mode:
            raise InMaintenanceModeException()

        self.__maintenance_queue.clear()

        self.__maintenance_mode = True
        self.send_maintenance_data(master_api.to_cli_mode().create_input(0))

    def send_maintenance_data(self, data):
        """ Send data to the master in maintenance mode.

        :param data: data to send to the master
        :type data: string
         """
        if not self.__maintenance_mode:
            raise Exception('Not in maintenance mode !')

        self.__write_to_serial(data)

    def get_maintenance_data(self):
        """ Get data from the master in maintenance mode.

        :returns: string containing unprocessed output
        """
        if not self.__maintenance_mode:
            raise Exception('Not in maintenance mode !')

        try:
            return self.__maintenance_queue.get(timeout=1)
        except Empty:
            return None

    def stop_maintenance_mode(self):
        """ Stop maintenance mode. """
        if self.__maintenance_mode:
            self.send_maintenance_data(bytearray(b'exit\r\n'))
        self.__maintenance_mode = False

    def in_maintenance_mode(self):
        """ Returns whether the MasterCommunicator is in maintenance mode. """
        return self.__maintenance_mode

    def __get_start_bytes(self):
        """ Create a dict that maps the start byte to a list of consumers. """
        start_bytes = {}  # type: Dict[int, List[Union[Consumer, BackgroundConsumer]]]
        for consumer in self.__consumers:
            start_byte = consumer.get_prefix()[0]
            if start_byte in start_bytes:
                start_bytes[start_byte].append(consumer)
            else:
                start_bytes[start_byte] = [consumer]
        return start_bytes

    def __read(self):
        """
        Code for the background read thread: reads from the serial port, checks if
        consumers for incoming bytes, if not: put in pass through buffer.
        """
        def consumer_done(_consumer):
            """ Callback for when consumer is done. ReadState does not access parent directly. """
            if isinstance(_consumer, Consumer):
                self.__consumers.remove(_consumer)
            elif isinstance(_consumer, BackgroundConsumer) and _consumer.send_to_passthrough:
                self.__push_passthrough_data(_consumer.last_cmd_data)

        class ReadState(object):
            """" The read state keeps track of the current consumer and the partial result
            for that consumer. """
            def __init__(self):
                self.current_consumer = None  # type: Optional[Union[Consumer, BackgroundConsumer]]
                self.partial_result = None  # type: Optional[Result]

            def should_resume(self):
                """ Checks whether we should resume consuming data with the current_consumer. """
                return self.current_consumer is not None

            def should_find_consumer(self):
                """ Checks whether we should find a new consumer. """
                return self.current_consumer is None

            def set_consumer(self, _consumer):
                """ Set a new consumer. """
                self.current_consumer = _consumer
                self.partial_result = None

            def consume(self, _data):
                # type: (bytearray) -> bytearray
                """
                Consume the bytes in data using the current_consumer, and return the bytes
                that were not used.
                """
                assert read_state.current_consumer
                try:
                    bytes_consumed, result, done = read_state.current_consumer.consume(_data, read_state.partial_result)
                except ValueError as value_error:
                    logger.error('Could not consume/decode message from the master: {0}'.format(value_error))
                    return bytearray()

                if done:
                    assert self.current_consumer
                    consumer_done(self.current_consumer)
                    self.current_consumer.deliver(result)

                    self.current_consumer = None
                    self.partial_result = None

                    return _data[bytes_consumed:]
                self.partial_result = result
                return bytearray()

        read_state = ReadState()
        data = bytearray()

        while self.__running:

            # Don't touch the serial during updates.
            if self.__update_mode:
                time.sleep(2)
                continue

            # TODO: use a non blocking serial instead?
            readers, _, _ = select.select([self.__serial], [], [], 2)
            if not readers:
                continue

            num_bytes = self.__serial.inWaiting()
            data += self.__serial.read(num_bytes)
            if len(data) > 0:
                self.__communication_stats['bytes_read'] += num_bytes

                threshold = time.time() - self.__debug_buffer_duration
                self.__debug_buffer['read'][time.time()] = printable(data)
                for t in self.__debug_buffer['read'].keys():
                    if t < threshold:
                        del self.__debug_buffer['read'][t]

                if self.__verbose:
                    logger.info('Reading from Master serial: {0}'.format(printable(data)))

                if read_state.should_resume():
                    data = read_state.consume(data)

                # No else here: data might not be empty when current_consumer is done
                if read_state.should_find_consumer():
                    start_bytes = self.__get_start_bytes()
                    leftovers = bytearray()  # for unconsumed bytes; these will go to the passthrough.

                    while len(data) > 0:
                        if data[0] in start_bytes:
                            # Prefixes are 3 bytes, make sure we have enough data to match
                            if len(data) >= 3:
                                match = False
                                for consumer in start_bytes[data[0]]:
                                    if data[:3] == consumer.get_prefix():
                                        # Found matching consumer
                                        read_state.set_consumer(consumer)
                                        data = read_state.consume(data[3:])  # Strip off prefix
                                        # Consumers might have changed, update start_bytes
                                        start_bytes = self.__get_start_bytes()
                                        match = True
                                        break
                                if match:
                                    continue
                            else:
                                # All commands end with '\r\n', there are no prefixes that start
                                # with \r\n so the last bytes of a command will not get stuck
                                # waiting for the next serial.read()
                                break

                        leftovers.append(data[0])
                        data = data[1:]

                    if len(leftovers) > 0:
                        if not self.__maintenance_mode:
                            self.__push_passthrough_data(leftovers)
                        else:
                            self.__maintenance_queue.put(leftovers)


class CrcCheckFailedException(Exception):
    """ This exception is raised if we receive a bad message. """
    def __init__(self):
        Exception.__init__(self)


class Consumer(object):
    """ A consumer is registered to the read thread before a command is issued.  If an output
    matches the consumer, the output will unblock the get() caller. """

    def __init__(self, cmd, cid):
        # logger.debug("@> masterCommunicator | consumer | init: {}".format(locals()))
        self.cmd = cmd  # type: MasterCommandSpec
        self.cid = cid  # type: int
        self.__queue = Queue()

    def get_prefix(self):
        """ Get the prefix of the answer from the master. """
        return self.cmd.output_action + bytearray([self.cid])

    def consume(self, data, partial_result):
        # logger.debug("@> masterCommunicator | consumer | consume: {}".format(locals()))
        # type: (bytearray, Optional[Result]) -> Tuple[int, Result, bool]
        """ Consume data. """
        return self.cmd.consume_output(data, partial_result)

    def get(self, timeout):
        """
        Wait until the master replies or the timeout expires.

        :param timeout: timeout in seconds
        :raises: :class`CommunicationTimedOutException` if master did not respond in time
        :returns: dict containing the output fields of the command
        """
        try:
            return self.__queue.get(timeout=timeout)
        except Empty:
            raise CommunicationTimedOutException()

    def deliver(self, output):
        # type: (Result) -> None
        """ Deliver output to the thread waiting on get(). """
        self.__queue.put(output)


class BackgroundConsumer(object):
    """
    A consumer that runs in the background. The BackgroundConsumer does not provide get()
    but does a callback to a function whenever a message was consumed.
    """

    def __init__(self, cmd, cid, callback, send_to_passthrough=False):
        """
        Create a background consumer using a cmd, cid and callback.

        :param cmd: the MasterCommand to consume.
        :param cid: the communication id.
        :param callback: function to call when an instance was found.
        :param send_to_passthrough: whether to send the command to the passthrough.
        """
        # logger.debug("@> masterCommunicator | backroundConsumer | init: {}".format(locals()))
        self.cmd = cmd
        self.cid = cid
        self.callback = callback
        self.last_cmd_data = None  # type: Optional[bytearray]  # Keep the data of the last command.
        self.send_to_passthrough = send_to_passthrough

    def get_prefix(self):
        # type: () -> bytearray
        """ Get the prefix of the answer from the master. """
        return bytearray(self.cmd.output_action) + bytearray([self.cid])

    def consume(self, data, partial_result):
        # type: (bytearray, Optional[Result]) -> Tuple[int, Result, bool]
        """ Consume data. """
        # logger.debug("@> masterCommunicator | backgroundConsumer | consume: {}".format(locals()))
        (bytes_consumed, last_result, done) = self.cmd.consume_output(data, partial_result)
        self.last_cmd_data = (self.get_prefix() + last_result.actual_bytes) if done else None
        return bytes_consumed, last_result, done

    def deliver(self, output):
        # type: (Result) -> None
        """ Deliver output to the thread waiting on get(). """
        # logger.debug("@> masterCommunicator | backgroundConsumer | deliver: {}".format(locals()))
        try:
            self.callback(output)
        except Exception:
            logger.exception('Unexpected exception delivering BackgroundConsumer payload')
