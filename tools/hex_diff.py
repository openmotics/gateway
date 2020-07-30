#!/bin/python2
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
import os
import sys
from intelhex import IntelHex


WIDTH = 32


def diff(hex_filename_1, hex_filename_2):
    hex_1 = IntelHex(hex_filename_1)
    hex_2 = IntelHex(hex_filename_2)
    max_address = max(hex_1.maxaddr(), hex_2.maxaddr())

    print('+-{0}-+-{1}-+-{1}-+'.format('-' * 6, '-' * WIDTH * 2))
    print('| Addr   | {0} | {1} |'.format(hex_filename_1.split('/')[-1].ljust(WIDTH * 2),
                                          hex_filename_2.split('/')[-1].ljust(WIDTH * 2)))
    print('+-{0}-+-{1}-+-{1}-+'.format('-' * 6, '-' * WIDTH * 2))

    previous_data_1, previous_data_2 = None, None
    start_skip = None
    end_skip = None
    for address in xrange(0, max_address, WIDTH):
        data_1 = []
        data_2 = []
        for offset in xrange(WIDTH):
            data_1.append(hex_1[address + offset])
            data_2.append(hex_2[address + offset])

        # Reducing output
        if data_1 == previous_data_1 and data_2 == previous_data_2:
            previous_data_1, previous_data_2 = data_1, data_2
            if start_skip is None:
                start_skip = address
            end_skip = address
            continue
        else:
            if start_skip is not None:
                _print('...', previous_data_1, previous_data_2)
                _print(end_skip, previous_data_1, previous_data_2)
        start_skip = None
        end_skip = None
        previous_data_1, previous_data_2 = data_1, data_2

        # Printing
        _print(address, data_1, data_2)

    print('+-{0}-+-{1}-+-{1}-+'.format('-' * 6, '-' * WIDTH * 2))


def _print(address, data_1, data_2):
    # Printing
    if address == '...':
        formatted_address = ' ...  '
    else:
        formatted_address = '{:06X}'.format(address)
    formatted_data_1 = ''.join('{:02X}'.format(byte) for byte in data_1)
    formatted_data_2 = '='.ljust(WIDTH * 2)
    if data_1 != data_2:
        formatted_data_2 = ''.join('{:02X}'.format(byte) for byte in data_2).rjust(WIDTH * 2)
    print('| {0} | {1} | {2} |'.format(formatted_address, formatted_data_1, formatted_data_2))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print('Usage: ./hex_diff.py first.hex second.hex')
        sys.exit(1)
    filename_1 = sys.argv[1]
    if not os.path.exists(filename_1):
        print('File {0} does not exist'.format(filename_1))
        sys.exit(1)
    filename_2 = sys.argv[2]
    if not os.path.exists(filename_2):
        print('File {0} does not exist'.format(filename_2))
        sys.exit(1)

    diff(filename_1, filename_2)
