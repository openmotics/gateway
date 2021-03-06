# Copyright (C) 2019 OpenMotics BV
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
Module to work update an uCAN
"""

from __future__ import absolute_import
import logging
import os
import struct
from intelhex import IntelHex
from master.core.ucan_api import UCANAPI
from master.core.ucan_command import UCANPalletCommandSpec, SID
from master.core.ucan_communicator import UCANCommunicator
from master.core.fields import UInt32Field

if False:  # MYPY
    from typing import Optional

logger = logging.getLogger('openmotics')


class UCANUpdater(object):
    """
    This is a class holding tools to execute uCAN updates
    """

    ADDRESS_START = 0x4
    ADDRESS_END = 0xCFF8  # End of application space. After this, 4 bytes hold the original reset vector, 4 bytes hold the CRC

    # There's a buffer of 8 segments on the uCAN. This means 7 data segments with a 1-byte header, so 49 bytes.
    # In this data stream is also the address (4 bytes) and the CRC (4 bytes) leaving 41 usefull bytes.
    MAX_FLASH_BYTES = 41

    # Bootloader timeouts
    BOOTLOADER_TIMEOUT_UPDATE = 255
    BOOTLOADER_TIMEOUT_RUNTIME = 0  # Currently needed to switch to application mode

    @staticmethod
    def update(cc_address, ucan_address, ucan_communicator, hex_filename, version):
        # type: (str, str, UCANCommunicator, str, Optional[str]) -> bool
        """ Flashes the content from an Intel HEX file to the specified uCAN """
        try:
            logger.info('Updating uCAN {0} at CC {1} to {2}'.format(ucan_address,
                                                                    cc_address,
                                                                    'v{0}'.format(version) if version is not None else 'unknown version'))

            try:
                response = ucan_communicator.do_command(cc_address, UCANAPI.get_version(), ucan_address, {})
                if response is None:
                    raise RuntimeError()
                current_version = response['firmware_version']
                logger.info('Current uCAN version: v{0}'.format(current_version))
            except Exception:
                raise RuntimeError('Could not load uCAN version')

            if current_version == version:
                logger.info('uCAN already up-to-date. Skipping')
                return True

            if not os.path.exists(hex_filename):
                raise RuntimeError('The given path does not point to an existing file')
            intel_hex = IntelHex(hex_filename)

            in_bootloader = ucan_communicator.is_ucan_in_bootloader(cc_address, ucan_address)
            if in_bootloader:
                logger.info('Bootloader active')
            else:
                logger.info('Bootloader not active, switching to bootloader')
                ucan_communicator.do_command(cc_address, UCANAPI.set_bootloader_timeout(SID.NORMAL_COMMAND), ucan_address, {'timeout': UCANUpdater.BOOTLOADER_TIMEOUT_UPDATE})
                response = ucan_communicator.do_command(cc_address, UCANAPI.reset(SID.NORMAL_COMMAND), ucan_address, {}, timeout=10)
                if response is None:
                    raise RuntimeError('Error resettings uCAN before flashing')
                if response.get('application_mode', 1) != 0:
                    raise RuntimeError('uCAN didn\'t enter bootloader after reset')
                in_bootloader = ucan_communicator.is_ucan_in_bootloader(cc_address, ucan_address)
                if not in_bootloader:
                    raise RuntimeError('Could not enter bootloader')
                logger.info('Bootloader active')

            logger.info('Erasing flash...')
            ucan_communicator.do_command(cc_address, UCANAPI.erase_flash(), ucan_address, {})
            logger.info('Erasing flash... Done')

            logger.info('Flashing contents of {0}'.format(os.path.basename(hex_filename)))
            logger.info('Flashing...')
            uint32_helper = UInt32Field('')
            address_blocks = list(range(UCANUpdater.ADDRESS_START, UCANUpdater.ADDRESS_END, UCANUpdater.MAX_FLASH_BYTES))
            total_amount = float(len(address_blocks))
            crc = 0
            total_payload = bytearray()
            logged_percentage = -1
            reset_vector = bytearray([intel_hex[i] for i in range(4)])
            for index, start_address in enumerate(address_blocks):
                end_address = min(UCANUpdater.ADDRESS_END, start_address + UCANUpdater.MAX_FLASH_BYTES) - 1

                payload = intel_hex.tobinarray(start=start_address, end=end_address)
                crc = UCANPalletCommandSpec.calculate_crc(payload, crc)
                if start_address == address_blocks[-1]:
                    crc = UCANPalletCommandSpec.calculate_crc(reset_vector, crc)
                    payload += reset_vector
                    payload += uint32_helper.encode(crc)

                little_start_address = struct.unpack('<I', struct.pack('>I', start_address))[0]

                if payload != [255] * UCANUpdater.MAX_FLASH_BYTES:
                    # Since the uCAN flash area is erased, skip empty blocks
                    ucan_communicator.do_command(cc_address, UCANAPI.write_flash(len(payload)), ucan_address, {'start_address': little_start_address,
                                                                                                               'data': payload})
                total_payload += payload

                percentage = int(index / total_amount * 100)
                if percentage > logged_percentage:
                    logger.info('Flashing... {0}%'.format(percentage))
                    logged_percentage = percentage

            logger.info('Flashing... Done')
            crc = UCANPalletCommandSpec.calculate_crc(total_payload)
            if crc != 0:
                raise RuntimeError('Unexpected error in CRC calculation ({0})'.format(crc))

            # Prepare reset to application mode
            logger.info('Reduce bootloader timeout to {0}s'.format(UCANUpdater.BOOTLOADER_TIMEOUT_RUNTIME))
            ucan_communicator.do_command(cc_address, UCANAPI.set_bootloader_timeout(SID.BOOTLOADER_COMMAND), ucan_address, {'timeout': UCANUpdater.BOOTLOADER_TIMEOUT_RUNTIME})
            logger.info('Set safety bit allowing the application to immediately start on reset')
            ucan_communicator.do_command(cc_address, UCANAPI.set_bootloader_safety_flag(), ucan_address, {'safety_flag': 1})

            # Switch to application mode
            logger.info('Reset to application mode')
            response = ucan_communicator.do_command(cc_address, UCANAPI.reset(SID.BOOTLOADER_COMMAND), ucan_address, {}, timeout=10)
            if response is None:
                raise RuntimeError('Error resettings uCAN after flashing')
            if response.get('application_mode', 0) != 1:
                raise RuntimeError('uCAN didn\'t enter application mode after reset')

            try:
                response = ucan_communicator.do_command(cc_address, UCANAPI.get_version(), ucan_address, {})
                if response is None:
                    raise RuntimeError()
                current_version = response['firmware_version']
                logger.info('New uCAN version: v{0}'.format(current_version))
            except Exception:
                raise RuntimeError('Could not load new uCAN version')

            logger.info('Update completed')
            return True
        except Exception as ex:
            logger.error('Error flashing: {0}'.format(ex))
            return False
