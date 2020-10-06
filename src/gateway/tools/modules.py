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
from platform_utils import System, Platform
System.import_libs()

import argparse
import constants
import time
import os
import sys
import logging
from logging import handlers
from six.moves.configparser import ConfigParser
from ioc import INJECTED, Inject
from gateway.initialize import setup_minimal_master_platform

logger = logging.getLogger("openmotics")


def setup_logger():
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
def get_communicator(master_communicator=INJECTED):
    return master_communicator


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
    module_type = args.type.upper()
    filename = args.file
    version = args.version
    gen3_firmware = module_type.endswith('3')
    if gen3_firmware:
        module_type = module_type[0]

    config = ConfigParser()
    config.read(constants.get_config_file())
    port = config.get('OpenMotics', 'controller_serial')

    setup_minimal_master_platform(port)

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


if __name__ == '__main__':
    setup_logger()
    success = main()
    if not success:
        sys.exit(1)
