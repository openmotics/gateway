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

import logging
from threading import Lock
from simple_pid import PID
from ioc import Inject, INJECTED
from serial_utils import CommunicationTimedOutException
from gateway.models import ThermostatGroup, Thermostat

if False:  # MYPY
    from typing import Optional, List, Callable
    from gateway.gateway_api import GatewayApi
    from gateway.thermostat.gateway.pump_valve_controller import PumpValveController

logger = logging.getLogger('openmotics')


@Inject
class ThermostatPid(object):

    DEFAULT_KP = 5.0
    DEFAULT_KI = 0.0
    DEFAULT_KD = 2.0

    def __init__(self, thermostat, pump_valve_controller, gateway_api=INJECTED):
        # type: (Thermostat, PumpValveController, GatewayApi) -> None
        self._gateway_api = gateway_api
        self._pump_valve_controller = pump_valve_controller
        self._thermostat_change_lock = Lock()
        self._heating_valve_ids = []  # type: List[int]
        self._cooling_valve_ids = []  # type: List[int]
        self._report_state_callbacks = []  # type: List[Callable[[int, str, float, Optional[float], List[float], int], None]]
        self._thermostat = thermostat
        self._mode = thermostat.thermostat_group.mode
        self._active_preset = thermostat.active_preset
        self._pid = PID(Kp=ThermostatPid.DEFAULT_KP,
                        Ki=ThermostatPid.DEFAULT_KI,
                        Kd=ThermostatPid.DEFAULT_KD)
        self._current_temperature = None  # type: Optional[float]
        self._errors = 0
        self._current_steering_power = None  # type: Optional[int]
        self._current_enabled = None  # type: Optional[bool]
        self._current_preset_type = None  # type: Optional[str]
        self._current_setpoint = None  # type: Optional[float]
        self.update_thermostat(thermostat)

    @property
    def enabled(self):  # type: () -> bool
        # 1. PID loop is initialized
        # 2. Sensor is valid
        # 3. Outputs configured (heating or cooling)
        if self._thermostat.sensor is None:
            return False
        if len(self._heating_valve_ids) == 0 and len(self._cooling_valve_ids) == 0:
            return False
        if not self._thermostat.thermostat_group.on:
            return False
        if self._errors > 5:
            return False
        return True

    @property
    def valve_ids(self):  # type: () -> List[int]
        return self._heating_valve_ids + self._cooling_valve_ids

    def update_thermostat(self, thermostat):  # type: (Thermostat) -> None
        with self._thermostat_change_lock:
            # cache these values to avoid DB lookups on every tick
            self._thermostat = thermostat
            self._mode = thermostat.thermostat_group.mode
            self._active_preset = thermostat.active_preset

            self._heating_valve_ids = [valve.id for valve in thermostat.heating_valves]
            self._cooling_valve_ids = [valve.id for valve in thermostat.cooling_valves]

            if thermostat.thermostat_group.mode == ThermostatGroup.Modes.HEATING:
                pid_p = thermostat.pid_heating_p if thermostat.pid_heating_p is not None else self.DEFAULT_KP
                pid_i = thermostat.pid_heating_i if thermostat.pid_heating_i is not None else self.DEFAULT_KI
                pid_d = thermostat.pid_heating_d if thermostat.pid_heating_d is not None else self.DEFAULT_KD
                setpoint = self._active_preset.heating_setpoint if self._active_preset is not None else 14.0
            else:
                pid_p = thermostat.pid_cooling_p if thermostat.pid_cooling_p is not None else self.DEFAULT_KP
                pid_i = thermostat.pid_cooling_i if thermostat.pid_cooling_i is not None else self.DEFAULT_KI
                pid_d = thermostat.pid_cooling_d if thermostat.pid_cooling_d is not None else self.DEFAULT_KD
                setpoint = self._active_preset.cooling_setpoint if self._active_preset is not None else 30.0

            self._pid.tunings = (pid_p, pid_i, pid_d)
            self._pid.setpoint = setpoint
            self._pid.output_limits = (-100, 100)
            self._errors = 0

    @property
    def thermostat(self):  # type: () -> Thermostat
        return self._thermostat

    def subscribe_state_changes(self, callback):
        # type: (Callable[[int, str, float, Optional[float], List[float], int], None]) -> None
        self._report_state_callbacks.append(callback)

    def report_state_change(self):  # type: () -> None
        # TODO: Only invoke callback if change occurred
        room_number = 255 if self._thermostat.room is None else self._thermostat.room.number
        for callback in self._report_state_callbacks:
            callback(self.number, self._active_preset.type, self.setpoint, self._current_temperature,
                     self.get_active_valves_percentage(), room_number)

    def tick(self):  # type: () -> bool
        if self.enabled != self._current_enabled:
            logger.info('Thermostat {0}: {1}abled in {2} mode'.format(self._thermostat.number, 'En' if self.enabled else 'Dis', self._mode))
            self._current_enabled = self.enabled

        if not self.enabled:
            self.switch_off()
            return False

        if self._current_preset_type != self._active_preset.type or self._current_setpoint != self._pid.setpoint:
            logger.info('Thermostat {0}: Preset {1} with setpoint {2}'
                        .format(self._thermostat.number, self._active_preset.type, self._pid.setpoint))
            self._current_preset_type = self._active_preset.type
            self._current_setpoint = self._pid.setpoint

        try:
            current_temperature = None  # type: Optional[float]
            if self._thermostat.sensor is not None:
                current_temperature = self._gateway_api.get_sensor_temperature_status(self._thermostat.sensor.number)
            if current_temperature is not None:
                self._current_temperature = current_temperature
            else:
                # Keep using old temperature reading and count the errors
                logger.warning('Thermostat {0}: Could not read current temperature, use last value of {1}'
                               .format(self._thermostat.number, self._current_temperature))
                self._errors += 1

            if self._current_temperature is not None:
                output_power = self._pid(self._current_temperature)
            else:
                logger.error('Thermostat {0}: No known current temperature. Cannot calculate desired output'
                             .format(self._thermostat.number))
                self._errors += 1
                output_power = 0

            # Heating needed while in cooling mode OR cooling needed while in heating mode
            # -> no active airon required, rely on losses/gains of system to reach equilibrium
            if ((self._mode == ThermostatGroup.Modes.COOLING and output_power > 0) or
                    (self._mode == ThermostatGroup.Modes.HEATING and output_power < 0)):
                output_power = 0
            self.steer(output_power)
            self.report_state_change()
            return True
        except CommunicationTimedOutException:
            logger.error('Thermostat {0}: Error in PID tick'.format(self._thermostat.number))
            self._errors += 1
            return False

    def get_active_valves_percentage(self):  # type: () -> List[float]
        return [self._pump_valve_controller.get_valve_driver(valve.id).percentage for valve in self._thermostat.active_valves]

    @property
    def number(self):  # type: () -> int
        return self._thermostat.number

    @property
    def setpoint(self):  # type: () -> float
        return self._pid.setpoint

    def steer(self, power):  # type: (int) -> None
        if self._current_steering_power != power:
            logger.info('Thermostat {0}: Steer to {1} '.format(self._thermostat.number, power))
            self._current_steering_power = power

        # Configure valves and set desired opening
        # TODO: Check union to avoid opening same valves in heating and cooling
        if power > 0:
            self._pump_valve_controller.set_valves(0, self._cooling_valve_ids, mode=self._thermostat.valve_config)
            self._pump_valve_controller.set_valves(power, self._heating_valve_ids, mode=self._thermostat.valve_config)
        else:
            power = abs(power)
            self._pump_valve_controller.set_valves(0, self._heating_valve_ids, mode=self._thermostat.valve_config)
            self._pump_valve_controller.set_valves(power, self._cooling_valve_ids, mode=self._thermostat.valve_config)

        # Effectively steer pumps and valves according to needs
        self._pump_valve_controller.steer()

    def switch_off(self):  # type: () -> None
        self.steer(0)

    @property
    def kp(self):  # type: () -> float
        return self._pid.Kp

    @kp.setter
    def kp(self, kp):  # type: (float) -> None
        self._pid.Kp = kp

    @property
    def ki(self):  # type: () -> float
        return self._pid.Ki

    @ki.setter
    def ki(self, ki):  # type: (float) -> None
        self._pid.Ki = ki

    @property
    def kd(self):  # type: () -> float
        return self._pid.Kd

    @kd.setter
    def kd(self, kd):  # type: (float) -> None
        self._pid.Kd = kd
