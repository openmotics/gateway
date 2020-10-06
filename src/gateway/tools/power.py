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
Tool to bootload the power modules from the command line.
"""
from __future__ import absolute_import
from platform_utils import System
System.import_libs()

import intelhex
import constants
import sys
import argparse
import logging
import time
from logging import handlers
from ioc import INJECTED, Inject
from serial_utils import CommunicationTimedOutException
from power import power_api
from gateway.initialize import setup_minimal_power_platform

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


class HexReader(object):
    """ Reads the hex from file and returns it in the OpenMotics format. """

    def __init__(self, hex_file):
        """ Constructor with the name of the hex file. """
        self.__hex = intelhex.IntelHex(hex_file)
        self.__crc = 0

    def get_bytes_version_8(self, address):
        """ Get the 192 bytes from the hex file, with 3 address bytes prepended. """
        data_bytes = [address % 256,
                      (address % 65536) / 256,
                      address / 65536]

        iaddress = address * 2
        for i in range(64):
            data0 = self.__hex[iaddress + (4 * i) + 0]
            data1 = self.__hex[iaddress + (4 * i) + 1]
            data2 = self.__hex[iaddress + (4 * i) + 2]

            if address == 0 and i == 0:  # Set the start address to the bootloader: 0x400
                data1 = 4

            data_bytes.append(data0)
            data_bytes.append(data1)
            data_bytes.append(data2)

            if not (address == 43904 and i >= 62):  # Don't include the CRC bytes in the CRC
                self.__crc += data0 + data1 + data2

        if address == 43904:  # Add the CRC at the end of the program
            data_bytes[-1] = self.__crc % 256
            data_bytes[-2] = (self.__crc % (256 * 256)) / 256
            data_bytes[-3] = (self.__crc % (256 * 256 * 256)) / (256 * 256)
            data_bytes[-4] = (self.__crc % (256 * 256 * 256 * 256)) / (256 * 256 * 256)

        return data_bytes

    @staticmethod
    def int_to_array_12(integer):
        """ Convert an integer to an array for the 12 port energy module. """
        return [integer % 256, (integer % 65536) / 256, (integer / 65536) % 256, (integer / 65536) / 256]

    def get_bytes_version_12(self, address):
        """ Get the 128 bytes from the hex file, with 4 address bytes prepended. """
        data_bytes = self.int_to_array_12(address)

        for i in range(32):
            data0 = self.__hex[address + (4 * i) + 0]
            data1 = self.__hex[address + (4 * i) + 1]
            data2 = self.__hex[address + (4 * i) + 2]
            data3 = self.__hex[address + (4 * i) + 3]

            data_bytes.append(data0)
            data_bytes.append(data1)
            data_bytes.append(data2)
            data_bytes.append(data3)

            if not (address == 486801280 and i == 31):
                self.__crc += data0 + data1 + data2 + data3

        if address == 486801280:
            data_bytes = data_bytes[:-4]
            data_bytes += self.int_to_array_12(self.get_crc())

        return data_bytes

    def get_crc(self):
        """ Get the crc for the block that have been read from the HexReader. """
        return self.__crc


def get_module_firmware_version(module_address, version, power_communicator):
    """
    Get the version of a power module.

    :param module_address: The address of a power module (integer).
    :param version: The version
    :param power_communicator: Communication with the power modules.
    """
    raw_version = power_communicator.do_command(module_address, power_api.get_version(version))
    if version == power_api.P1_CONCENTRATOR:
        return '{0}.{1}.{2}'.format(raw_version[1], raw_version[2], raw_version[3]), str(raw_version[0])
    else:
        cleaned_version = raw_version[0].split('\x00', 1)[0]
        parsed_version = cleaned_version.split('_')
        if len(parsed_version) != 4:
            return cleaned_version, None
        return '{0}.{1}.{2}'.format(parsed_version[1], parsed_version[2], parsed_version[3]), str(parsed_version[0])


def bootload_power_module(module_address, hex_file, power_communicator, version):
    """
    Bootload a 8 port power module.

    :param module_address: The address of a power module (integer).
    :param hex_file: The filename of the hex file to write.
    :param power_communicator: Communication with the power modules.
    :param version: Version of the provided hexfile
    """
    firmware_version, hardware_version = get_module_firmware_version(module_address, power_api.POWER_MODULE, power_communicator)
    logger.info('P{0} - Version: {1} ({2})'.format(module_address, firmware_version, hardware_version))
    if firmware_version == version:
        logger.info('P{0} - Already up-to-date. Skipping')
        return
    logger.info('P{0} - Start bootloading'.format(module_address))
    reader = HexReader(hex_file)

    logger.info('P{0} - Going to bootloader'.format(module_address))
    power_communicator.do_command(module_address, power_api.bootloader_goto(power_api.POWER_MODULE), 10)

    logger.info('P{0} - Reading chip id'.format(module_address))
    chip_id = power_communicator.do_command(module_address, power_api.bootloader_read_id())
    if chip_id[0] != 213:
        raise Exception('Unknown chip id: {0}'.format(chip_id[0]))

    logger.info('P{0} - Writing vector tabel'.format(module_address))
    for address in range(0, 1024, 128):      # 0x000 - 0x400
        data = reader.get_bytes_version_8(address)
        power_communicator.do_command(module_address, power_api.bootloader_write_code(power_api.POWER_MODULE), *data)

    logger.info('P{0} -  Writing code'.format(module_address))
    for address in range(8192, 44032, 128):  # 0x2000 - 0xAC00
        data = reader.get_bytes_version_8(address)
        power_communicator.do_command(module_address, power_api.bootloader_write_code(power_api.POWER_MODULE), *data)

    logger.info('P{0} - Jumping to application'.format(module_address))
    power_communicator.do_command(module_address, power_api.bootloader_jump_application())

    logger.info('P{0} - Done'.format(module_address))


def bootload_energy_module(module_address, hex_file, power_communicator, version):
    """
    Bootload a 12 port power module.

    :param module_address: The address of a power module (integer).
    :param hex_file: The filename of the hex file to write.
    :param power_communicator: Communication with the power modules.
    :param version: Version of the provided hexfile
    """
    firmware_version, hardware_version = get_module_firmware_version(module_address, power_api.ENERGY_MODULE, power_communicator)
    logger.info('E{0} - Version: {1} ({2})'.format(module_address, firmware_version, hardware_version))
    if firmware_version == version:
        logger.info('E{0} - Already up-to-date. Skipping')
        return
    logger.info('E{0} - Start bootloading'.format(module_address))

    try:
        logger.info('E{0} - Reading calibration data'.format(module_address))
        calibration_data = list(power_communicator.do_command(module_address, power_api.read_eeprom(12, 100), *[256, 100]))
        logger.info('E{0} - Calibration data: {1}'.format(module_address, ','.join([str(d) for d in calibration_data])))
    except Exception as ex:
        logger.info('E{0} - Could not read calibration data: {1}'.format(module_address, ex))
        calibration_data = None

    reader = HexReader(hex_file)

    logger.info('E{0} - Going to bootloader'.format(module_address))
    power_communicator.do_command(module_address, power_api.bootloader_goto(power_api.ENERGY_MODULE), 10)
    logger.info('E{0} - Bootloader version: {1}'.format(module_address, get_module_firmware_version(module_address, power_api.ENERGY_MODULE, power_communicator)))

    try:
        logger.info('E{0} - Erasing code...'.format(module_address))
        for page in range(6, 64):
            power_communicator.do_command(module_address, power_api.bootloader_erase_code(), page)

        logger.info('E{0} - Writing code...'.format(module_address))
        for address in range(0x1D006000, 0x1D03FFFB, 128):
            data = reader.get_bytes_version_12(address)
            power_communicator.do_command(module_address, power_api.bootloader_write_code(power_api.ENERGY_MODULE), *data)
    finally:
        logger.info('E{0} - Jumping to application'.format(module_address))
        power_communicator.do_command(module_address, power_api.bootloader_jump_application())

    tries = 0
    while True:
        try:
            tries += 1
            logger.info('E{0} - Waiting for application...'.format(module_address))
            logger.info('E{0} - Version: {1}'.format(module_address, get_module_firmware_version(module_address, power_api.ENERGY_MODULE, power_communicator)))
            break
        except Exception:
            if tries >= 3:
                raise
            time.sleep(1)

    if calibration_data is not None:
        time.sleep(1)
        logger.info('E{0} - Restoring calibration data'.format(module_address))
        power_communicator.do_command(module_address, power_api.write_eeprom(12, 100), *([256] + calibration_data))

    logger.info('E{0} - Done'.format(module_address))


def bootload_p1_concentrator(module_address, hex_file, power_communicator, version):
    """ Bootload a P1 Concentrator module """

    firmware_version, hardware_version = get_module_firmware_version(module_address, power_api.P1_CONCENTRATOR, power_communicator)
    logger.info('C{0} - Version: {1} ({2})'.format(module_address, firmware_version, hardware_version))
    if firmware_version == version:
        logger.info('C{0} - Already up-to-date. Skipping')
        return
    logger.info('C{0} - Start bootloading'.format(module_address))

    logger.info('C{0} - Going to bootloader'.format(module_address))
    power_communicator.do_command(module_address, power_api.bootloader_goto(power_api.P1_CONCENTRATOR), 10)

    # No clue yet. Most likely this will use the same approach as regular master slave modules

    logger.info('C{0} - Done'.format(module_address))


def main():
    """ The main function. """
    logger.info('Bootloader for Energy/Power Modules and P1 Concentrator')
    logger.info('Command: {0}'.format(' '.join(sys.argv)))

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

    args = parser.parse_args()

    if not args.file and not args.scan:
        parser.print_help()
        return

    setup_minimal_power_platform()

    @Inject
    def _get_from_ioc(power_store=INJECTED, power_communicator=INJECTED, power_serial=INJECTED):
        return power_store, power_communicator, power_serial

    store, communicator, serial = _get_from_ioc()
    serial.start()

    if args.scan:
        logger.info('Scanning addresses 0-255...')
        for address in range(256):
            for module_type, version in {'E/P': power_api.ENERGY_MODULE,
                                         'C': power_api.P1_CONCENTRATOR}.items():
                try:
                    logger.info('{0}{1} - Version: {2}'.format(
                        module_type, address, get_module_firmware_version(address, version, communicator)
                    ))
                except Exception:
                    pass
        logger.info('Scan completed')
        return

    version = power_api.ENERGY_MODULE
    if args.old:
        version = power_api.POWER_MODULE
    elif args.p1c:
        version = power_api.P1_CONCENTRATOR

    def _bootload(_module, _module_address, filename):
        try:
            if version == _module['version'] == power_api.POWER_MODULE:
                bootload_power_module(_module_address, filename, communicator, args.firmware_version)
            elif version == _module['version'] == power_api.ENERGY_MODULE:
                bootload_energy_module(_module_address, filename, communicator, args.firmware_version)
            elif version == _module['version'] == power_api.P1_CONCENTRATOR:
                bootload_p1_concentrator(_module_address, filename, communicator, args.firmware_version)
        except CommunicationTimedOutException:
            logger.warning('E{0} - Module unavailable. Skipping...'.format(address))
        except Exception:
            logger.exception('E{0} - Unexpected exception during bootload. Skipping...'.format(address))

    if args.address or args.all:
        power_modules = store.get_power_modules()
        if args.all:
            for module_id in power_modules:
                module = power_modules[module_id]
                address = module['address']
                _bootload(module, address, args.file)
        else:
            address = args.address
            modules = [module for module in power_modules.values() if module['address'] == address]
            if len(modules) != 1:
                logger.info('ERROR: Cannot find a module with address {0}'.format(address))
                sys.exit(0)
            module = modules[0]
            _bootload(module, address, args.file)
    else:
        parser.print_help()


if __name__ == '__main__':
    setup_logger()
    main()
