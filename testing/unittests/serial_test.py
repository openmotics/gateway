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
Contains the SerialMock.

@author: fryckbos
"""

from __future__ import absolute_import
import os
import pty
import threading
import time
import unittest

import xmlrunner
from serial import Serial

from serial_utils import printable

if False:  # MYPY
    from typing import List, Optional


class DummyPty(object):
    def __init__(self, sequence):
        # type: (List[str]) -> None
        self._replies = []  # type: List[str]
        self._sequence = sequence
        self._read = threading.Event()
        master, slave = pty.openpty()
        self.fd = os.fdopen(master, 'wb', 0)
        self._serial = Serial(os.ttyname(slave))
        self._serial.timeout = None

    def master_reply(self, data):
        # type: (str) -> None
        self._replies.append(data)

    def master_wait(self, timeout=2):
        # type: (Optional[float]) -> None
        self._read.clear()
        self._read.wait(timeout)

    def read(self, size=None):
        # type: (Optional[int]) -> str
        data = self._serial.read(size)
        if data:
            self._read.set()
        return data

    def write(self, data):
        # type: (str) -> int
        if data != self._sequence[0]:
            assert printable(self._sequence[0]) == printable(data)
        self._sequence.pop(0)
        if self._replies:
            self.fd.write(self._replies[0])
            self._replies.pop(0)
        return len(data)

    def fileno(self):
        # type: () -> int
        return self._serial.fileno()

    @property
    def in_waiting(self):
        # type: () -> int
        return self._serial.in_waiting

    def inWaiting(self):  # pylint: disable=C0103
        # type: () -> int
        return self._serial.inWaiting()


def sin(data):
    """ Input for the SerialMock """
    return 'i', data


def sout(data):
    """ Output from the SerialMock """
    return 'o', data


class SerialMock(object):
    """ Mockup for :class`serial.Serial`.
    TODO Serial timeout is not implemented here
    TODO For sequence: [ sout(" "), sout("two") ]
         read() returns " "
         inWaiting() returns 3 instead of 0
    """

    def __init__(self, sequence, timeout=0):
        """ Takes a sequence of sin() and sout(). Check if we get the sin bytes on write(),
        gives the sout bytes to read(). """
        self.__sequence = sequence
        self.__timeout = timeout

        self.bytes_written = 0
        self.bytes_read = 0

    def write(self, data):
        """ Write data to serial port """
        while self.__sequence[0][0] == 'o':
            time.sleep(0.01)

        if data != self.__sequence[0][1]:
            raise Exception("Got wrong data in SerialMock: expected %s, got %s",
                            (printable(self.__sequence[0][1]), printable(data)))
        self.__sequence.pop(0)
        self.bytes_written += len(data)

    def read(self, size):
        """ Read size bytes from serial port """
        while len(self.__sequence) == 0 or self.__sequence[0][0] == 'i':
            time.sleep(0.01)

        if self.__timeout != 0 and self.__sequence[0][1] == '':
            time.sleep(self.__timeout)
            self.__sequence.pop(0)
            return ''
        else:
            ret = self.__sequence[0][1][:size]
            self.__sequence[0] = (self.__sequence[0][0], self.__sequence[0][1][size:])

            if len(self.__sequence[0][1]) == 0:
                self.__sequence.pop(0)

            self.bytes_read += len(ret)
            return ret

    def inWaiting(self): #pylint: disable=C0103
        """ Get the number of bytes pending to be read """
        if len(self.__sequence) == 0 or self.__sequence[0][0] == 'i':
            return 0
        else:
            return len(self.__sequence[0][1])

    def interrupt(self):
        """ Interrupt a read that is waiting until the end of time. """
        if len(self.__sequence) > 0:
            raise Exception("Can only interrupt read at end of stream")
        self.__sequence.append(sout("\x00"))

    def fileno(self):
        return None


class SerialMockTest(unittest.TestCase):
    """ Tests for SerialMock class """

    def test_serial_mock(self):
        """ Tests for SerialMock. """
        serial_mock = SerialMock([sin("abc"), sout("def"), sin("g"), sout("h")])
        serial_mock.write("abc")
        self.assertEqual("d", serial_mock.read(1))
        self.assertEqual(2, serial_mock.inWaiting())
        self.assertEqual("ef", serial_mock.read(2))
        serial_mock.write("g")
        self.assertEqual("h", serial_mock.read(1))
        self.assertEqual(0, serial_mock.inWaiting())

    def test_threaded_serial_mock(self):
        """ Tests for SerialMock in thread, check if reads and writes are in sequence. """
        serial_mock = SerialMock([sin("abc"), sout("def"), sin("g"), sout("h")])
        phase = {'phase':0}

        def __reader(serial, phase):
            """ Code for reading from a differen thread, checks the output and phase. """
            self.assertEqual("d", serial.read(1))
            self.assertEqual(1, phase['phase'])
            phase['phase'] = 2
            self.assertEqual(2, serial.inWaiting())
            self.assertEqual("ef", serial.read(2))

            self.assertEqual("h", serial.read(1))
            self.assertEqual(3, phase['phase'])
            self.assertEqual(0, serial.inWaiting())

        threading.Thread(target=__reader, args=(serial_mock, phase)).start()

        serial_mock.write("abc")
        phase['phase'] = 1
        serial_mock.write("g")
        self.assertEqual(2, phase['phase'])
        phase['phase'] = 3

    def test_keep_read_waiting(self):
        """ Tests for serial mock, that checks if a read() stays waiting if there is
        no data available. """
        serial_mock = SerialMock([])
        phase = {'phase':0}

        def __timeout(serial, phase):
            """ Interrupts the read to make the test finish. """
            time.sleep(0.05)
            phase['phase'] = 1
            serial.interrupt()

        threading.Thread(target=__timeout, args=(serial_mock, phase)).start()

        serial_mock.read(1)
        self.assertEqual(1, phase['phase'])


if __name__ == "__main__":
    unittest.main(testRunner=xmlrunner.XMLTestRunner(output='gw-unit-reports'))
