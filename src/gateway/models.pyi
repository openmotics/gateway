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

from typing import Optional, Literal, List, Tuple, Set, Dict, Any
from playhouse.signals import Model
from peewee import (
    CharField,
    FloatField, ForeignKeyField, IntegerField, PrimaryKeyField,
    SqliteDatabase, TextField
)

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
    room: Optional[RoomForeignKeyField]


class Shutter(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    room: Optional[RoomForeignKeyField]


class ShutterGroup(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    room: Optional[RoomForeignKeyField]


class Sensor(BaseModel):
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
    module_type: Optional[Literal['sensor', 'input', 'output', 'shutter', 'dim_control', 'can_control', 'energy', 'power', 'p1_concentrator']]
    hardware_type: Literal['physical', 'emulated', 'virtual']
    firmware_version: Optional[str]
    hardware_version: Optional[str]
    order = Optional[MixedIntegerField]


class ThermostatGroup(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    on: bool
    threshold_temp: Optional[MixedIntegerField]
    sensor: Optional[MixedIntegerField]
    mode: Literal['heating', 'cooling']

    @staticmethod
    def v0_get_global() -> ThermostatGroup: ...

    @property
    def v0_switch_to_heating_outputs(self) -> List[Tuple[int, int]]: ...

    @property
    def v0_switch_to_cooling_outputs(self) -> List[Tuple[int, int]]: ...


class ThermostatGroupForeignKeyField(ThermostatGroup, ForeignKeyField): ...


class OutputToThermostatGroup(BaseModel):
    output: OutputForeignKeyField
    thermostat_group: ThermostatGroupForeignKeyField
    index: MixedIntegerField
    mode: Literal['heating', 'cooling']
    value: MixedIntegerField


class Pump(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    output: OutputForeignKeyField

    @property
    def valves(self) -> List[Valve]: ...

    @property
    def heating_valves(self) -> List[Valve]: ...

    @property
    def cooling_valves(self) -> List[Valve]: ...

    def __valves(self, mode: Literal['heating', 'cooling']) -> Set[Valve]: ...


class PumpForeignKeyField(Pump, ForeignKeyField): ...


class Valve(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    delay: MixedIntegerField
    output: OutputForeignKeyField

    @property
    def pumps(self) -> List[Pump]: ...


class ValveForeignKeyField(Valve, ForeignKeyField): ...


class PumpToValve(BaseModel):
    pump: PumpForeignKeyField
    valve: ValveForeignKeyField


class Thermostat(BaseModel):
    id: MixedPrimaryKeyField
    number: MixedIntegerField
    name: MixedCharField
    sensor: MixedIntegerField
    pid_heating_p: MixedFloatField
    pid_heating_i: MixedFloatField
    pid_heating_d: MixedFloatField
    pid_cooling_p: MixedFloatField
    pid_cooling_i: MixedFloatField
    pid_cooling_d: MixedFloatField
    automatic: bool
    room: RoomForeignKeyField
    start: MixedIntegerField
    valve_config: Literal['cascade', 'equal']
    thermostat_group: ThermostatGroupForeignKeyField

    def get_preset(self, name: str) -> Preset: ...

    @property
    def setpoint(self) -> float: ...

    @property
    def active_preset(self) -> Preset: ...

    @active_preset.setter
    def active_preset(self, new_preset: Preset) -> None: ...

    def deactivate_all_presets(self) -> None: ...

    @property
    def mode(self) -> Literal['heating', 'cooling']: ...

    @property
    def valves(self) -> List[Valve]: ...

    def _valves(self, mode: Literal['cooling', 'heating']) -> List[Valve]: ...

    @property
    def active_valves(self) -> List[Valve]: ...

    @property
    def heating_valves(self) -> List[Valve]: ...

    @property
    def cooling_valves(self) -> List[Valve]: ...

    @property
    def presets(self) -> List[Preset]: ...

    def heating_schedules(self) -> List[DaySchedule]: ...

    def cooling_schedules(self) -> List[DaySchedule]: ...

    def v0_get_output_numbers(self, mode: Optional[Literal['cooling', 'heating']]) -> Tuple[Optional[Output], Optional[Output]]: ...


class ThermostatForeignKeyField(Thermostat, ForeignKeyField): ...


class ValveToThermostat(BaseModel):
    valve: ValveForeignKeyField
    thermostat: ThermostatForeignKeyField
    mode: Literal['heating', 'cooling']
    priority: MixedIntegerField


class Preset(BaseModel):
    id: MixedPrimaryKeyField
    name: MixedCharField
    heating_setpoint: MixedFloatField
    cooling_setpoint: MixedFloatField
    active: bool
    thermostat: ThermostatForeignKeyField

    def get_v0_setpoint_id(self) -> int: ...

    @classmethod
    def get_by_thermostat_and_v0_setpoint(cls, thermostat: Thermostat, v0_setpoint: int) -> Preset: ...


class DaySchedule(BaseModel):
    id: MixedPrimaryKeyField
    index: MixedIntegerField
    content: MixedTextField
    mode: Literal['heating', 'cooling']
    thermostat: ThermostatForeignKeyField

    @property
    def schedule_data(self) -> Dict[int, str]: ...

    @schedule_data.setter
    def schedule_data(self, content: Dict[int, str]) -> None: ...

    @classmethod
    def _schedule_data_from_v0(cls, v0_schedule: List[Any]) -> Dict[int, float]: ...

    def update_schedule_from_v0(self, v0_schedule: List[Any]) -> None: ...

    def get_scheduled_temperature(self, seconds_in_day: int) -> float: ...
