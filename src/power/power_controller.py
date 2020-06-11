# Copyright (C) 2016 OpenMotics BV
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
The power controller module contains the PowerController class, abstracts
calls to the PowerCommunicator.
"""

from __future__ import absolute_import

from ioc import INJECTED, Inject, Injectable, Singleton
from power import power_api
from power.power_api import NUM_PORTS, P1_CONCENTRATOR

if False:  # MYPY
    from typing import Any, Dict, List, Optional, Tuple
    from power.power_communicator import PowerCommunicator


@Injectable.named('power_controller')
@Singleton
class PowerController(object):
    """ The PowerController abstracts calls to the communicator. """

    @Inject
    def __init__(self, power_communicator=INJECTED):
        # type: (PowerCommunicator) -> None
        self._power_communicator = power_communicator

    def get_module_current(self, module, phase=None):
        # type: (Dict[str,Any], Optional[int]) -> Optional[Tuple[Any, ...]]
        # TODO return type depends on module version/phase, translate here?
        cmd = power_api.get_current(module['version'], phase=phase)
        return self._power_communicator.do_command(module['address'], cmd)

    def get_module_frequency(self, module):
        # type: (Dict[str,Any]) ->  Optional[Tuple[Any, ...]]
        cmd = power_api.get_frequency(module['version'])
        return self._power_communicator.do_command(module['address'], cmd)

    def get_module_power(self, module):
        # type: (Dict[str,Any]) ->  Optional[Tuple[Any, ...]]
        cmd = power_api.get_power(module['version'])
        return self._power_communicator.do_command(module['address'], cmd)

    def get_module_voltage(self, module, phase=None):
        # type: (Dict[str,Any], Optional[int]) -> Optional[Tuple[Any, ...]]
        # TODO return type depends on module version/phase, translate here?
        cmd = power_api.get_voltage(module['version'], phase=phase)
        return self._power_communicator.do_command(module['address'], cmd)

    def get_module_day_energy(self, module):
        # type: (Dict[str,Any]) -> Optional[Tuple[Any, ...]]
        if module['version'] == P1_CONCENTRATOR:
            raise ValueError("Unknown power api version")
        else:
            cmd = power_api.get_day_energy(module['version'])
            return self._power_communicator.do_command(module['address'], cmd)

    def get_module_night_energy(self, module):
        # type: (Dict[str,Any]) -> Optional[Tuple[Any, ...]]
        if module['version'] == P1_CONCENTRATOR:
            raise ValueError("Unknown power api version")
        else:
            cmd = power_api.get_night_energy(module['version'])
            return self._power_communicator.do_command(module['address'], cmd)


@Injectable.named('p1_controller')
@Singleton
class P1Controller(object):
    """ The PowerController keeps track of the registered power modules. """

    @Inject
    def __init__(self, power_communicator=INJECTED):
        # type: (PowerCommunicator) -> None
        """
        Constructor a new P1Controller.
        """
        self._power_communicator = power_communicator

    # TODO: rework get_realtime_power or call this there.
    def get_realtime(self, modules):
        # type: (Dict[str,Dict[str,Any]]) -> List[Dict[str,Any]]
        """
        Get the realtime p1 measurement values.
        """
        values = []
        for module_id, module in sorted(modules.items()):
            if module['version'] == power_api.P1_CONCENTRATOR:
                statuses = self.get_module_status(modules[module_id])
                timestamps = self.get_module_timestamp(modules[module_id])
                eans1 = self.get_module_meter(modules[module_id], type=1)
                eans2 = self.get_module_meter(modules[module_id], type=2)
                currents = self.get_module_current(modules[module_id])
                voltages = self.get_module_voltage(modules[module_id])
                tariffs1 = self.get_module_injection_tariff(modules[module_id], type=1)
                tariffs2 = self.get_module_injection_tariff(modules[module_id], type=2)
                tariff_indicators = self.get_module_tariff_indicator(modules[module_id])
                gas_consumptions = self.get_module_gas_consumption(modules[module_id])

                for port_id, status in enumerate(statuses):
                    if status:
                        values.append({'device_id': '{}.{}'.format(module['address'], port_id),
                                       'module_id': module_id,
                                       'port_id': port_id,
                                       'timestamp': timestamps[port_id],
                                       'gas': {'ean': eans2[port_id].strip(),
                                               'consumption': gas_consumptions[port_id]},
                                       'electricity': {'ean': eans1[port_id].strip(),
                                                       'current': currents[port_id],
                                                       'voltage': voltages[port_id],
                                                       'tariff_low': tariffs1[port_id],
                                                       'tariff_normal': tariffs2[port_id],
                                                       'tariff_indicator': tariff_indicators[port_id]}})

        return values

    def get_module_status(self, module):
        # type: (Dict[str,Any]) -> List[bool]
        cmd = power_api.get_status_p1(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        status = []
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            status.append((payload & 1 << port_id) != 0)
        return status

    def get_module_meter(self, module, type=1):
        # type: (Dict[str,Any], int) -> List[str]
        """
        Request meter id for all meters and parse repsonse.
        """
        cmd = power_api.get_meter_p1(module['version'], type=type)

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        meters = []
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            meters.append(payload[port_id * 28:(port_id + 1) * 28])
        return meters

    def get_module_timestamp(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        """
        Request timestamps for all meters and parse repsonse.
        """
        cmd = power_api.get_timestamp_p1(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        timestamps = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                timestamps.append(float(payload[port_id * 13:(port_id + 1) * 13][:12]))
            except ValueError:
                timestamps.append(None)
        return timestamps

    def get_module_gas_consumption(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        """
        Request gas consumptions for all meters and parse repsonse.
        """
        cmd = power_api.get_gas_consumption_p1(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        consumptions = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                consumptions.append(float(payload[port_id * 12:(port_id + 1) * 12][:9]))
            except ValueError:
                consumptions.append(None)
        return consumptions

    def get_module_injection_tariff(self, module, type=None):
        # type: (Dict[str,Any], int) -> List[Optional[float]]
        """
        Request consumption tariff for all meters and parse repsonse.
        """
        cmd = power_api.get_injection_tariff_p1(module['version'], type=type)

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        consumptions = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                consumptions.append(float(payload[port_id * 14:(port_id + 1) * 14][:10]))
            except ValueError:
                consumptions.append(None)
        return consumptions

    def get_module_tariff_indicator(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        """
        Request tariff indicator for all meters and parse repsonse.
        """
        cmd = power_api.get_tariff_indicator_p1(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        consumptions = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                consumptions.append(float(payload[port_id * 4:(port_id + 1) * 4]))
            except ValueError:
                consumptions.append(None)
        return consumptions

    def get_module_current(self, module):
        # type: (Dict[str,Any]) -> List[Dict[str,Optional[float]]]
        """
        Request phase voltages for all meters and parse repsonse.
        """
        payloads = {}
        for i in range(1, 4):
            cmd = power_api.get_current(module['version'], phase=i)

            result = self._power_communicator.do_command(module['address'], cmd)
            if result:
                payloads['phase{}'.format(i)] = result[0]

        currents = []
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            phases = {}  # type: Dict[str,Optional[float]]
            for phase, payload in payloads.items():
                try:
                    phases[phase] = float(payload[port_id * 5:(port_id + 1) * 6][:3])
                except ValueError:
                    phases[phase] = None
            currents.append(phases)
        return currents

    def get_module_voltage(self, module):
        # type: (Dict[str,Any]) -> List[Dict[str,Optional[float]]]
        """
        Request phase voltages for all meters and parse repsonse.
        """
        payloads = {}
        for i in range(1, 4):
            cmd = power_api.get_voltage(module['version'], phase=i)

            result = self._power_communicator.do_command(module['address'], cmd)
            if result:
                payloads['phase{}'.format(i)] = result[0]

        voltages = []
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            phases = {} # type: Dict[str,Optional[float]]
            for phase, payload in payloads.items():
                try:
                    phases[phase] = float(payload[port_id * 7:(port_id + 1) * 7][:5])
                except ValueError:
                    phases[phase] = None
            voltages.append(phases)
        return voltages

    def get_module_delivered_power(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        cmd = power_api.get_delivered_power(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        delivered = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                delivered.append(float(payload[port_id * 9:(port_id + 1) * 9][:6]))
            except ValueError:
                delivered.append(None)
        return delivered

    def get_module_received_power(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        cmd = power_api.get_received_power(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        received = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                received.append(float(payload[port_id * 9:(port_id + 1) * 9][:6]))
            except ValueError:
                received.append(None)
        return received

    def get_module_day_energy(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        cmd = power_api.get_day_energy(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        energy = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                energy.append(float(payload[port_id * 14:(port_id + 1) * 14][:10]))
            except ValueError:
                energy.append(None)
        return energy

    def get_module_night_energy(self, module):
        # type: (Dict[str,Any]) -> List[Optional[float]]
        cmd = power_api.get_night_energy(module['version'])

        result = self._power_communicator.do_command(module['address'], cmd)
        if result:
            payload = result[0]

        energy = []  # type: List[Optional[float]]
        for port_id in range(NUM_PORTS[P1_CONCENTRATOR]):
            try:
                energy.append(float(payload[port_id * 14:(port_id + 1) * 14][:10]))
            except ValueError:
                energy.append(None)
        return energy
