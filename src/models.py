import datetime
import json
import sys
import logging
import time
import inspect
from playhouse.signals import post_save, Model

import constants
from peewee import PrimaryKeyField, IntegerField, FloatField, BooleanField, TextField, ForeignKeyField, \
                   CompositeKey, SqliteDatabase, DoesNotExist, CharField

logger = logging.getLogger('openmotics')


class Database(object):

    filename = constants.get_gateway_database_file()
    _db = SqliteDatabase(filename, pragmas={'foreign_keys': 1})

    # used to store database metrics (e.g. number of saves)
    _metrics = {}

    @classmethod
    def get_db(cls):
        return cls._db

    @classmethod
    def incr_metrics(cls, sender, incr=1):
        cls._metrics.setdefault(sender, 0)
        cls._metrics[sender] += incr

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
    Database.incr_metrics(sender.__name__.lower())


class BaseModel(Model):
    class Meta:
        database = Database.get_db()


class Plugin(BaseModel):
    id = PrimaryKeyField()
    name = CharField()
    version = CharField()
    uri = CharField(unique=True)

class Light(BaseModel):
    TYPES = (
        (0, 'OnOff'),
        (1, 'Dimmable'),
        (2, 'RGB'),
    )
    id = PrimaryKeyField()
    name = CharField()
    type = IntegerField(choices=TYPES)
    plugin = ForeignKeyField(Plugin, on_delete='CASCADE', null=True)
    external_id = CharField(null=True, unique=True)

    @property
    def uri(self):
        return '{}/{}'.format(self.plugin.uri, self.external_id)


class Feature(BaseModel):
    id = PrimaryKeyField()
    name = CharField(unique=True)
    enabled = BooleanField()


class Output(BaseModel):
    id = PrimaryKeyField()
    number = IntegerField(unique=True)
    # TODO: model AnalagOutput and DigitalOutput and use as dimmer etc


class ThermostatGroup(BaseModel):
    id = PrimaryKeyField()
    number = IntegerField(unique=True)
    name = CharField()
    on = BooleanField(default=True)
    threshold_temp = IntegerField(null=True, default=None)
    sensor = IntegerField(null=True, default=None)
    mode = CharField(default='heating')  # heating or cooling # TODO: add support for 'both'

    @staticmethod
    def v0_get_global():
        return ThermostatGroup.get(number=0)

    @property
    def v0_switch_to_heating_outputs(self):
        return [(link.output.number, link.value) for link in OutputToThermostatGroup.select()
                                                                                    .where(OutputToThermostatGroup.thermostat_group == self.id)
                                                                                    .where(OutputToThermostatGroup.mode == 'heating')
                                                                                    .order_by(OutputToThermostatGroup.index)]

    @property
    def v0_switch_to_cooling_outputs(self):
        return [(link.output.number, link.value) for link in OutputToThermostatGroup.select()
                                                                                    .where(OutputToThermostatGroup.thermostat_group == self.id)
                                                                                    .where(OutputToThermostatGroup.mode == 'cooling')
                                                                                    .order_by(OutputToThermostatGroup.index)]


class OutputToThermostatGroup(BaseModel):
    """ Outputs on a thermostat group are sometimes used for setting the pumpgroup in a certain state
        the index var is 0-4 of the output in setting this config """
    output = ForeignKeyField(Output, on_delete='CASCADE')
    thermostat_group = ForeignKeyField(ThermostatGroup, on_delete='CASCADE')
    index = IntegerField()   # the index of this output in the config 0-3
    mode = CharField()       # the mode this config is used for e.g. cooling or heating
    value = IntegerField()   # the value that needs to be set on the output when in this mode (0-100)

    class Meta:
        primary_key = CompositeKey('output', 'thermostat_group')


class Pump(BaseModel):
    id = PrimaryKeyField()
    number = IntegerField(unique=True)
    name = CharField()
    output = ForeignKeyField(Output, backref='valve', on_delete='SET NULL', unique=True)

    @property
    def valves(self):
        return [valve for valve in Valve.select(Valve)
                                        .join(PumpToValve)
                                        .where(PumpToValve.pump == self.id)]

    @property
    def heating_valves(self):
        return self.__valves(mode='heating')

    @property
    def cooling_valves(self):
        return self.__valves(mode='cooling')

    def __valves(self, mode):
        valves = [valve for valve in Valve.select(Valve, ValveToThermostat.mode, ValveToThermostat.priority)
                                          .join(ValveToThermostat)
                                          .where(ValveToThermostat.mode == mode)
                                          .order_by(ValveToThermostat.priority)]

        return set([valve for valve in valves if self.number in valve.pumps])


class Valve(BaseModel):
    id = PrimaryKeyField()
    number = IntegerField(unique=True)
    name = CharField()
    delay = IntegerField(default=60)
    output = ForeignKeyField(Output, backref='valve', on_delete='SET NULL', unique=True)

    @property
    def pumps(self):
        return [pump for pump in Pump.select(Pump)
                                     .join(PumpToValve)
                                     .where(PumpToValve.valve == self.id)]


class PumpToValve(BaseModel):
    """ Outputs on a thermostat group are sometimes used for setting the pumpgroup in a certain state
        the index var is 0-4 of the output in setting this config """
    pump = ForeignKeyField(Pump, on_delete='CASCADE')
    valve = ForeignKeyField(Valve, on_delete='CASCADE')

    class Meta:
        primary_key = CompositeKey('pump', 'valve')


class Thermostat(BaseModel):
    id = PrimaryKeyField()
    number = IntegerField(unique=True)
    name = CharField(default='Thermostat')
    sensor = IntegerField()

    pid_heating_p = FloatField(default=120)
    pid_heating_i = FloatField(default=0)
    pid_heating_d = FloatField(default=0)
    pid_cooling_p = FloatField(default=120)
    pid_cooling_i = FloatField(default=0)
    pid_cooling_d = FloatField(default=0)

    automatic = BooleanField(default=True)
    room = IntegerField()
    start = IntegerField()

    valve_config = CharField(default='cascade')  # options: cascade, equal

    thermostat_group = ForeignKeyField(ThermostatGroup, backref='thermostats', on_delete='CASCADE', default=1)

    def get_preset(self, name):
        presets = [preset for preset in Preset.select()
                                              .where(Preset.name == name)
                                              .where(Preset.thermostat == self.id)]
        if len(presets) > 0:
            return presets[0]
        else:
            raise ValueError('Preset with name {} not found.'.format(name))

    @property
    def setpoint(self):
        return self.active_preset.heating_setpoint if self.mode == 'heating' else self.active_preset.cooling_setpoint

    @property
    def active_preset(self):
        preset = Preset.get_or_none(thermostat=self.id, active=True)
        if preset is None:
            preset = self.get_preset('SCHEDULE')
            preset.active = True
            preset.save()
        return preset

    @active_preset.setter
    def active_preset(self, new_preset):
        if new_preset is not None and new_preset.thermostat == self:
            if new_preset != self.active_preset:
                if self.active_preset is not None:
                    current_active_preset = self.active_preset
                    current_active_preset.active = False
                    current_active_preset.save()
                new_preset.active = True
                new_preset.save()
        else:
            raise ValueError('Not a valid preset {}.'.format(new_preset))

    def deactivate_all_presets(self):
        for preset in Preset.select().where(Preset.thermostat == self.id):
            preset.active = False
            preset.save()

    @property
    def mode(self):
        return self.thermostat_group.mode

    @property
    def valves(self):
        return [valve for valve in Valve.select(Valve)
                                        .join(ValveToThermostat)
                                        .where(ValveToThermostat.thermostat == self.id)
                                        .order_by(ValveToThermostat.priority)]

    def _valves(self, mode):
        return [valve for valve in Valve.select(Valve, ValveToThermostat.mode, ValveToThermostat.priority)
                                        .join(ValveToThermostat)
                                        .where(ValveToThermostat.thermostat == self.id)
                                        .where(ValveToThermostat.mode == mode)
                                        .order_by(ValveToThermostat.priority)]

    @property
    def active_valves(self):
        return self._valves(mode=self.mode)

    @property
    def heating_valves(self):
        return self._valves(mode='heating')

    @property
    def cooling_valves(self):
        return self._valves(mode='cooling')

    @property
    def presets(self):
        return [preset for preset in Preset.select().where(Preset.thermostat == self.id)]

    def heating_schedules(self):
        return DaySchedule.select()\
                          .where(DaySchedule.thermostat == self.id)\
                          .where(DaySchedule.mode == 'heating')\
                          .order_by(DaySchedule.index)

    def cooling_schedules(self):
        return DaySchedule.select()\
                          .where(DaySchedule.thermostat == self.id)\
                          .where(DaySchedule.mode == 'cooling')\
                          .order_by(DaySchedule.index)

    def v0_get_output_numbers(self, mode=None):
        if mode is None:
            mode = self.thermostat_group.mode
        valves = self.cooling_valves if mode == 'cooling' else self.heating_valves
        db_outputs = [valve.output.number for valve in valves]
        number_of_outputs = len(db_outputs)

        if number_of_outputs > 2:
            logger.warning('Only 2 outputs are supported in the old format. Total: {} outputs.'.format(number_of_outputs))

        output0 = db_outputs[0] if number_of_outputs > 0 else None
        output1 = db_outputs[1] if number_of_outputs > 1 else None
        return [output0, output1]

    def to_v0_format(self, mode='heating', fields=None):
        """
        :returns: thermostat_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        """
        data = {}
        data['id'] = self.number
        data['name'] = self.name
        data['sensor'] = self.sensor
        if mode == 'heating':
            data['pid_p'] = self.pid_heating_p
            data['pid_i'] = self.pid_heating_i
            data['pid_d'] = self.pid_heating_d
        else:
            data['pid_p'] = self.pid_cooling_p
            data['pid_i'] = self.pid_cooling_i
            data['pid_d'] = self.pid_cooling_d

        for preset in self.presets:
            setpoint = preset.heating_setpoint if mode == 'heating' else preset.cooling_setpoint
            if preset.name == 'AWAY':
                data['setp3'] = setpoint
            if preset.name == 'VACATION':
                data['setp4'] = setpoint
            if preset.name == 'PARTY':
                data['setp5'] = setpoint

        data['permanent_manual'] = self.automatic
        data['room'] = self.room
        data['output0'], data['output1'] = self.v0_get_output_numbers(mode=mode)

        day_schedules = sorted(self.day_schedules, key=lambda schedule: schedule.index, reverse=False)
        start_day_of_week = (self.start / 86400 - 4) % 7  # 0: Monday, 1: Tuesday, ...
        for (day_index, key) in [(0, 'auto_mon'),
                                 (1, 'auto_tue'),
                                 (2, 'auto_wed'),
                                 (3, 'auto_thu'),
                                 (4, 'auto_fri'),
                                 (5, 'auto_sat'),
                                 (6, 'auto_sun')]:
            index = (7 - start_day_of_week + day_index) % 7
            data[key] = day_schedules[index].to_v0_dict()
        return data


class ValveToThermostat(BaseModel):
    valve = ForeignKeyField(Valve, on_delete='CASCADE')
    thermostat = ForeignKeyField(Thermostat, on_delete='CASCADE')
    mode = CharField(default='heating')
    priority = IntegerField(default=0)

    class Meta:
        table_name = 'valve_to_thermostat'


class Preset(BaseModel):
    id = PrimaryKeyField()
    name = CharField()
    heating_setpoint = FloatField(default=14.0)
    cooling_setpoint = FloatField(default=30.0)
    active = BooleanField(default=False)
    thermostat = ForeignKeyField(Thermostat, on_delete='CASCADE')

    def get_v0_setpoint_id(self):
        mapping = {'MANUAL': 1,
                   'SCHEDULE': 2,
                   'AWAY': 3,
                   'VACATION': 4,
                   'PARTY': 5}
        name = str(self.name)
        v0_setpoint = mapping.get(name)
        if v0_setpoint is None:
            raise ValueError('Preset name {} not compatible with v0_setpoint. Should be one of {}.'.format(name, mapping.keys()))
        return v0_setpoint

    @classmethod
    def get_by_thermostat_and_v0_setpoint(cls, thermostat, v0_setpoint):
        mapping = {3: 'AWAY',
                   4: 'VACATION',
                   5: 'PARTY'}
        name = mapping.get(v0_setpoint)
        if name is None:
            raise ValueError('Preset v0_setpoint {} unknown'.format(v0_setpoint))
        return Preset.get(name=name, thermostat=thermostat)


class DaySchedule(BaseModel):
    id = PrimaryKeyField()
    index = IntegerField()
    content = TextField()
    mode = CharField(default='heating')
    thermostat = ForeignKeyField(Thermostat, backref='day_schedules', on_delete='CASCADE')

    @classmethod
    def from_dict(cls, thermostat, day_index, mode, data):
        """
        "data":
            {
                "0": <temperature from this timestamp>,
                "23400": <temperature from this timestamp>,
                "30600": <temperature from this timestamp>,
                "61200": <temperature from this timestamp>,
                "84600": <temperature from this timestamp>
            }
        """
        # convert relative timestamps to int and temperature values to float
        for key, value in data.iteritems():
            relative_timestamp = int(key)
            if relative_timestamp < 86400:
                data[relative_timestamp] = float(value)
        return cls(thermostat=thermostat, index=day_index, mode=mode, content=json.dumps(data))

    @property
    def schedule_data(self):
        return json.loads(self.content)

    @schedule_data.setter
    def schedule_data(self, content):
        self.content = json.dumps(content)

    @classmethod
    def _schedule_data_from_v0(cls, v0_schedule):
        def get_seconds(hour_timestamp):
            x = time.strptime(hour_timestamp, '%H:%M')
            return int(datetime.timedelta(hours=x.tm_hour, minutes=x.tm_min, seconds=x.tm_sec).total_seconds())
        # e.g. [17, u'06:30', u'08:30', 20, u'17:00', u'23:30', 21]
        temp_n, start_d1, stop_d1, temp_d1, start_d2, stop_d2, temp_d2 = v0_schedule

        data = {0: temp_n,
                get_seconds(start_d1): temp_d1,
                get_seconds(stop_d1):  temp_n,
                get_seconds(start_d2): temp_d2,
                get_seconds(stop_d2):  temp_n}
        return data

    def update_schedule_from_v0(self, v0_schedule):
        data = DaySchedule._schedule_data_from_v0(v0_schedule)
        self.schedule_data = data

    @classmethod
    def from_v0_dict(cls, thermostat, index, mode, v0_schedule,):
        data = cls._schedule_data_from_v0(v0_schedule)
        return cls.from_dict(thermostat, index, mode, data)

    def to_v0_dict(self):
        return_data = {}
        schedule = self.schedule_data
        n_entries = len(schedule)
        if n_entries == 0:
            logger.error('Serializing an empty temperature day schedule.')
        elif n_entries < 4:
            logger.warning('Not enough data to serialize day schedule in old format. Returning best effort data.')
            first_value = schedule.itervalues().next()
            return_data['temp_n'] = first_value
            return_data['temp_d1'] = first_value
            return_data['temp_d2'] = first_value
        else:
            index = 0
            for timestamp in sorted(schedule.keys()):
                temperature = schedule[timestamp]
                if index == 0:
                    return_data['temp_n'] = temperature
                elif index == 1:
                    return_data['temp_d1'] = temperature
                elif index == 3:
                    return_data['temp_d2'] = temperature
                index += 1
        return return_data

    def get_scheduled_temperature(self, seconds_in_day):
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
        for preset_name in ['MANUAL', 'SCHEDULE', 'AWAY', 'VACATION', 'PARTY']:
            try:
                preset = Preset.get(name=preset_name, thermostat=instance)
            except DoesNotExist:
                preset = Preset(name=preset_name, thermostat=instance)
            if preset_name == 'SCHEDULE':
                preset.active = True
            preset.save()
