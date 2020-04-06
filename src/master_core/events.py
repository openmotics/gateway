# Copyright (C) 2019 OpenMotics BV
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
Module to handle Events from the Core
"""

import logging
from master_core.fields import WordField

logger = logging.getLogger('openmotics')


class Event(object):
    class Types(object):
        OUTPUT = 'OUTPUT'
        INPUT = 'INPUT'
        SENSOR = 'SENSOR'
        THERMOSTAT = 'THERMOSTAT'
        SYSTEM = 'SYSTEM'
        POWER = 'POWER'
        LED_ON = 'LED_ON'
        LED_BLINK = 'LED_BLINK'
        UNKNOWN = 'UNKNOWN'

    class SensorType(object):
        TEMPERATURE = 'TEMPERATURE'
        HUMIDITY = 'HUMIDITY'
        BRIGHTNESS = 'BRIGHTNESS'
        UNKNOWN = 'UNKNOWN'

    class SystemEventTypes(object):
        EEPROM_ACTIVATE = 'EEPROM_ACTIVATE'
        ONBOARD_TEMP_CHANGED = 'ONBOARD_TEMP_CHANGED'
        UNKNOWN = 'UNKNOWN'

    class ThermostatOrigins(object):
        SLAVE = 'SLAVE'
        MASTER = 'MASTER'
        UNKNOWN = 'UNKNOWN'

    class Bus(object):
        RS485 = 'RS485'
        CAN = 'CAN'

    class Leds(object):
        LED_0 = 0  # TODO: Rename these enums to more relevant names once known
        LED_1 = 1
        LED_2 = 2
        LED_3 = 3
        LED_4 = 4
        LED_5 = 5
        LED_6 = 6
        LED_7 = 7
        LED_8 = 8
        LED_9 = 9
        LED_10 = 10
        LED_11 = 11
        LED_12 = 12
        LED_13 = 13
        LED_14 = 14
        LED_15 = 15

    class LedStates(object):
        OFF = 'OFF'
        BLINKING_25 = 'BLINKING_25'
        BLINKING_50 = 'BLINKING_50'
        BLINKING_75 = 'BLINKING_75'
        ON = 'ON'

    def __init__(self, data):
        self._type = data['type']
        self._action = data['action']
        self._device_nr = data['device_nr']
        self._data = data['data']

    @property
    def type(self):
        type_map = {0: Event.Types.OUTPUT,
                    1: Event.Types.INPUT,
                    2: Event.Types.SENSOR,
                    20: Event.Types.THERMOSTAT,
                    251: Event.Types.LED_BLINK,
                    252: Event.Types.LED_ON,
                    253: Event.Types.POWER,
                    254: Event.Types.SYSTEM}
        return type_map.get(self._type, Event.Types.UNKNOWN)

    @property
    def data(self):
        if self.type == Event.Types.OUTPUT:
            timer_factor = None
            timer_value = Event._word_decode(self._data[2:])
            if self._data[1] == 0:
                timer_value = None
            elif self._data[1] == 1:
                timer_factor = 0.1
            elif self._data[1] == 2:
                timer_factor = 1
            elif self._data[2] == 3:
                timer_factor = 60
            return {'output': self._device_nr,
                    'status': self._action == 1,
                    'dimmer_value': self._data[0],
                    'timer_factor': timer_factor,
                    'timer_value': timer_value}
        if self.type == Event.Types.INPUT:
            return {'input': self._device_nr,
                    'status': self._action == 1}
        if self.type == Event.Types.SENSOR:
            sensor_type = Event.SensorType.UNKNOWN
            sensor_value = None
            if self._action == 0:
                sensor_type = Event.SensorType.TEMPERATURE
                sensor_value = self._data[1]
            elif self._action == 1:
                sensor_type = Event.SensorType.HUMIDITY
                sensor_value = self._data[1]
            elif self._action == 2:
                sensor_type = Event.SensorType.BRIGHTNESS
                sensor_value = Event._word_decode(self._data[0:2])
            return {'sensor': self._device_nr,
                    'type': sensor_type,
                    'value': sensor_value}
        if self.type == Event.Types.THERMOSTAT:
            origin_map = {0: Event.ThermostatOrigins.SLAVE,
                          1: Event.ThermostatOrigins.MASTER}
            return {'origin': origin_map.get(self._action, Event.ThermostatOrigins.UNKNOWN),
                    'thermostat': self._device_nr,
                    'mode': self._data[0],
                    'setpoint': self._data[1]}
        if self.type == Event.Types.LED_BLINK:
            word_25 = self._device_nr
            word_50 = Event._word_decode(self._data[0:2])
            word_75 = Event._word_decode(self._data[2:4])
            leds = {}
            for i in xrange(16):
                if word_25 & (1 << i):
                    leds[i] = Event.LedStates.BLINKING_25
                elif word_50 & (1 << i):
                    leds[i] = Event.LedStates.BLINKING_50
                elif word_75 & (1 << i):
                    leds[i] = Event.LedStates.BLINKING_75
                else:
                    leds[i] = Event.LedStates.OFF
            return {'chip': self._device_nr,
                    'leds': leds}
        if self.type == Event.Types.LED_ON:
            word_on = Event._word_decode(self._data[0:2])
            leds = {}
            for i in xrange(16):
                leds[i] = Event.LedStates.ON if word_on & (1 << i) else Event.LedStates.OFF
            return {'chip': self._device_nr,
                    'leds': leds}
        if self.type == Event.Types.POWER:
            return {'bus': Event.Bus.RS485 if self._device_nr == 0 else Event.Bus.CAN,
                    'power': self._data[0 > 1]}
        if self.type == Event.Types.SYSTEM:
            type_map = {0: Event.SystemEventTypes.EEPROM_ACTIVATE,
                        1: Event.SystemEventTypes.ONBOARD_TEMP_CHANGED}
            event_type = type_map.get(self._action, Event.SystemEventTypes.UNKNOWN)
            event_data = {'type': event_type}
            if event_type == Event.SystemEventTypes.ONBOARD_TEMP_CHANGED:
                event_data['temperature'] = self._data[0]
            return event_data
        return None

    @staticmethod
    def _word_decode(data):
        return WordField.decode(str(chr(data[0])) + str(chr(data[1])))

    def __str__(self):
        return '{0} ({1})'.format(self.type, self.data if self.type != Event.Types.UNKNOWN else self._type)
