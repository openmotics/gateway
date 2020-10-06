#!/usr/bin/env python
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

import argparse
import logging
import sys
from logging import handlers

import constants
from gateway.settings import setup_global_arguments
from openmotics_cli import minimal_power

logger = logging.getLogger('openmotics')


def setup_update_log():
    # type: () -> None
    handler = handlers.RotatingFileHandler(constants.get_update_log_location(), maxBytes=3 * 1024 ** 2, backupCount=2)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)


@minimal_power()
def cmd_bootloader(args):
    # type: (argparse.Namespace) -> None
    setup_update_log()

    from gateway.tools.power import power_bootloader
    power_bootloader(args)


def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description='Tool to bootload a module.')
    parser.add_argument('--address', dest='address', type=int,
                        help='the address of the module to bootload')
    parser.add_argument('--all', dest='all', action='store_true',
                        help='bootload all modules')
    parser.add_argument('--file', dest='file',
                        help='the filename of the hex file to bootload')
    parser.add_argument('--8', dest='old', action='store_true',
                        help='bootload for the 8-port power modules')
    parser.add_argument('--p1c', dest='p1c', action='store_true',
                        help='bootload for the P1 concentrator modules')
    parser.add_argument('--verbose', dest='verbose', action='store_true',
                        help='show the serial output')
    parser.add_argument('--scan', dest='scan', action='store_true',
                        help='Scan the energy bus for modules')
    parser.add_argument('--version', dest='firmware_version', required=False,
                        help='version of the provided hex file')
    setup_global_arguments(parser)
    args = parser.parse_args()

    if not args.file and not args.scan:
        parser.print_help()
        return

    logger.info('Bootloader for Energy/Power Modules and P1 Concentrator')
    logger.info('Command: {0}'.format(' '.join(sys.argv)))

    cmd_bootloader(args)


if __name__ == '__main__':
    main()
