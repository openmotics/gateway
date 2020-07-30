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
"""
Module BLL
"""
from __future__ import absolute_import
import logging
import six
from ioc import Injectable, Inject, INJECTED, Singleton
from gateway.dto import ModuleDTO
from gateway.base_controller import BaseController
from gateway.models import Module
from gateway.mappers.module import ModuleMapper

if False:  # MYPY
    from typing import Optional, List, Dict
    from power.power_controller import PowerController

logger = logging.getLogger("openmotics")


@Injectable.named('module_controller')
@Singleton
class ModuleController(BaseController):

    @Inject
    def __init__(self, master_controller=INJECTED, power_controller=INJECTED):
        super(ModuleController, self).__init__(master_controller, sync_interval=24 * 60 * 60)
        self._power_controller = power_controller  # type: PowerController

    def _sync_orm(self):
        if self._sync_running:
            logger.info('ORM sync (Modules): Already running')
            return False
        self._sync_running = True

        logger.info('ORM sync (Modules)')

        amounts = {None: 0, True: 0, False: 0}
        try:
            ids = []
            module_dtos = self._master_controller.get_modules_information() + self._power_controller.get_modules_information()
            for dto in module_dtos:
                module = Module.get_or_none(source=dto.source,
                                            address=dto.address)
                if module is None:
                    module = Module(source=dto.source,
                                    address=dto.address)
                module.module_type = dto.module_type
                module.hardware_type = dto.hardware_type
                module.firmware_version = dto.firmware_version
                module.hardware_version = dto.hardware_version
                module.order = dto.order
                module.save()
                amounts[dto.online] += 1
                ids.append(module.id)
            Module.delete().where(Module.id.not_in(ids)).execute()  # type: ignore
        finally:
            self._sync_running = False

        logger.info('ORM sync (Modules): completed ({0} online, {1} offline, {2} emulated/virtual)'.format(
            amounts[True], amounts[False], amounts[None]
        ))
        return True

    def load_master_modules(self, address=None):  # type: (Optional[str]) -> List[ModuleDTO]
        return [ModuleMapper.orm_to_dto(module)
                for module in Module.select().where(Module.source == ModuleDTO.Source.MASTER)
                if address is None or module.address == address]

    def load_energy_modules(self, address=None):  # type: (Optional[str]) -> List[ModuleDTO]
        return [ModuleMapper.orm_to_dto(module)
                for module in Module.select().where(Module.source == ModuleDTO.Source.GATEWAY)
                if address is None or module.address == address]

    def replace_module(self, old_address, new_address):
        if old_address == new_address:
            raise RuntimeError('Old and new address cannot be identical')
        all_modules = {module.address: module for module in Module.select().where(Module.source == ModuleDTO.Source.MASTER)}  # type: Dict[str, Module]
        old_module = all_modules.get(old_address)
        new_module = all_modules.get(new_address)
        if old_module is None or new_module is None:
            raise RuntimeError('The specified modules could not be found')
        if old_module.source != new_module.source or old_module.source != ModuleDTO.Source.MASTER:
            raise RuntimeError('Only `master` modules can be replaced')
        if old_module.module_type != new_module.module_type:
            raise RuntimeError('The modules should be of the same type')
        if old_module.hardware_type != new_module.hardware_type or old_module.hardware_type != ModuleDTO.HardwareType.PHYSICAL:
            raise RuntimeError('Both modules should be physical modules')
        module_types = [[ModuleDTO.ModuleType.INPUT, ModuleDTO.ModuleType.SENSOR, ModuleDTO.ModuleType.CAN_CONTROL],
                        [ModuleDTO.ModuleType.OUTPUT, ModuleDTO.ModuleType.DIM_CONTROL],
                        [ModuleDTO.ModuleType.SHUTTER]]
        module_types_map = {mtype: mtypes
                            for mtypes in module_types
                            for mtype in mtypes}
        last_module_order = max(module.order for module in six.itervalues(all_modules)
                                if module.module_type in module_types_map[old_module.module_type])
        if new_module.order != last_module_order:
            raise RuntimeError('Only the last added module in its category can be used as replacement')
        self._master_controller.replace_module(old_address, new_address)
        if not self.run_sync_orm():
            # The sync might already be running, so we'll make sure it does a full run (again)
            self.request_sync_orm()
        new_module = Module.select().where(Module.source == new_module.source).where(Module.address == new_module.address)[0]
        return (ModuleMapper.orm_to_dto(old_module),
                ModuleMapper.orm_to_dto(new_module))
