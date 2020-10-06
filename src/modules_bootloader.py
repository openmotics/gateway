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

from platform_utils import System, Platform
System.import_libs()

import argparse
import time
import os
import sys
import logging
from logging import handlers

from six.moves.configparser import ConfigParser

import constants
from gateway.initialize import setup_minimal_master_platform
from gateway.tools.modules import modules_bootloader

logger = logging.getLogger('openmotics')


def setup_logger():
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
    supported_modules = ['O', 'R', 'D', 'I', 'T', 'C']
    supported_modules_gen3 = ['O3', 'R3', 'D3', 'I3', 'T3', 'C3']
    supported_can_modules = ['UC']
    all_supported_modules = supported_modules + supported_modules_gen3 + supported_can_modules

    parser = argparse.ArgumentParser(description='Tool to bootload the slave modules.')

    parser.add_argument('-t', '--type', dest='type', choices=all_supported_modules + [m.lower() for m in all_supported_modules], required=True,
                        help='The type of module to bootload (choices: {0})'.format(', '.join(all_supported_modules)))
    parser.add_argument('-f', '--file', dest='file', required=True,
                        help='The filename of the hex file to bootload')
    parser.add_argument('-v', '--version', dest='version', required=False,
                        help='The version of the firmware to flash')
    parser.add_argument('--verbose', dest='verbose', action='store_true',
                        help='Show the serial output')

    args = parser.parse_args()

    config = ConfigParser()
    config.read(constants.get_config_file())
    port = config.get('OpenMotics', 'controller_serial')

    setup_minimal_master_platform(port)

    success = modules_bootloader(args)
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    setup_logger()
    main()
