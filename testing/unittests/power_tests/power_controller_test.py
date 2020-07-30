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
Tests for the power controller module.

@author: fryckbos
"""

from __future__ import absolute_import

import unittest

import mock

from ioc import SetTestMode, SetUpTestInjections
from power.power_api import P1_CONCENTRATOR, POWER_MODULE, PowerCommand
from power.power_controller import P1Controller, PowerController


class PowerControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SetTestMode()

    def setUp(self):
        self.power_communicator = mock.Mock()
        SetUpTestInjections(power_communicator=self.power_communicator,
                            power_store=mock.Mock())
        self.controller = PowerController()

    def test_get_module_current(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_current({'version': POWER_MODULE,
                                                'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'CUR', '', '8f', module_type='E'))
            ]

    def test_get_module_frequency(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_frequency({'version': POWER_MODULE,
                                                'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'FRE', '', 'f', module_type='E'))
            ]

    def test_get_module_power(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_power({'version': POWER_MODULE,
                                              'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'POW', '', '8f', module_type='E'))
            ]

    def test_get_module_voltage(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_voltage({'version': POWER_MODULE,
                                                'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'VOL', '', 'f', module_type='E'))
            ]

    def test_get_module_day_energy(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_day_energy({'version': POWER_MODULE,
                                                   'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'EDA', '', '8L', module_type='E'))
            ]

    def test_get_module_night_energy(self):
        with mock.patch.object(self.power_communicator, 'do_command') as cmd:
            self.controller.get_module_night_energy({'version': POWER_MODULE,
                                                     'address': '11.0'})
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'ENI', '', '8L', module_type='E'))
            ]

class P1ControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SetTestMode()

    def setUp(self):
        self.power_communicator = mock.Mock()
        SetUpTestInjections(power_communicator=self.power_communicator)
        self.controller = P1Controller()

    def test_get_realtime_p1(self):
        with mock.patch.object(self.controller, 'get_module_status',
                               return_value = [
                                   True, True, False, True,
                                   False, False, False, False
                               ]), \
             mock.patch.object(self.controller, 'get_module_meter',
                               side_effect=([
                                   '1111111111111111111111111111',
                                   '3333333333333333333333333333',
                                   '                            ',
                                   '                            '
                               ], [
                                   '2222222222222222222222222222',
                                   '                            ',
                                   '                            ',
                                   '4444444444444444444444444444'
                               ])), \
             mock.patch.object(self.controller, 'get_module_timestamp',
                               return_value=[1.0, 2.0, 0.0, 190527083152.0]), \
             mock.patch.object(self.controller, 'get_module_gas_consumption',
                               return_value=[1.0, 2.3, 0.0, 12.0]), \
             mock.patch.object(self.controller, 'get_module_consumption_tariff',
                               return_value=[1.0, 2.3, 0.0, 12.0]), \
             mock.patch.object(self.controller, 'get_module_injection_tariff',
                               return_value=[1.0, 2.3, 0.0, 12.0]), \
             mock.patch.object(self.controller, 'get_module_tariff_indicator',
                               return_value=[1.0, 2.0, 0.0, 12.0]), \
             mock.patch.object(self.controller, 'get_module_current',
                               return_value=[
                                   {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0},
                                   {'phase1': 2.0, 'phase2': 2.0, 'phase3': 2.0},
                                   {'phase1': 0.0, 'phase2': 0.0, 'phase3': 0.0},
                                   {'phase1': 12.0, 'phase2': 12.0, 'phase3': 12.0},
                               ]), \
             mock.patch.object(self.controller, 'get_module_voltage',
                               return_value=[
                                   {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0},
                                   {'phase1': 2.3, 'phase2': 2.3, 'phase3': 2.3},
                                   {'phase1': 0.0, 'phase2': 0.0, 'phase3': 0.0},
                                   {'phase1': 12.0, 'phase2': 12.0, 'phase3': 12.0},
                               ]), \
             mock.patch.object(self.controller, 'get_module_delivered_power',
                               return_value=[2.0, 3.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0]), \
             mock.patch.object(self.controller, 'get_module_received_power',
                               return_value=[1.0, 3.0, 0.0, 12.0, 0.0, 0.0, 0.0, 0.0]):
            result = self.controller.get_realtime(
                {10: {'address': 11, 'version': P1_CONCENTRATOR}}
            )
            assert result == [
                {'electricity': {'current': {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0},
                                 'ean': '1111111111111111111111111111',
                                 'tariff_indicator': 1.0,
                                 'consumption_tariff1': 1.0,
                                 'consumption_tariff2': 1.0,
                                 'injection_tariff1': 1.0,
                                 'injection_tariff2': 1.0,
                                 'voltage': {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0}},
                 'gas': {'consumption': 1.0, 'ean': '2222222222222222222222222222'},
                 'device_id': '11.0',
                 'module_id': 10,
                 'port_id': 0,
                 'timestamp': 1.0},
                {'electricity': {'current': {'phase1': 2.0, 'phase2': 2.0, 'phase3': 2.0},
                                 'ean': '3333333333333333333333333333',
                                 'tariff_indicator': 2.0,
                                 'consumption_tariff1': 2.3,
                                 'consumption_tariff2': 2.3,
                                 'injection_tariff1': 2.3,
                                 'injection_tariff2': 2.3,
                                 'voltage': {'phase1': 2.3, 'phase2': 2.3, 'phase3': 2.3}},
                 'gas': {'consumption': 2.3, 'ean': ''},
                 'device_id': '11.1',
                 'module_id': 10,
                 'port_id': 1,
                 'timestamp': 2.0},
                {'electricity': {'current': {'phase1': 12.0,
                                             'phase2': 12.0,
                                             'phase3': 12.0},
                                 'ean': '',
                                 'tariff_indicator': 12.0,
                                 'consumption_tariff1': 12.0,
                                 'consumption_tariff2': 12.0,
                                 'injection_tariff1': 12.0,
                                 'injection_tariff2': 12.0,
                                 'voltage': {'phase1': 12.0,
                                             'phase2': 12.0,
                                             'phase3': 12.0}},
                 'gas': {'consumption': 12.0, 'ean': '4444444444444444444444444444'},
                 'device_id': '11.3',
                 'module_id': 10,
                 'port_id': 3,
                 'timestamp': 190527083152.0}
            ]

    def test_get_module_status(self):
        payload = 0b00001011
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            status = self.controller.get_module_status({'version': P1_CONCENTRATOR,
                                                       'address': '11.0'})
            assert status == [
                True, True, False, True,
                False, False, False, False
            ]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'SP\x00', '', 'B', module_type='C'))
            ]

    def test_get_module_meter(self):
        payload = '11111111111111111111111111112222222222222222222222222222                            4444444444444444444444444444'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_meter({'version': P1_CONCENTRATOR,
                                                       'address': '11.0'},
                                                      type=1)
            assert meters == [
                '1111111111111111111111111111',
                '2222222222222222222222222222',
                '                            ',
                '4444444444444444444444444444',
                '', '', '', '',
            ]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'M1\x00', '', '224s', module_type='C'))
            ]

    def test_get_module_timestamp(self):
        # TODO confirm this is correct
        payload = '000000000001S000000000002              000000000012S'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_timestamp({'version': P1_CONCENTRATOR,
                                                           'address': '11.0'})
            assert meters == [1.0, 2.0, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'TS\x00', '', '104s', module_type='C'))
            ]

    def test_get_module_gas(self):
        payload = '000000001*m300002.300*m3            00012.000*m3'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_gas_consumption({'version': P1_CONCENTRATOR,
                                                                  'address': '11.0'})
            assert meters == [1.0, 2.3, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'cG\x00', '', '112s', module_type='C'))
            ]

    def test_get_module_consumption_tariff(self):
        payload = '0000000001*kWh000002.300*kWh              000012.000*kWh'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_consumption_tariff({'version': P1_CONCENTRATOR,
                                                                    'address': '11.0'},
                                                                 type=1)
            assert meters == [1.0, 2.3, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'c1\x00', '', '112s', module_type='C'))
            ]

    def test_get_module_injection_tariff(self):
        payload = '0000000001*kWh000002.300*kWh              000012.000*kWh'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_injection_tariff({'version': P1_CONCENTRATOR,
                                                                  'address': '11.0'},
                                                                 type=1)
            assert meters == [1.0, 2.3, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'i1\x00', '', '112s', module_type='C'))
            ]

    def test_get_module_tariff_indicator(self):
        # TODO confirm this is correct
        payload = '00010002    0012'
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            meters = self.controller.get_module_tariff_indicator({'version': P1_CONCENTRATOR,
                                                                  'address': '11.0'})
            assert meters == [1.0, 2.0, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'ti\x00', '', '32s', module_type='C'))
            ]

    def test_get_module_current(self):
        payload = '001  002  !42  012  '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            voltages = self.controller.get_module_current({'version': P1_CONCENTRATOR,
                                                           'address': '11.0'})
            assert voltages == [
                {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0},
                {'phase1': 2.0, 'phase2': 2.0, 'phase3': 2.0},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': 12.0, 'phase2': 12.0, 'phase3': 12.0},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None}
            ]
            self.assertIn(mock.call('11.0', PowerCommand('G', 'C1\x00', '', '40s', module_type='C')),
                          cmd.call_args_list)
            self.assertIn(mock.call('11.0', PowerCommand('G', 'C2\x00', '', '40s', module_type='C')),
                          cmd.call_args_list)
            self.assertIn(mock.call('11.0', PowerCommand('G', 'C3\x00', '', '40s', module_type='C')),
                          cmd.call_args_list)

    def test_get_module_voltage(self):
        payload = '00001  002.3  !@#42  00012  '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            voltages = self.controller.get_module_voltage({'version': P1_CONCENTRATOR,
                                                           'address': '11.0'})
            assert voltages == [
                {'phase1': 1.0, 'phase2': 1.0, 'phase3': 1.0},
                {'phase1': 2.3, 'phase2': 2.3, 'phase3': 2.3},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': 12.0, 'phase2': 12.0, 'phase3': 12.0},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None},
                {'phase1': None, 'phase2': None, 'phase3': None}
            ]
            self.assertIn(mock.call('11.0', PowerCommand('G', 'V1\x00', '', '56s', module_type='C')),
                          cmd.call_args_list)
            self.assertIn(mock.call('11.0', PowerCommand('G', 'V2\x00', '', '56s', module_type='C')),
                          cmd.call_args_list)
            self.assertIn(mock.call('11.0', PowerCommand('G', 'V3\x00', '', '56s', module_type='C')),
                          cmd.call_args_list)

    def test_get_module_delivered_power(self):
        payload = '000001   000002   !@#$42   000012   '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            delivered = self.controller.get_module_delivered_power({'version': P1_CONCENTRATOR,
                                                                    'address': '11.0'})
            assert delivered == [1.0, 2.0, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'PD\x00', '', '72s', module_type='C')),
            ]

    def test_get_module_received_power(self):
        payload = '000001   000002   !@#$42   000012   '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            received = self.controller.get_module_received_power({'version': P1_CONCENTRATOR,
                                                                  'address': '11.0'})
            assert received == [1.0, 2.0, None, 12.0, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'PR\x00', '', '72s', module_type='C')),
            ]

    def test_get_module_day_energy(self):
        payload = '000000.001    000000.002    !@#$%^&*42    000000.012    '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            received = self.controller.get_module_day_energy({'version': P1_CONCENTRATOR,
                                                              'address': '11.0'})
            assert received == [0.001, 0.002, None, 0.012, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'c1\x00', '', '112s', module_type='C')),
            ]

    def test_get_module_night_energy(self):
        payload = '000000.001    000000.002    !@#$%^&*42    000000.012    '
        with mock.patch.object(self.power_communicator, 'do_command',
                               return_value=[payload]) as cmd:
            received = self.controller.get_module_night_energy({'version': P1_CONCENTRATOR,
                                                                'address': '11.0'})
            assert received == [0.001, 0.002, None, 0.012, None, None, None, None]
            assert cmd.call_args_list == [
                mock.call('11.0', PowerCommand('G', 'c2\x00', '', '112s', module_type='C')),
            ]
