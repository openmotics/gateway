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
Contains type hints for models.py

It has a few interesting quirks which are explained below:

Assume following implementation in models.py:
> class Foo(Model):
>     bar = IntegerField(...)

This will work well, unless we want to have the following code
> some_foo.bar = 5

Mypy will complain about assigning a value of type `int` to a field of type `IntegerField`.

In models.pyi (this file) there could be a few solutions.

> class Foo(Model):
>    bar: int

This works great for the int assignment (e.g. `some_foo.bar = 5`). But once we want to
use field queries (e.g. `Foo.delete().where(Foo.bar.not_in(some_list)).execute()`), things fail
as well (as an `int` doesn't have a `not_in` method.

The MyPy `Union[...]` doesn't resolve anything, since this only accept methods that are available in
both.

This is where thise `Mixed...` classes come as a (possible) solution. Since they
inherit from both the primitive as the `...Field` class which - in theory - should yield
all possibilities to MyPy.

One downside: It's not possible to inherit from the `bool` primitive, so boolean fields are
still an issue

"""

from typing import Optional, Literal, List, Tuple, Set, Dict, Any, TypeVar
from playhouse.signals import Model
from peewee import (
    CharField,
    FloatField, ForeignKeyField, IntegerField, PrimaryKeyField,
    SqliteDatabase, TextField
)

T = TypeVar('T')

class MixedIntegerField(int, IntegerField): ...
class MixedCharField(str, CharField): ...
class MixedFloatField(float, FloatField): ...
class MixedPrimaryKeyField(int, PrimaryKeyField): ...
class MixedTextField(str, TextField): ...


class Database(object):
    _db: SqliteDatabase
    _metrics: Dict[str, int]

    @classmethod
    def get_db(cls) -> SqliteDatabase: ...

    @classmethod
    def get_dirty_flag(cls) -> bool: ...

    @classmethod
    def set_dirty(cls) -> None: ...

    @classmethod
    def incr_metrics(cls, sender: str, incr=1) -> None: ...

    @classmethod
    def get_models(cls) -> Set: ...

    @classmethod
    def get_metrics(cls) -> Dict[str, int]: ...


class BaseModel(Model): ...


class Floor(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: Optional[MixedCharField]


class FloorForeignKeyField(Floor, ForeignKeyField): ...


class Room(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: Optional[MixedCharField]
    floor: Optional[FloorForeignKeyField]


class RoomForeignKeyField(Room, ForeignKeyField): ...


class Feature(BaseModel):
    id: MixedPrimaryKeyField
    name: MixedCharField
    enabled: bool


class Output(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    room: Optional[RoomForeignKeyField]


class OutputForeignKeyField(Output, ForeignKeyField): ...


class Input(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    event_enabled: bool
    room: Optional[RoomForeignKeyField]


class Shutter(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    room: Optional[RoomForeignKeyField]


class ShutterGroup(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    room: Optional[RoomForeignKeyField]


class PulseCounter(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: str
    source: Literal['master', 'gateway']
    persistent: bool
    room: Optional[RoomForeignKeyField]


class GroupAction(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField


class Module(BaseModel):
    id: MixedPrimaryKeyField
    source: Literal['master', 'gateway']
    address: str
    module_type: Optional[Literal['sensor', 'input', 'output', 'shutter', 'dim_control', 'can_control', 'open_collector', 'energy', 'power', 'p1_concentrator']]
    hardware_type: Literal['physical', 'emulated', 'virtual', 'internal']
    firmware_version: Optional[str]
    hardware_version: Optional[str]
    order = Optional[MixedIntegerField]
    last_online_update = Optional[MixedIntegerField]


class DataMigration(BaseModel):
    id: MixedPrimaryKeyField
    name: str
    migrated: bool


class Schedule(BaseModel):
    id: MixedPrimaryKeyField
    name: str
    start: MixedFloatField
    repeat: Optional[str]
    duration: Optional[MixedFloatField]
    end: Optional[MixedFloatField]
    action: Literal['BASIC_ACTION', 'GROUP_ACTION', 'LOCAL_API']
    arguments: Optional[str]
    status: Literal['ACTIVE', 'COMPLETED']


class User(BaseModel):
    id: MixedPrimaryKeyField
    username: MixedTextField
    password: MixedTextField
    accepted_terms: MixedIntegerField


class Config(BaseModel):
    id: MixedPrimaryKeyField
    setting: MixedTextField
    data: MixedTextField

    @staticmethod
    def get_entry(key: str, fallback: T) -> T: ...

    @staticmethod
    def set_entry(key: str, value: Any) -> None: ...

    @staticmethod
    def remove_entry(key: str) -> None: ...


class Plugin(BaseModel):
    id: MixedPrimaryKeyField
    name: MixedTextField
    version: MixedTextField


class PluginForeignKeyField(Plugin, ForeignKeyField): ...


class Sensor(BaseModel):
    class Types(object):
        TEMPERATURE: Literal['temperature']
        HUMIDITY: Literal['humidity']
        BRIGHTNESS: Literal['brightness']
        LUX: Literal['lux']
        CO2: Literal['co2']

    id: MixedPrimaryKeyField
    type: MixedCharField
    source: MixedCharField
    plugin: PluginForeignKeyField
    external_id: MixedCharField
    name: MixedCharField
    offset: MixedFloatField
    room: RoomForeignKeyField


class SensorForeignKeyField(Sensor, ForeignKeyField): ...


class Ventilation(BaseModel):
    id: MixedPrimaryKeyField
    source: MixedTextField
    plugin: PluginForeignKeyField
    external_id: MixedTextField
    name: MixedTextField
    type: MixedTextField
    vendor: MixedTextField
    amount_of_levels: MixedIntegerField


class ThermostatGroup(BaseModel):
    class Modes(object):
        HEATING: Literal['heating']
        COOLING: Literal['cooling']

    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    on: bool
    threshold_temperature: Optional[MixedFloatField]
    sensor: Optional[SensorForeignKeyField]
    mode: Literal['heating', 'cooling']

    @property
    def thermostats(self) -> List[Thermostat]: ...


class ThermostatGroupForeignKeyField(ThermostatGroup, ForeignKeyField): ...


class OutputToThermostatGroup(BaseModel):
    id: MixedPrimaryKeyField
    output: OutputForeignKeyField
    thermostat_group: ThermostatGroupForeignKeyField
    index: MixedIntegerField
    mode: MixedCharField
    value: MixedIntegerField


class Pump(BaseModel):
    id: MixedPrimaryKeyField
    name: MixedCharField
    output: Optional[OutputForeignKeyField]

    @property
    def valves(self) -> List[Valve]: ...

    @property
    def heating_valves(self) -> List[Valve]: ...

    @property
    def cooling_valves(self) -> List[Valve]: ...

    def _valves(self, mode: str) -> List[Valve]: ...


class PumpForeignKeyField(Pump, ForeignKeyField): ...


class Valve(BaseModel):
    id: MixedPrimaryKeyField
    name: MixedCharField
    delay: MixedIntegerField
    output: OutputForeignKeyField

    @property
    def pumps(self) -> List[Pump]: ...


class ValveForeignKeyField(Valve, ForeignKeyField): ...


class PumpToValve(BaseModel):
    id: MixedPrimaryKeyField
    pump: PumpForeignKeyField
    valve: ValveForeignKeyField


class Thermostat(BaseModel):
    class ValveConfigs(object):
        CASCADE: Literal['cascade']
        EQUAL: Literal['equal']

    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    sensor: Optional[SensorForeignKeyField]
    pid_heating_p: MixedFloatField
    pid_heating_i: MixedFloatField
    pid_heating_d: MixedFloatField
    pid_cooling_p: MixedFloatField
    pid_cooling_i: MixedFloatField
    pid_cooling_d: MixedFloatField
    automatic: bool
    room: Optional[RoomForeignKeyField]
    start: MixedIntegerField
    valve_config: MixedCharField
    thermostat_group: ThermostatGroupForeignKeyField

    def get_preset(self, preset_type: str) -> Preset: ...

    @property
    def setpoint(self) -> float: ...

    @property
    def active_preset(self) -> Preset: ...

    @active_preset.setter
    def active_preset(self, value: Preset) -> None: ...

    @property
    def valves(self) -> List[Valve]: ...

    @property
    def active_valves(self) -> List[Valve]: ...

    @property
    def heating_valves(self) -> List[Valve]: ...

    @property
    def cooling_valves(self) -> List[Valve]: ...

    def _valves(self, mode: str) -> List[Valve]: ...

    def heating_schedules(self) -> List[DaySchedule]: ...

    def cooling_schedules(self) -> List[DaySchedule]: ...


class ThermostatForeignKeyField(Thermostat, ForeignKeyField): ...


class ValveToThermostat(BaseModel):
    valve: ValveForeignKeyField
    thermostat: ThermostatForeignKeyField
    mode: MixedCharField
    priority: MixedIntegerField


class Preset(BaseModel):
    class Types(object):
        MANUAL: Literal['manual']
        SCHEDULE: Literal['schedule']
        AWAY: Literal['away']
        VACATION: Literal['vacation']
        PARTY: Literal['party']

    TYPE_TO_SETPOINT: Dict[str, int]
    SETPOINT_TO_TYPE: Dict[int, str]

    id: MixedPrimaryKeyField
    type: MixedCharField
    heating_setpoint: float
    cooling_setpoint: float
    active: bool
    thermostat: ThermostatForeignKeyField


class DaySchedule(BaseModel):
    id: MixedPrimaryKeyField
    index: MixedIntegerField
    content: MixedTextField
    mode: MixedCharField
    thermostat: ThermostatForeignKeyField

    @property
    def schedule_data(self) -> Dict[int, float]: ...

    @schedule_data.setter
    def schedule_data(self, value: Dict[int, float]) -> None: ...

    def get_scheduled_temperature(self, seconds_in_day: int) -> float: ...
