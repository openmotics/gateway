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
Tool to control the master from the command line.
"""
from __future__ import absolute_import
from platform_utils import Platform, System

System.import_libs()

import argparse
import logging
import shutil
import sys
from logging import handlers
from six.moves.configparser import ConfigParser

import constants
from gateway.initialize import setup_minimal_master_platform
from ioc import INJECTED, Inject
from serial_utils import CommunicationTimedOutException

if False:  # MYPY
    from typing import Union
    from gateway.hal.master_controller import MasterController
    from master.classic.master_communicator import MasterCommunicator
    from master.core.core_communicator import CoreCommunicator


logger = logging.getLogger('openmotics')


def setup_logger():
    # type: () -> None
    """ Setup the OpenMotics logger. """

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)

    handler = handlers.RotatingFileHandler(constants.get_update_log_location(), maxBytes=3 * 1024 ** 2, backupCount=2)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


@Inject
def master_sync(master_controller=INJECTED):
    # type: (MasterController) -> None
    logger.info('Sync...')
    try:
        master_controller.get_status()
        logger.info('Done sync')
    except CommunicationTimedOutException:
        logger.error('Failed sync')
        sys.exit(1)


@Inject
def master_version(master_controller=INJECTED):
    # type: (MasterController) -> None
    status = master_controller.get_status()
    print('{} H{}'.format(status['version'], status['hw_version']))


@Inject
def master_reset(master_controller=INJECTED):
    # type: (MasterController) -> None
    logger.info('Resetting...')
    try:
        master_controller.reset()
        logger.info('Done resetting')
    except CommunicationTimedOutException:
        logger.error('Failed resetting')
        sys.exit(1)


@Inject
def master_cold_reset(master_controller=INJECTED):
    # type: (MasterController) -> None
    logger.info('Performing hard reset...')
    master_controller.cold_reset()
    logger.info('Done performing hard reset')


@Inject
def master_factory_reset(master_controller=INJECTED):
    # type: (MasterController) -> None
    logger.info('Wiping the master...')
    master_controller.factory_reset()
    logger.info('Done wiping the master')


@Inject
def master_update(firmware, master_controller=INJECTED):
    # type: (str, MasterController) -> None
    try:
        master_controller.update_master(hex_filename=firmware)
        shutil.copy(firmware, '/opt/openmotics/firmware.hex')
    except Exception as ex:
        logger.error('Failed to update master: {0}'.format(ex))
        sys.exit(1)


@Inject
def get_communicator(master_communicator=INJECTED):
    # type: (Union[CoreCommunicator, MasterCommunicator]) -> Union[CoreCommunicator, MasterCommunicator]
    return master_communicator


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
    platform = Platform.get_platform()

    if args.hardreset:
        master_cold_reset()
        return
    elif args.update:
        if platform == Platform.Type.CORE_PLUS:
            firmware = args.master_firmware_core
            if not firmware:
                print('error: --master-firmware-core is required to update')
                sys.exit(1)
        else:
            firmware = args.master_firmware_classic
            if not firmware:
                print('error: --master-firmware-classic is required to update')
                sys.exit(1)
        master_update(firmware)
        return

    communicator = get_communicator()
    communicator.start()
    try:
        if args.sync:
            master_sync()
        elif args.version:
            master_version()
        elif args.reset:
            master_reset()
        elif args.wipe:
            master_factory_reset()
    finally:
        communicator.stop()


if __name__ == '__main__':
    main()
