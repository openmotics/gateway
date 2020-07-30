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

import unittest
import mock
from peewee import SqliteDatabase
from gateway.hal.master_controller import MasterController
from gateway.dto import ModuleDTO
from gateway.api.serializers import ModuleSerializer
from gateway.models import Module
from gateway.module_controller import ModuleController
from power.power_controller import PowerController
from ioc import SetTestMode, SetUpTestInjections

MODELS = [Module]


class ModuleControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SetTestMode()
        cls.test_db = SqliteDatabase(':memory:')

    def setUp(self):
        self.test_db.bind(MODELS, bind_refs=False, bind_backrefs=False)
        self.test_db.connect()
        self.test_db.create_tables(MODELS)
        self.master_controller = mock.Mock(MasterController)
        self.power_controller = mock.Mock(PowerController)
        SetUpTestInjections(master_controller=self.master_controller,
                            power_controller=self.power_controller,
                            maintenance_controller=mock.Mock())
        self.controller = ModuleController()

    def tearDown(self):
        self.test_db.drop_tables(MODELS)
        self.test_db.close()

    def test_module_sync(self):
        master_modules = [ModuleDTO(source=ModuleDTO.Source.MASTER,
                                    module_type=ModuleDTO.ModuleType.OUTPUT,
                                    address='079.000.000.001',
                                    hardware_type=ModuleDTO.HardwareType.PHYSICAL,
                                    firmware_version='3.1.0',
                                    hardware_version='4',
                                    order=0)]
        energy_modules = [ModuleDTO(source=ModuleDTO.Source.GATEWAY,
                                    module_type=ModuleDTO.ModuleType.ENERGY,
                                    address='2',
                                    hardware_type=ModuleDTO.HardwareType.PHYSICAL,
                                    firmware_version='1.2.3',
                                    order=0)]
        self.master_controller.get_modules_information.return_value = master_modules
        self.power_controller.get_modules_information.return_value = energy_modules
        self.controller.run_sync_orm()
        self.assertEqual(master_modules, self.controller.load_master_modules())
        self.assertEqual(energy_modules, self.controller.load_energy_modules())
        self.assertEqual([], self.controller.load_master_modules(address='000.000.000.000'))

    def test_serialization(self):
        master_module = ModuleDTO(source=ModuleDTO.Source.MASTER,
                                  module_type=ModuleDTO.ModuleType.OUTPUT,
                                  address='079.000.000.001',
                                  hardware_type=ModuleDTO.HardwareType.PHYSICAL,
                                  firmware_version='3.1.0',
                                  hardware_version='4',
                                  order=0)
        energy_module = ModuleDTO(source=ModuleDTO.Source.GATEWAY,
                                  module_type=ModuleDTO.ModuleType.ENERGY,
                                  address='2',
                                  hardware_type=ModuleDTO.HardwareType.PHYSICAL,
                                  firmware_version='1.2.3',
                                  order=0)
        self.assertEqual({'type': 'O',
                          'module_nr': 0,
                          'category': 'OUTPUT',
                          'is_can': False,
                          'is_virtual': False,
                          'firmware': '3.1.0',
                          'hardware': '4',
                          'address': '079.000.000.001'}, ModuleSerializer.serialize(master_module, fields=None))
        self.assertEqual({'type': 'E',
                          'firmware': '1.2.3',
                          'address': '2',
                          'id': 0}, ModuleSerializer.serialize(energy_module, fields=None))
