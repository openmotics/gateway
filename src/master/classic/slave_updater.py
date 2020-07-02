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
For more information, see:
* https://wiki.openmotics.com/index.php/API_Reference_Guide
* https://wiki.openmotics.com/index.php/Bootloader_Error_Codes
"""
from __future__ import absolute_import
from platform_utils import System
System.import_libs()

import time
import traceback
import intelhex
import logging
import master.classic.master_api as master_api
from ioc import Inject, INJECTED
from master.classic.master_communicator import MasterCommunicator, CommunicationTimedOutException
from master.classic.eeprom_controller import EepromFile, EepromAddress

if False:  # MYPY
    from typing import Tuple, List, Dict, Any, Union
    from master.classic.master_api import MasterCommandSpec

logger = logging.getLogger("openmotics")


def add_crc(command, command_input):
    # type: (MasterCommandSpec, Dict[str, Any]) -> None
    """
    Create a bootload action, this uses the command and the inputs
    to calculate the crc for the action, the crc is added to input.

    :param command: The bootload action (from master_api).
    :param command_input: dict with the inputs for the action.
    """
    crc = 0

    crc += ord(command.action[0])
    crc += ord(command.action[1])

    for field in command.input_fields:
        if field.name == 'literal' and field.encode(None) == 'C':
            break

        for byte in field.encode(command_input[field.name]):
            crc += ord(byte)

    command_input['crc0'] = crc / 256
    command_input['crc1'] = crc % 256


def check_bl_crc(command, command_output):
    # type: (MasterCommandSpec, Dict[str, Any]) -> bool
    """
    Check the crc in the response from the master.

    :param command: The bootload action (from master_api).
    :param command_output: dict containing the values for the output field.
    :returns: Whether the crc is valid.
    """
    crc = 0

    crc += ord(command.action[0])
    crc += ord(command.action[1])

    for field in command.output_fields:
        if field.name == 'literal' and field.encode(None) == 'C':
            break

        for byte in field.encode(command_output[field.name]):
            crc += ord(byte)

    return command_output['crc0'] == (crc / 256) and command_output['crc1'] == (crc % 256)


def get_module_addresses(module_type):
    # type: (str) -> List[str]
    """
    Get the addresses for the modules of the given type.

    :param module_type: the type of the module (O, R, D, I, T, C)
    :returns: A list containing the addresses of the modules (strings of length 4).
    """
    eeprom_file = EepromFile()
    base_address = EepromAddress(0, 1, 2)
    no_modules = eeprom_file.read([base_address])
    modules = []

    no_input_modules = ord(no_modules[base_address].bytes[0])
    for i in range(no_input_modules):
        address = EepromAddress(2 + i, 252, 1)
        is_can = eeprom_file.read([address])[address].bytes == 'C'
        address = EepromAddress(2 + i, 0, 4)
        module = eeprom_file.read([address])[address].bytes
        if not is_can or module[0] == 'C':
            modules.append(module)

    no_output_modules = ord(no_modules[base_address].bytes[1])
    for i in range(no_output_modules):
        address = EepromAddress(33 + i, 0, 4)
        modules.append(eeprom_file.read([address])[address].bytes)

    return [module for module in modules if module[0] == module_type]


def pretty_address(address):
    # type: (str) -> str
    """
    Create a pretty printed version of an address.

    :param address: address string
    :returns: string with format 'M.x.y.z' where M is in [O, R, D, I, T, C, o, r, d, i, t, c] and x, y and z are integers.
    """
    return '{0}.{1}.{2}.{3}'.format(address[0], ord(address[1]), ord(address[2]), ord(address[3]))


def calc_crc(ihex, blocks):
    # type: (intelhex.IntelHex, int) -> Tuple[int, int, int, int]
    """
    Calculate the crc for a hex file.

    :param ihex: intelhex file.
    :param blocks: the number of blocks.
    """
    bytes_sum = 0
    for i in range(64 * blocks - 8):
        bytes_sum += ihex[i]

    crc0 = (bytes_sum & (255 << 24)) >> 24
    crc1 = (bytes_sum & (255 << 16)) >> 16
    crc2 = (bytes_sum & (255 << 8)) >> 8
    crc3 = (bytes_sum & (255 << 0)) >> 0

    return crc0, crc1, crc2, crc3


def check_result(command, result, success_code=0):
    # type: (MasterCommandSpec, Dict[str, Any], Union[int, List[int]]) -> None
    """
    Raise an exception if the crc for the result is invalid, or if an unexpectederror_code is set in the result.
    """
    if not check_bl_crc(command, result):
        raise Exception('CRC check failed on {0}'.format(command.action))

    returned_code = result.get('error_code')
    if isinstance(success_code, list):
        if returned_code not in success_code:
            raise Exception('{0} returned error code {1}'.format(command.action, returned_code))
    elif returned_code != success_code:
        raise Exception('{0} returned error code {1}'.format(command.action, returned_code))


@Inject
def do_command(cmd, fields, retry=True, success_code=0, master_communicator=INJECTED):
    # type: (MasterCommandSpec, Dict[str, Any], bool, Union[int, List[int]], MasterCommunicator) -> Dict[str, Any]
    """
    Execute a command using the master communicator. If the command times out, retry.

    :param master_communicator: Used to communicate with the master.
    :param cmd: The command to execute.
    :param fields: The fields to use
    :param retry: If the master command should be retried
    :param success_code: Expected success code
    """
    add_crc(cmd, fields)  # `fields` is updated by reference
    try:
        result = master_communicator.do_command(cmd, fields)
        check_result(cmd, result, success_code)
        return result
    except Exception as exception:
        error_message = str(exception)
        if error_message == '':
            error_message = exception.__class__.__name__
        if isinstance(exception, CommunicationTimedOutException):
            logger.info('Got timeout while executing command')
        else:
            logger.info('Got exception while executing command: {0}'.format(error_message))
        if retry:
            logger.info('Retrying...')
            result = master_communicator.do_command(cmd, fields)
            check_result(cmd, result, success_code)
            return result
        raise


@Inject
def bootload(address, ihex, crc, blocks, master_communicator=INJECTED):
    # type: (str, intelhex.IntelHex, Tuple[int, int, int, int], int, MasterCommunicator) -> None
    """
    Bootload 1 module.

    :param master_communicator: Used to communicate with the master.
    :param address: Address for the module to bootload
    :param ihex: The hex file
    :param crc: The crc for the hex file
    :param blocks: The number of blocks to write
    """
    logger.info('Checking the version')
    try:
        result = do_command(cmd=master_api.modules_get_version(),
                            fields={'addr': address},
                            retry=False,
                            success_code=255)
        logger.info('Current version: v{0}.{1}.{2}'.format(result['f1'], result['f2'], result['f3']))
    except Exception:
        logger.info('Version call not (yet) implemented or module unavailable')

    logger.info('Going to bootloader')
    try:
        do_command(cmd=master_api.modules_goto_bootloader(),
                   fields={'addr': address, 'sec': 5},
                   retry=False,
                   success_code=[255, 1])
    except Exception:
        logger.info('Module has no bootloader or is unavailable. Skipping...')
        return

    time.sleep(1)

    logger.info('Setting the firmware crc')
    do_command(cmd=master_api.modules_new_crc(),
               fields={'addr': address, 'ccrc0': crc[0], 'ccrc1': crc[1], 'ccrc2': crc[2], 'ccrc3': crc[3]})

    try:
        logger.info('Going to long mode')
        master_communicator.do_command(cmd=master_api.change_communication_mode_to_long())

        logger.info('Writing firmware data')
        for i in range(blocks):
            bytes_to_send = ''
            for j in range(64):
                if i == blocks - 1 and j >= 56:
                    # The first 8 bytes (the jump) is placed at the end of the code.
                    bytes_to_send += chr(ihex[j - 56])
                else:
                    bytes_to_send += chr(ihex[i*64 + j])

            logger.debug('* Block {0}'.format(i))
            do_command(cmd=master_api.modules_update_firmware_block(),
                       fields={'addr': address, 'block': i, 'bytes': bytes_to_send})
    finally:
        logger.info('Going to short mode')
        master_communicator.do_command(cmd=master_api.change_communication_mode_to_short())

    logger.info("Integrity check")
    do_command(cmd=master_api.modules_integrity_check(),
               fields={'addr': address})

    logger.info("Going to application")
    do_command(cmd=master_api.modules_goto_application(),
               fields={'addr': address})

    tries = 0
    while True:
        try:
            tries += 1
            logger.info('Waiting for application...')
            result = do_command(cmd=master_api.modules_get_version(),
                                fields={'addr': address},
                                success_code=255)
            logger.info('New version: v{0}.{1}.{2}'.format(result['f1'], result['f2'], result['f3']))
            break
        except Exception:
            if tries >= 5:
                raise
            time.sleep(1)

    logger.info('Resetting error list')
    master_communicator.do_command(cmd=master_api.clear_error_list())


@Inject
def bootload_modules(module_type, filename):
    # type: (str, str) -> bool
    """
    Bootload all modules of the given type with the firmware in the given filename.

    :param module_type: Type of the modules (O, R, D, I, T, C)
    :param filename: The filename for the hex file to load
    """

    logger.info('Loading module addresses...')
    addresses = get_module_addresses(module_type)

    blocks = 922 if module_type == 'C' else 410
    ihex = intelhex.IntelHex(filename)
    crc = calc_crc(ihex, blocks)

    update_success = True
    for address in addresses:
        logger.info('Bootloading module {0}'.format(pretty_address(address)))
        try:
            bootload(address, ihex, crc, blocks)
        except Exception:
            update_success = False
            logger.info('Bootloading failed:')
            logger.info(traceback.format_exc())

    return update_success
