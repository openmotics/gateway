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
from ioc import Injectable, Inject, INJECTED, Singleton
from gateway.dto import ModuleDTO
from gateway.base_controller import BaseController
from gateway.models import Module

if False:  # MYPY
    from typing import Optional, List
    from power.power_controller import PowerController
    from power.power_store import PowerStore

logger = logging.getLogger("openmotics")


@Injectable.named('module_controller')
@Singleton
class ModuleController(BaseController):

    @Inject
    def __init__(self, master_controller=INJECTED, power_controller=INJECTED):
        super(ModuleController, self).__init__(master_controller, sync_interval=24 * 60 * 60)
        self._power_controller = power_controller  # type: PowerController
        self._sync_running = False

    def sync_orm(self):
        if self._sync_running:
            logger.info('ORM sync (Modules): already running')
            return

        logger.info('ORM sync (Modules)')
        self._sync_running = True

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

    def load_master_modules(self, address=None):  # type: (Optional[str]) -> List[ModuleDTO]
        return [module for module in Module.select().where(Module.source == ModuleDTO.Source.MASTER)
                if address is None or module.address == address]

    def load_energy_modules(self, address=None):  # type: (Optional[str]) -> List[ModuleDTO]
        return [module for module in Module.select().where(Module.source == ModuleDTO.Source.GATEWAY)
                if address is None or module.address == address]
