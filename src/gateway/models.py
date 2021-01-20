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

import inspect
import json
import logging
import sys
import time

from peewee import AutoField, BooleanField, CharField, \
    DoesNotExist, FloatField, ForeignKeyField, IntegerField, SqliteDatabase, \
    TextField
from playhouse.signals import Model, post_save

import constants

if False:  # MYPY
    from typing import Dict, List, Optional, Any
    T = TypeVar('T')

logger = logging.getLogger('openmotics')


class Database(object):

    filename = constants.get_gateway_database_file()
    _db = SqliteDatabase(filename, pragmas={'foreign_keys': 1})

    # Used to store database metrics (e.g. number of saves)
    _metrics = {}  # type: Dict[str,int]
    _dirty_flag = False

    @classmethod
    def get_db(cls):
        return cls._db

    @classmethod
    def incr_metrics(cls, sender, incr=1):
        cls._metrics.setdefault(sender, 0)
        cls._metrics[sender] += incr

    @classmethod
    def get_dirty_flag(cls):
        dirty = cls._dirty_flag
        cls._dirty_flag = False
        return dirty

    @classmethod
    def set_dirty(cls):
        cls._dirty_flag = True

    @classmethod
    def get_models(cls):
        models = set()
        for (class_name, class_member) in inspect.getmembers(sys.modules[__name__], inspect.isclass):
            if issubclass(class_member, BaseModel):
                models.add(class_member.__name__)
        return models

    @classmethod
    def get_metrics(cls):
        return cls._metrics


@post_save()
def db_metrics_handler(sender, instance, created):
    _, _ = instance, created
    Database.set_dirty()
    Database.incr_metrics(sender.__name__.lower())


class BaseModel(Model):
    class Meta:
        database = Database.get_db()


class Floor(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    name = CharField(null=True)


class Room(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    name = CharField(null=True)
    floor = ForeignKeyField(Floor, null=True, on_delete='SET NULL', backref='rooms')


class Feature(BaseModel):
    id = AutoField()
    name = CharField(unique=True)
    enabled = BooleanField()


class Output(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='outputs')


class Input(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    event_enabled = BooleanField(default=False)
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='inputs')


class Shutter(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='shutters')


class ShutterGroup(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='shutter_groups')


class PulseCounter(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)
    name = CharField()
    source = CharField()  # Options: 'master' or 'gateway'
    persistent = BooleanField()
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='pulse_counters')


class GroupAction(BaseModel):
    id = AutoField()
    number = IntegerField(unique=True)


class Module(BaseModel):
    id = AutoField()
    source = CharField()
    address = CharField()
    module_type = CharField(null=True)
    hardware_type = CharField()
    firmware_version = CharField(null=True)
    hardware_version = CharField(null=True)
    order = IntegerField(null=True)
    last_online_update = IntegerField(null=True)


class DataMigration(BaseModel):
    id = AutoField()
    name = CharField()
    migrated = BooleanField()


class Schedule(BaseModel):
    id = AutoField()
    name = CharField()
    start = FloatField()
    repeat = CharField(null=True)
    duration = FloatField(null=True)
    end = FloatField(null=True)
    action = CharField()
    arguments = CharField(null=True)
    status = CharField()


class User(BaseModel):
    id = AutoField()
    username = CharField(unique=True)
    password = CharField()
    accepted_terms = IntegerField(default=0)


class Config(BaseModel):
    id = AutoField()
    setting = CharField(unique=True)
    data = CharField()

    CACHE_EXPIRY_DURATION = 60
    CACHE = {}

    @staticmethod
    def get_entry(key, fallback):
        # type: (str, T) -> T
        """ Retrieves a setting from the DB, returns the argument 'fallback' when non existing """
        key = key.lower()
        if key in Config.CACHE:
            data, expire_at = Config.CACHE[key]
            if expire_at > time.time():
                return data
        raw_data = Config.select(Config.data).where(Config.setting == key).dicts().first()
        if raw_data is not None:
            data = json.loads(raw_data['data'])
        else:
            data = fallback
        Config.CACHE[key] = (data, time.time() + Config.CACHE_EXPIRY_DURATION)
        return data

    @staticmethod
    def set_entry(key, value):
        # type: (str, Any) -> None
        """ Sets a setting in the DB, does overwrite if already existing """
        key = key.lower()
        data = json.dumps(value)
        config_orm = Config.get_or_none(Config.setting == key)
        if config_orm is not None:
            # if the key already exists, update the value
            config_orm.data = data
            config_orm.save()
        else:
            # create a new setting if it was non existing
            config_orm = Config(setting=key, data=data)
            config_orm.save()
        Config.CACHE[key] = (value, time.time() + Config.CACHE_EXPIRY_DURATION)

    @staticmethod
    def remove_entry(key):
        # type: (str) -> None
        """ Removes a setting from the DB """
        Config.delete().where(
            Config.setting == key.lower()
        ).execute()
        Config.CACHE.pop(key, None)


class Plugin(BaseModel):
    id = AutoField()
    name = CharField(unique=True)
    version = CharField()


class Sensor(BaseModel):
    class Types(object):
        TEMPERATURE = 'temperature'
        HUMIDITY = 'humidity'
        BRIGHTNESS = 'brightness'
        LUX = 'lux'
        CO2 = 'co2'

    id = AutoField()
    type = CharField()  # Options: see Sensor.Types
    source = CharField()  # Options: 'gateway', 'plugin' or 'master'
    plugin = ForeignKeyField(Plugin, null=True, on_delete='CASCADE')
    external_id = CharField()  # eg. serial number, reference, ...
    name = CharField(null=True)
    offset = FloatField()
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='sensors')

    class Meta:
        indexes = (
            (('source', 'plugin_id', 'external_id'), True),
        )


class Ventilation(BaseModel):
    id = AutoField()
    source = CharField()  # Options: 'gateway' or 'plugin'
    plugin = ForeignKeyField(Plugin, null=True, on_delete='CASCADE')
    external_id = CharField()  # eg. serial number
    name = CharField()
    amount_of_levels = IntegerField()
    device_vendor = CharField()
    device_type = CharField()
    device_serial = CharField()

    class Meta:
        indexes = (
            (('source', 'plugin_id', 'external_id'), True),
        )


class ThermostatGroup(BaseModel):
    class Modes(object):
        HEATING = 'heating'
        COOLING = 'cooling'

    id = AutoField()
    number = IntegerField(unique=True)
    name = CharField()
    on = BooleanField(default=True)
    threshold_temperature = FloatField(null=True, default=None)
    sensor = ForeignKeyField(Sensor, null=True, backref='thermostat_groups', on_delete='SET NULL')
    mode = CharField(default=Modes.HEATING)  # Options: 'heating' or 'cooling'


class OutputToThermostatGroup(BaseModel):
    id = AutoField()
    output = ForeignKeyField(Output, on_delete='CASCADE')
    thermostat_group = ForeignKeyField(ThermostatGroup, on_delete='CASCADE')
    index = IntegerField()  # The index of this output in the config 0-3
    mode = CharField()  # The mode this config is used for. Options: 'heating' or 'cooling'
    value = IntegerField()  # The value that needs to be set on the output when in this mode (0-100)

    class Meta:
        indexes = (
            (('output_id', 'thermostat_group_id', 'mode'), True),
        )


class Pump(BaseModel):
    id = AutoField()
    name = CharField()
    output = ForeignKeyField(Output, null=True, backref='pumps', on_delete='SET NULL', unique=True)

    @property
    def valves(self):  # type: () -> List[Valve]
        return [valve for valve in Valve.select(Valve)
                                        .join(PumpToValve)
                                        .where(PumpToValve.pump == self.id)]

    @property
    def heating_valves(self):  # type: () -> List[Valve]
        return self._valves(mode=ThermostatGroup.Modes.HEATING)

    @property
    def cooling_valves(self):  # type: () -> List[Valve]
        return self._valves(mode=ThermostatGroup.Modes.COOLING)

    def _valves(self, mode):
        return [valve for valve in Valve.select(Valve, ValveToThermostat.mode, ValveToThermostat.priority)
                                        .distinct()
                                        .join_from(Valve, ValveToThermostat)
                                        .join_from(Valve, PumpToValve)
                                        .join_from(PumpToValve, Pump)
                                        .where((ValveToThermostat.mode == mode) &
                                               (Pump.id == self.id))
                                        .order_by(ValveToThermostat.priority)]


class Valve(BaseModel):
    id = AutoField()
    name = CharField()
    delay = IntegerField(default=60)
    output = ForeignKeyField(Output, backref='valves', on_delete='CASCADE', unique=True)

    @property
    def pumps(self):  # type: () -> List[Pump]
        return [pump for pump in Pump.select(Pump)
                                     .join(PumpToValve)
                                     .where(PumpToValve.valve == self.id)]


class PumpToValve(BaseModel):
    id = AutoField()
    pump = ForeignKeyField(Pump, on_delete='CASCADE')
    valve = ForeignKeyField(Valve, on_delete='CASCADE')

    class Meta:
        indexes = (
            (('pump_id', 'valve_id'), True),
        )


class Thermostat(BaseModel):
    class ValveConfigs(object):
        CASCADE = 'cascade'
        EQUAL = 'equal'

    id = AutoField()
    number = IntegerField(unique=True)
    name = CharField(default='Thermostat')
    sensor = ForeignKeyField(Sensor, null=True, backref='thermostats', on_delete='SET NULL')
    pid_heating_p = FloatField(default=120)
    pid_heating_i = FloatField(default=0)
    pid_heating_d = FloatField(default=0)
    pid_cooling_p = FloatField(default=120)
    pid_cooling_i = FloatField(default=0)
    pid_cooling_d = FloatField(default=0)
    automatic = BooleanField(default=True)
    room = ForeignKeyField(Room, null=True, on_delete='SET NULL', backref='thermostats')
    start = IntegerField()
    valve_config = CharField(default=ValveConfigs.CASCADE)  # Options: 'cascade' or 'equal'
    thermostat_group = ForeignKeyField(ThermostatGroup, backref='thermostats', on_delete='CASCADE')

    def get_preset(self, preset_type):  # type: (str) -> Preset
        return Preset.get((Preset.type == preset_type) &
                          (Preset.thermostat_id == self.id))

    @property
    def setpoint(self):
        return self.active_preset.heating_setpoint if self.mode == ThermostatGroup.Modes.HEATING else self.active_preset.cooling_setpoint

    @property
    def active_preset(self):
        preset = Preset.get_or_none(thermostat=self.id, active=True)
        if preset is None:
            preset = self.get_preset(Preset.Types.SCHEDULE)
            preset.active = True
            preset.save()
        return preset

    @active_preset.setter
    def active_preset(self, value):
        if value is None or value.thermostat_id != self.id:
            raise ValueError('The given Preset does not belong to this Thermostat')
        if value != self.active_preset:
            if self.active_preset is not None:
                current_active_preset = self.active_preset
                current_active_preset.active = False
                current_active_preset.save()
            value.active = True
            value.save()

    @property
    def valves(self):  # type: () -> List[Valve]
        return [valve for valve in Valve.select(Valve)
                                        .join(ValveToThermostat)
                                        .where(ValveToThermostat.thermostat_id == self.id)
                                        .order_by(ValveToThermostat.priority)]

    @property
    def active_valves(self):  # type: () -> List[Valve]
        return self._valves(mode=self.thermostat_group.mode)

    @property
    def heating_valves(self):  # type: () -> List[Valve]
        return self._valves(mode=ThermostatGroup.Modes.HEATING)

    @property
    def cooling_valves(self):  # type: () -> List[Valve]
        return self._valves(mode=ThermostatGroup.Modes.COOLING)

    def _valves(self, mode):  # type: (str) -> List[Valve]
        return [valve for valve in Valve.select(Valve, ValveToThermostat.mode, ValveToThermostat.priority)
                                        .join(ValveToThermostat)
                                        .where((ValveToThermostat.thermostat_id == self.id) &
                                               (ValveToThermostat.mode == mode))
                                        .order_by(ValveToThermostat.priority)]

    def heating_schedules(self):  # type: () -> List[DaySchedule]
        return [schedule for schedule in
                DaySchedule.select()
                           .where((DaySchedule.thermostat == self.id) &
                                  (DaySchedule.mode == ThermostatGroup.Modes.HEATING))
                           .order_by(DaySchedule.index)]

    def cooling_schedules(self):  # type: () -> List[DaySchedule]
        return [x for x in
                DaySchedule.select()
                           .where((DaySchedule.thermostat == self.id) &
                                  (DaySchedule.mode == ThermostatGroup.Modes.COOLING))
                           .order_by(DaySchedule.index)]


class ValveToThermostat(BaseModel):
    valve = ForeignKeyField(Valve, on_delete='CASCADE')
    thermostat = ForeignKeyField(Thermostat, on_delete='CASCADE')
    mode = CharField(default=ThermostatGroup.Modes.HEATING)
    priority = IntegerField(default=0)

    class Meta:
        indexes = (
            (('valve_id', 'thermostat_id', 'mode'), True),
        )


class Preset(BaseModel):
    class Types(object):
        MANUAL = 'manual'
        SCHEDULE = 'schedule'
        AWAY = 'away'
        VACATION = 'vacation'
        PARTY = 'party'

    TYPE_TO_SETPOINT = {Types.AWAY: 3,
                        Types.VACATION: 4,
                        Types.PARTY: 5}
    SETPOINT_TO_TYPE = {setpoint: preset_type
                        for preset_type, setpoint in TYPE_TO_SETPOINT.items()}

    id = AutoField()
    type = CharField()  # Options: see Preset.Types
    heating_setpoint = FloatField(default=14.0)
    cooling_setpoint = FloatField(default=30.0)
    active = BooleanField(default=False)
    thermostat = ForeignKeyField(Thermostat, backref='presets', on_delete='CASCADE')


class DaySchedule(BaseModel):
    id = AutoField()
    index = IntegerField()
    content = TextField()
    mode = CharField(default=ThermostatGroup.Modes.HEATING)
    thermostat = ForeignKeyField(Thermostat, backref='day_schedules', on_delete='CASCADE')

    @property
    def schedule_data(self):  # type: () -> Dict[int, float]
        return json.loads(self.content)

    @schedule_data.setter
    def schedule_data(self, value):  # type: (Dict[int, float]) -> None
        self.content = json.dumps(value)

    def get_scheduled_temperature(self, seconds_in_day):  # type: (int) -> float
        seconds_in_day = seconds_in_day % 86400
        data = self.schedule_data
        last_value = data.get(0)
        for key in sorted(data):
            if key > seconds_in_day:
                break
            last_value = data[key]
        return last_value


@post_save(sender=Thermostat)
def on_thermostat_save_handler(model_class, instance, created):
    _ = model_class
    if created:
        for preset_type in [Preset.Types.MANUAL, Preset.Types.SCHEDULE, Preset.Types.AWAY,
                            Preset.Types.VACATION, Preset.Types.PARTY]:
            try:
                preset = Preset.get(type=preset_type, thermostat=instance)
            except DoesNotExist:
                preset = Preset(type=preset_type, thermostat=instance)
            if preset_type == Preset.Types.SCHEDULE:
                preset.active = True
            preset.save()
