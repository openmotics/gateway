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

from platform_utils import System
System.import_libs()

import argparse
import logging
from logging import handlers
from six.moves.configparser import ConfigParser

import constants
from gateway.initialize import setup_minimal_master_platform
from gateway.tools.master import master_tool


logger = logging.getLogger('openmotics')


def setup_logger():
    # type: () -> None
    """ Setup the OpenMotics logger. """

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

    handler = handlers.RotatingFileHandler(constants.get_update_log_location(), maxBytes=3 * 1024 ** 2, backupCount=2)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)


def main():
    # type: () -> None
    """ The main function. """
    parser = argparse.ArgumentParser(description='Tool to control the master.')
    parser.add_argument('--port', dest='port', action='store_true',
                        help='get the serial port device')
    parser.add_argument('--sync', dest='sync', action='store_true',
                        help='sync the serial port')
    parser.add_argument('--reset', dest='reset', action='store_true',
                        help='reset the master')
    parser.add_argument('--hard-reset', dest='hardreset', action='store_true',
                        help='perform a hardware reset on the master')
    parser.add_argument('--version', dest='version', action='store_true',
                        help='get the version of the master')
    parser.add_argument('--wipe', dest='wipe', action='store_true',
                        help='wip the master eeprom')
    parser.add_argument('--update', dest='update', action='store_true',
                        help='update the master firmware')
    parser.add_argument('--master-firmware-classic',
                        help='path to the hexfile with the classic firmware')
    parser.add_argument('--master-firmware-core',
                        help='path to the hexfile with the core+ firmware')

    args = parser.parse_args()

    setup_logger()

    config = ConfigParser()
    config.read(constants.get_config_file())

    port = config.get('OpenMotics', 'controller_serial')
    if args.port:
        print(port)
        return

    if not any([args.sync, args.version, args.reset, args.hardreset, args.wipe, args.update]):
        parser.print_help()

    setup_minimal_master_platform(port)
    master_tool(args)


if __name__ == '__main__':
    main()
