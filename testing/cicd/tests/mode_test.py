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
import logging
import socket
import ssl
import time

import psutil
import pytest

logger = logging.getLogger('openmotics')


def check_ip_range():
    # type: () -> bool
    addresses = ([x.address for x in xs if x.broadcast == '10.91.115.255'] for xs in psutil.net_if_addrs().values())
    return not sum(addresses, [])


@pytest.fixture
def power_on(request, toolbox):
    try:
        yield
    finally:
        toolbox.ensure_power_on()
        toolbox.dut.login()
        time.sleep(15)


@pytest.fixture
def authorized_mode(request, toolbox):
    try:
        yield
    finally:
        toolbox.authorized_mode_stop()


@pytest.fixture
def maintenance_mode(request, toolbox):
    yield
    for _ in range(10):
        data = toolbox.dut.get('/get_status', success=False)
        if data['success']:
            break
        time.sleep(0.2)
    assert data['success']


@pytest.mark.slow
def test_power_cycle(toolbox, power_on):
    toolbox.power_off()
    toolbox.ensure_power_on()
    pending = toolbox.health_check()
    assert pending == []


@pytest.mark.slow
@pytest.mark.skipif(check_ip_range(), reason='the maintenance ports are not accessible on jenkins')
def test_maintenance(toolbox, maintenance_mode):
    data = toolbox.dut.get('/get_status')
    expected_version = 'F{} H{}'.format(data['version'], data['hw_version'])

    logger.debug('start maintenance')
    data = toolbox.dut.get('/open_maintenance')
    assert 'port' in data

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)

    time.sleep(2)
    ssl_sock = ssl.wrap_socket(
        sock,
        ssl_version=ssl.PROTOCOL_SSLv23,
        do_handshake_on_connect=False,
        suppress_ragged_eofs=False
    )
    ssl_sock.connect((toolbox.dut._host, data['port']))

    def readline():
        c, buf = (b'', b'')
        while c != b'\n':
            c = ssl_sock.recv(1)
            buf += c
        return buf.decode().strip()

    data = ''
    while data != 'OK':
        data = readline()
        logger.debug('received data "{}"'.format(data))

    ssl_sock.send(b'firmware version\r\n')
    assert readline() == 'firmware version'
    assert readline() == expected_version

    # not allowed during maintenance
    data = toolbox.dut.get('/get_status', success=False)
    assert data['msg'] == 'maintenance_mode'

    ssl_sock.send(b'exit\r\n')
    ssl_sock.close()


@pytest.mark.slow
def test_authorized_mode(toolbox, authorized_mode):
    data = toolbox.dut.get('/get_usernames', success=False)
    assert not data['success'] and data['msg'] == 'unauthorized'

    toolbox.authorized_mode_start()
    data = toolbox.dut.get('/get_usernames')
    assert 'openmotics' in data['usernames']


@pytest.fixture
def factory_reset(request, toolbox):
    try:
        yield
    finally:
        toolbox.initialize()


@pytest.mark.slow
def test_factory_reset(toolbox, authorized_mode, factory_reset):
    data = toolbox.factory_reset()
    assert data['factory_reset'] == 'pending'
    time.sleep(60)
    toolbox.health_check(timeout=300)

    toolbox.authorized_mode_start()
    data = toolbox.dut.get('/get_usernames', use_token=False)
    logger.debug('users after reset {}'.format(data['usernames']))
    toolbox.create_or_update_user()
    toolbox.dut.login()

    data = toolbox.dut.get('/get_modules')
    assert 'inputs' in data
    assert data['inputs'] == []
    assert 'outputs' in data
    assert data['outputs'] == []

    toolbox.discover_modules(input_modules=True,
                             output_modules=True)
    # TODO: Once `can_controls` and `ucans` are also included, the below `firmeware` check
    #       also needs to be adapted, as emulated modules don't have a firwmare version

    data = toolbox.dut.get('/get_modules')
    assert 'inputs' in data
    assert ['I'] == data['inputs']
    assert 'outputs' in data
    assert ['O'] == data['outputs']

    data = toolbox.dut.get('/get_modules_information')
    modules = list(data['modules']['master'].values())
    assert {'I', 'O'} == set(x['type'] for x in modules)
    assert None not in [x['firmware'] for x in modules]

    toolbox.add_virtual_modules(module_amounts={'o': 1})

    data = toolbox.dut.get('/get_modules_information')
    modules = list(data['modules']['master'].values())
    assert {'I', 'O', 'o'} == set(x['type'] for x in modules)
