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
The self test scripts runs the 'echo + 1' routine on the RS485 and 2 RS232
ports.
"""

from __future__ import absolute_import
import threading
import sys

from serial import Serial
from serial_utils import RS485


def echo_plus_one(name, serial):
    """ For each character received on the rx channel of the serial port,
    the character + 1 is send on transmit channel of the serial port.
    """
    while True:
        try:
            data = serial.read(1)
            if bool(data) and data[0] != '\x00':
                print("Read '%s' from %s" % (data, name))
                serial.write(chr((ord(data[0]) + 1) % 256))
        except Exception:
            traceback.print_exc()


def start_echo_plus_one(name, serial):
    """ Runs echo_plus_one in a separate thread. """
    thread = threading.Thread(target=echo_plus_one, args=(name, serial))
    thread.setName("Echo thread %s" % name)
    thread.start()


if __name__ == "__main__":
    print("Starting tty echo's...")
    for tty in ["/dev/ttyO1", "/dev/ttyO2", "/dev/ttyO5"]:
        sys.stdout.write("Starting tty echo on %s... " % tty)
        start_echo_plus_one(tty, Serial(tty, 115200))
        sys.stdout.write("Done\n")

    for rs485 in ["/dev/ttyO4"]:
        sys.stdout.write("Starting rs485 echo on %s... " % rs485)
        start_echo_plus_one(rs485, RS485(Serial(rs485, 115200)))
        sys.stdout.write("Done\n")
