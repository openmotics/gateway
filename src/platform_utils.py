# Copyright (C) 2018 OpenMotics BV
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
""""
The hardware_utils module contains various classes helping with Hardware and System abstraction
"""
from __future__ import absolute_import

import logging
import os
import subprocess
import sys

import constants

logger = logging.getLogger('openmotics')


class Hardware(object):
    """
    Abstracts the hardware related functions
    """
    class BoardType(object):
        BB = 'BB'
        BBB = 'BBB'
        BBGW = 'BBGW'

    BoardTypes = [BoardType.BB, BoardType.BBB, BoardType.BBGW]

    # eMMC registers
    EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B = 269
    EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A = 268
    EXT_CSD_PRE_EOL_INFO = 267

    @staticmethod
    def read_mmc_ext_csd():
        registers = {
            'life_time_est_typ_b': Hardware.EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B,
            'life_time_est_typ_a': Hardware.EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A,
            'eol_info': Hardware.EXT_CSD_PRE_EOL_INFO,
        }
        with open('/sys/kernel/debug/mmc1/mmc1:0001/ext_csd') as fd:
            ecsd = fd.read()

        ecsd_info = {}
        # NOTE: this only works for fields with length 1
        for reg, i in registers.items():
            pos = i * 2
            ecsd_info[reg] = int(ecsd[pos:pos + 2], 16)
        return ecsd_info

    @staticmethod
    def get_board_type():
        try:
            with open('/proc/device-tree/model', 'r') as mfh:
                board_type = mfh.read().strip('\x00').replace(' ', '_')
                if board_type in ['TI_AM335x_BeagleBone', 'TI_AM335x_BeagleBone_Black']:
                    return Hardware.BoardType.BBB
                if board_type in ['TI_AM335x_BeagleBone_Green_Wireless']:
                    return Hardware.BoardType.BBGW
        except IOError:
            pass
        try:
            with open('/proc/meminfo', 'r') as memfh:
                mem_total = memfh.readline()
                if '254228 kB' in mem_total:
                    return Hardware.BoardType.BB
                if '510716 kB' in mem_total:
                    return Hardware.BoardType.BBB
        except IOError:
            pass
        logger.warning('could not detect board type, unknown')
        return  # Unknown

    @staticmethod
    def get_main_interface():
        board_type = Hardware.get_board_type()
        if board_type in [Hardware.BoardType.BB, Hardware.BoardType.BBB]:
            return 'eth0'
        if board_type == Hardware.BoardType.BBGW:
            return 'wlan0'
        logger.warning('Could not detect local interface. Fallback: lo')
        return 'lo'


class System(object):
    """
    Abstracts the system related functions
    """

    SYSTEMD_UNIT_MAP = {'openmotics': 'openmotics-api.service',
                        'vpn_service': 'openmotics-vpn.service'}

    class OS(object):
        ANGSTROM = 'angstrom'
        DEBIAN = 'debian'


    @staticmethod
    def restart_service(service):
        # type: (str) -> None
        System.run_service_action('restart', service)

    @staticmethod
    def run_service_action(action, service):
        # type: (str) -> subprocess.Popen
        unit_name = System.SYSTEMD_UNIT_MAP.get(service, service)
        try:
            subprocess.check_output(['systemctl', 'is-enabled', unit_name])
            is_systemd = True
        except subprocess.CalledProcessError:
            is_systemd = False

        if is_systemd:
            return subprocess.Popen(['systemctl', action, '--no-pager', unit_name],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        else:
            return subprocess.Popen(['supervisorctl', action, service],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    @staticmethod
    def get_operating_system():
        operating_system = {}
        try:
            with open('/etc/os-release', 'r') as osfh:
                lines = osfh.readlines()
                for line in lines:
                    k, v = line.strip().split('=')
                    operating_system[k] = v
            operating_system['ID'] = operating_system['ID'].lower()
        except IOError:
            logger.warning('could not detect operating system, unknown')
        return operating_system

    @staticmethod
    def get_ip_address():
        """ Get the local ip address. """
        interface = Hardware.get_main_interface()
        operating_system = System.get_operating_system()
        try:
            lines = subprocess.check_output('ifconfig {0}'.format(interface), shell=True)
            if operating_system['ID'] == System.OS.ANGSTROM:
                return lines.split('\n')[1].strip().split(' ')[1].split(':')[1]
            elif operating_system['ID'] == System.OS.DEBIAN:
                return lines.split('\n')[1].strip().split(' ')[1]
            else:
                return
        except Exception:
            return

    @staticmethod
    def get_vpn_service():
        return 'openvpn.service' if System.get_operating_system()['ID'] == System.OS.ANGSTROM else 'openvpn-client@omcloud'

    @staticmethod
    def _use_pyopenssl():
        return System.get_operating_system()['ID'] == System.OS.ANGSTROM

    @staticmethod
    def get_ssl_socket(sock, private_key_filename, certificate_filename):
        if System._use_pyopenssl():
            from OpenSSL import SSL
            context = SSL.Context(SSL.SSLv23_METHOD)
            context.use_privatekey_file(private_key_filename)
            context.use_certificate_file(certificate_filename)
            return SSL.Connection(context, sock)
        import ssl
        return ssl.wrap_socket(sock,
                               keyfile=private_key_filename,
                               certfile=certificate_filename,
                               ssl_version=ssl.PROTOCOL_SSLv23,
                               do_handshake_on_connect=False,
                               suppress_ragged_eofs=False)

    @staticmethod
    def setup_cherrypy_ssl(https_server):
        if System._use_pyopenssl():
            https_server.ssl_module = 'pyopenssl'
        else:
            import ssl
            https_server.ssl_module = 'builtin'
            if sys.version_info[:3] < (3, 6, 0):
                https_server.ssl_context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)

    @staticmethod
    def handle_socket_exception(connection, exception, logger):
        if System._use_pyopenssl():
            import select
            from OpenSSL import SSL
            if isinstance(exception, SSL.SysCallError):
                if exception[0] == 11:  # Temporarily unavailable
                    # This should be ok, just wait for more data to arrive
                    return True  # continue
                if exception[0] == -1:  # Unexpected EOF
                    logger.info('Got (unexpected) EOF, aborting due to lost connection')
                    return False  # break
            elif isinstance(exception, SSL.WantReadError):
                # This should be ok, just wait for more data to arrive
                select.select([connection], [], [], 1.0)
                return True  # continue
        else:
            import select
            import ssl
            if isinstance(exception, ssl.SSLEOFError):
                logger.info('Got SSLEOFError, aborting due to lost connection')
                return False  # break
            elif isinstance(exception, ssl.SSLError):
                if 'The read operation timed out' in str(exception):
                    # Got read timeout, just wait for data to arrive
                    return True  # continue
        raise exception

    @staticmethod
    def import_libs():
        operating_system = System.get_operating_system().get('ID')
        if operating_system in (System.OS.ANGSTROM, System.OS.DEBIAN):
            sys.path.insert(0, '/opt/openmotics/python-deps/lib/python2.7/site-packages')

        # Patching where/if required
        if operating_system == System.OS.ANGSTROM:
            from pkg_resources import resource_filename, resource_stream, Requirement
            resource_stream(Requirement.parse('requests'), 'requests/cacert.pem')
            os.environ['REQUESTS_CA_BUNDLE'] = resource_filename(Requirement.parse('requests'), 'requests/cacert.pem')


class Platform(object):
    """
    Abstracts the platform related functions
    """

    class Type(object):
        CLASSIC = 'CLASSIC'
        CORE_PLUS = 'CORE_PLUS'

    Types = [Type.CLASSIC, Type.CORE_PLUS]

    @staticmethod
    def get_platform():
        # type: () -> str
        from six.moves.configparser import ConfigParser
        config = ConfigParser()
        config.read(constants.get_config_file())

        if config.has_option('OpenMotics', 'platform'):
            platform = config.get('OpenMotics', 'platform')
            if platform in Platform.Types:
                return platform
        return Platform.Type.CLASSIC
