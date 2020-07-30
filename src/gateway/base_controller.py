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
Base Controller
"""
from __future__ import absolute_import
import logging
from ioc import INJECTED, Inject
from gateway.daemon_thread import DaemonThread
from gateway.hal.master_event import MasterEvent
from gateway.hal.master_controller import MasterController
from gateway.models import BaseModel

if False:  # MYPY
    from typing import Optional, Callable, Type, List
    from gateway.hal.master_event import MasterEvent
    from gateway.maintenance_controller import MaintenanceController

logger = logging.getLogger("openmotics")


class SyncStructure(object):
    def __init__(self, orm_model, name, skip=None):  # type: (Type[BaseModel], str, Optional[Callable[[BaseModel], bool]]) -> None
        self.orm_model = orm_model  # type: Type[BaseModel]
        self.name = name  # type: str
        self.skip = skip  # type: Optional[Callable[[BaseModel], bool]]


class BaseController(object):

    SYNC_STRUCTURES = None  # type: Optional[List[SyncStructure]]

    @Inject
    def __init__(self, master_controller, maintenance_controller=INJECTED, sync_interval=900):
        self._master_controller = master_controller  # type: MasterController
        self._maintenance_controller = maintenance_controller  # type: MaintenanceController
        self._sync_orm_thread = None  # type: Optional[DaemonThread]
        self._master_controller.subscribe_event(self._handle_master_event)
        self._maintenance_controller.subscribe_maintenance_stopped(self.request_sync_orm)
        self._sync_orm_interval = sync_interval
        self._sync_running = False

    def _handle_master_event(self, master_event):  # type: (MasterEvent) -> None
        if master_event.type in [MasterEvent.Types.EEPROM_CHANGE, MasterEvent.Types.MODULE_DISCOVERY]:
            self.request_sync_orm()

    def start(self):
        self._sync_orm_thread = DaemonThread(name='ORM syncer for {0}'.format(self.__class__.__name__),
                                             target=self._sync_orm,
                                             interval=self._sync_orm_interval,
                                             delay=300)
        self._sync_orm_thread.start()

    def stop(self):
        if self._sync_orm_thread is not None:
            self._sync_orm_thread.stop()

    def request_sync_orm(self):
        if self._sync_orm_thread is not None:
            self._sync_orm_thread.request_single_run()

    def run_sync_orm(self):
        self._sync_orm()

    def _sync_orm(self):
        # type: () -> bool
        if self.SYNC_STRUCTURES is None:
            return False

        if self._sync_running:
            for structure in self.SYNC_STRUCTURES:
                orm_model = structure.orm_model
                logger.info('ORM sync ({0}): Already running'.format(orm_model.__name__))
            return False
        self._sync_running = True

        try:
            for structure in self.SYNC_STRUCTURES:
                orm_model = structure.orm_model
                try:
                    name = structure.name
                    skip = structure.skip

                    logger.info('ORM sync ({0})'.format(orm_model.__name__))

                    ids = []
                    for dto in getattr(self._master_controller, 'load_{0}s'.format(name))():
                        if skip is not None and skip(dto):
                            continue
                        id_ = dto.id
                        ids.append(id_)
                        orm_model.get_or_create(number=id_)  # type: ignore
                    orm_model.delete().where(orm_model.number.not_in(ids)).execute()  # type: ignore

                    logger.info('ORM sync ({0}): completed'.format(orm_model.__name__))
                except Exception:
                    logger.exception('ORM sync ({0}): Failed'.format(orm_model.__name__))
        finally:
            self._sync_running = False
        return True
