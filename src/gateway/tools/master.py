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

import logging
import shutil
import sys
from argparse import Namespace

from ioc import INJECTED, Inject
from platform_utils import Platform
from serial_utils import CommunicationTimedOutException

if False:  # MYPY
    from typing import Union
    from gateway.hal.master_controller import MasterController
    from master.classic.master_communicator import MasterCommunicator
    from master.core.core_communicator import CoreCommunicator


logger = logging.getLogger('openmotics')


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


def master_tool(args):
    # type: (Namespace) -> None
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
