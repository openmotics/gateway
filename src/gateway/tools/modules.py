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
Tool to bootload the slave modules (output, dimmer, input, temperature, ...)
"""
from __future__ import absolute_import

import logging
import os
import time
from argparse import Namespace

from ioc import INJECTED, Inject
from platform_utils import Platform

logger = logging.getLogger("openmotics")


@Inject
def get_communicator(master_communicator=INJECTED):
    return master_communicator


def modules_bootloader(args):
    # type: (Namespace) -> bool
    module_type = args.type.upper()
    filename = args.file
    version = args.version
    gen3_firmware = module_type.endswith('3')
    if gen3_firmware:
        module_type = module_type[0]

    communicator = get_communicator()
    communicator.start()
    try:
        if Platform.get_platform() == Platform.Type.CORE_PLUS:
            from master.core.slave_updater import SlaveUpdater

            update_success = SlaveUpdater.update_all(module_type=module_type,
                                                     hex_filename=filename,
                                                     gen3_firmware=gen3_firmware,
                                                     version=version)
        else:
            from master.classic.slave_updater import bootload_modules

            try:
                if os.path.getsize(args.file) <= 0:
                    print('Could not read hex or file is empty: {0}'.format(args.file))
                    return False
            except OSError as ex:
                print('Could not open hex: {0}'.format(ex))
                return False

            if module_type == 'UC':
                print('Updating uCAN modules not supported on Classic platform')
                return True  # Don't fail the update

            update_success = bootload_modules(module_type=module_type,
                                              filename=filename,
                                              gen3_firmware=gen3_firmware,
                                              version=version)
    finally:
        communicator.stop()
        time.sleep(3)

    return update_success
