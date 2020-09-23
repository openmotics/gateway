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

from peewee import (
    Model, Database, SqliteDatabase,
    AutoField, BooleanField, CharField, ForeignKeyField, IntegerField
)
from peewee_migrate import Migrator
import constants

if False:  # MYPY
    from typing import Dict, Any


def migrate(migrator, database, fake=False, **kwargs):
    # type: (Migrator, Database, bool, Dict[Any, Any]) -> None

    class BaseModel(Model):
        class Meta:
            database = SqliteDatabase(constants.get_gateway_database_file(),
                                      pragmas={'foreign_keys': 1})

    class Plugin(BaseModel):
        id = AutoField()
        name = CharField(unique=True)
        version = CharField()

    class Ventilation(BaseModel):
        id = AutoField()
        source = CharField()
        plugin = ForeignKeyField(Plugin, null=True, on_delete='CASCADE')
        external_id = CharField()
        name = CharField()
        amount_of_levels = IntegerField()
        device_vendor = CharField()
        device_type = CharField()
        device_serial = CharField()

        class Meta:
            indexes = (
                (('source', 'plugin_id', 'external_id'), True),
            )

    migrator.create_model(Ventilation)


def rollback(migrator, database, fake=False, **kwargs):
    # type: (Migrator, Database, bool, Dict[Any, Any]) -> None
    pass
