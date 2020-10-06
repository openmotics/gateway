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
The vpn_service asks the OpenMotics cloud it a vpn tunnel should be opened. It starts openvpn
if required. On each check the vpn_service sends some status information about the outputs and
thermostats to the cloud, to keep the status information in the cloud in sync.
"""

from __future__ import absolute_import

from platform_utils import System
System.import_libs()

import glob
import logging
import os
import subprocess
import time
import traceback
from collections import deque
from threading import Thread

import requests
import six
import ujson as json
from six.moves.configparser import ConfigParser

import constants
from bus.om_bus_client import MessageClient
from bus.om_bus_events import OMBusEvents
from gateway.initialize import initialize
from gateway.models import Config
from ioc import INJECTED, Inject


if False:  # MYPY
    from typing import Any, Deque, Dict, Optional

REBOOT_TIMEOUT = 900
CHECK_CONNECTIVITY_TIMEOUT = 60
DEFAULT_SLEEP_TIME = 30

logger = logging.getLogger("openmotics")


def setup_logger():
    """ Setup the OpenMotics logger. """
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


def reboot_gateway():
    """ Reboot the gateway. """
    subprocess.call('sync && reboot', shell=True)


class VpnController(object):
    """ Contains methods to check the vpn status, start and stop the vpn. """

    vpn_service = System.get_vpn_service()
    start_cmd = "systemctl start " + vpn_service + " > /dev/null"
    stop_cmd = "systemctl stop " + vpn_service + " > /dev/null"
    check_cmd = "systemctl is-active " + vpn_service + " > /dev/null"

    def __init__(self):
        self.vpn_connected = False
        t_vpn_connected = Thread(target=self._vpn_connected)
        t_vpn_connected.daemon = True
        t_vpn_connected.start()

    @staticmethod
    def start_vpn():
        """ Start openvpn """
        logger.info('Starting VPN')
        return subprocess.call(VpnController.start_cmd, shell=True) == 0

    @staticmethod
    def stop_vpn():
        """ Stop openvpn """
        logger.info('Stopping VPN')
        return subprocess.call(VpnController.stop_cmd, shell=True) == 0

    @staticmethod
    def check_vpn():
        """ Check if openvpn is running """
        return subprocess.call(VpnController.check_cmd, shell=True) == 0

    def _vpn_connected(self):
        """ Checks if the VPN tunnel is connected """
        while True:
            try:
                routes = subprocess.check_output('ip r | grep tun | grep via || true', shell=True).strip()
                # example output:
                # 10.0.0.0/24 via 10.37.0.5 dev tun0\n
                # 10.37.0.1 via 10.37.0.5 dev tun0
                result = False
                if routes:
                    vpn_servers = [route.split(' ')[0] for route in routes.split('\n') if '/' not in route]
                    for vpn_server in vpn_servers:
                        if VPNService.ping(vpn_server, verbose=False):
                            result = True
                            break
                self.vpn_connected = result
            except Exception as ex:
                logger.info('Exception occured during vpn connectivity test: {0}'.format(ex))
                self.vpn_connected = False
            time.sleep(5)


class Cloud(object):
    """ Connects to the cloud """

    @Inject
    def __init__(self, url, sleep_time=DEFAULT_SLEEP_TIME, message_client=INJECTED):
        self._url = url
        self._message_client = message_client
        self._last_connect = time.time()
        self._sleep_time = sleep_time
        self._intervals = {}
        self._configuration = {}

    def call_home(self, extra_data):
        """ Call home reporting our state, and optionally get new settings or other stuff """
        try:
            request = requests.post(self._url,
                                    data={'extra_data': json.dumps(extra_data)},
                                    timeout=10.0)
            data = json.loads(request.text)

            if 'sleep_time' in data:
                self._sleep_time = data['sleep_time']
            else:
                self._sleep_time = DEFAULT_SLEEP_TIME

            if 'configuration' in data:
                configuration_changed = cmp(self._configuration, data['configuration']) != 0
                if configuration_changed:
                    for setting, value in data['configuration'].items():
                        Config.set(setting, value)
                    logger.info('Configuration changed: {0}'.format(data['configuration']))

                # update __configuration when storing config is successful
                self._configuration = data['configuration']

            if 'intervals' in data:
                # check if interval changes occurred and distribute interval changes
                intervals_changed = cmp(self._intervals, data['intervals']) != 0
                if intervals_changed and self._message_client is not None:
                    self._message_client.send_event(OMBusEvents.METRICS_INTERVAL_CHANGE, data['intervals'])
                    logger.info('intervals changed: {0}'.format(data['intervals']))

                # update __intervals when sending is successful
                self._intervals = data['intervals']

            self._last_connect = time.time()
            if self._message_client is not None:
                self._message_client.send_event(OMBusEvents.CLOUD_REACHABLE, True)
            return {'open_vpn': data['open_vpn'],
                    'success': True}
        except Exception as ex:
            message = str(ex)
            if 'Connection refused' in message:
                logger.warning('Cannot connect to the OpenMotics service')
            else:
                logger.info('Exception occured during check: {0}'.format(ex))
            if self._message_client is not None:
                self._message_client.send_event(OMBusEvents.CLOUD_REACHABLE, False)
            return {'open_vpn': True,
                    'success': False}

    def get_sleep_time(self):
        """ Get the time to sleep between two cloud checks. """
        return self._sleep_time

    def get_last_connect(self):
        """ Get the timestamp of the last connection with the cloud. """
        return self._last_connect


class Gateway(object):
    """ Class to get the current status of the gateway. """

    def __init__(self, host="127.0.0.1"):
        self.__host = host
        self.__last_pulse_counters = None

    def do_call(self, uri):
        """ Do a call to the webservice, returns a dict parsed from the json returned by the webserver. """
        try:
            request = requests.get("http://" + self.__host + "/" + uri, timeout=15.0)
            return json.loads(request.text)
        except Exception as ex:
            message = str(ex)
            if 'Connection refused' in message:
                logger.warning('Cannot connect to the OpenMotics service')
            else:
                logger.error('Exception during Gateway call: {0} {1}'.format(message, uri))
            return

    def get_real_time_power(self):
        """ Get the real time power measurements. """
        data = self.do_call("get_realtime_power?token=None")
        if data is not None and data['success']:
            del data['success']
            return data
        return

    def get_pulse_counter_diff(self):
        """ Get the pulse counter differences. """
        data = self.do_call("get_pulse_counter_status?token=None")
        if data is not None and data['success']:
            counters = data['counters']

            if self.__last_pulse_counters is None:
                ret = [0 for _ in range(0, 24)]
            else:
                ret = [Gateway.__counter_diff(counters[i], self.__last_pulse_counters[i])
                       for i in range(0, 24)]

            self.__last_pulse_counters = counters
            return ret
        return

    @staticmethod
    def __counter_diff(current, previous):
        """ Calculate the diff between two counter values. """
        diff = current - previous
        return diff if diff >= 0 else 65536 - previous + current

    def get_outputs_status(self):
        data = self.do_call("get_output_status?token=None")
        if data is not None and data['success']:
            ret = []
            for output in data['status']:
                parsed_data = {}
                for k in output.keys():
                    if k in ['id', 'status', 'dimmer', 'locked']:
                        parsed_data[k] = output[k]
                ret.append(parsed_data)
            return ret
        return

    def get_inputs_status(self):
        """ Get the inputs status. """
        data = self.do_call("get_input_status?token=None")
        if data is not None and data['success']:
            return [(inp["id"], inp["status"]) for inp in data['status']]
        return

    def get_shutters_status(self):
        """ Get the shutters status. """
        data = self.do_call("get_shutter_status?token=None")
        if data is not None and data['success']:
            return_data = []
            for shutter_id, details in six.iteritems(data['detail']):
                last_change = details['last_change']
                if last_change == 0.0:
                    entry = (int(shutter_id), details['state'].upper())
                else:
                    entry = (int(shutter_id), details['state'].upper(), last_change)
                return_data.append(entry)
            return return_data
        return

    def get_thermostats(self):
        """ Fetch the setpoints for the enabled thermostats from the webservice. """
        data = self.do_call("get_thermostat_status?token=None")
        if data is None or data['success'] is False:
            return None
        ret = {'thermostats_on': data['thermostats_on'],
               'automatic': data['automatic'],
               'cooling': data['cooling']}
        thermostats = []
        for thermostat in data['status']:
            to_add = {}
            for field in ['id', 'act', 'csetp', 'mode', 'output0', 'output1', 'outside', 'airco']:
                to_add[field] = thermostat[field]
            thermostats.append(to_add)
        ret['status'] = thermostats
        return ret

    def get_errors(self):
        """ Get the errors on the gateway. """
        data = self.do_call("get_errors?token=None")
        if data:
            if data['errors'] is not None:
                master_errors = sum([error[1] for error in data['errors']])
            else:
                master_errors = 0

            return {'master_errors': master_errors,
                    'master_last_success': data['master_last_success'],
                    'power_last_success': data['power_last_success']}
        return

    def get_local_ip_address(self):
        """ Get the local ip address. """
        _ = self  # Needs to be an instance method
        return System.get_ip_address()


class DataCollector(object):
    """ Defines a function to retrieve data, the period between two collections """

    def __init__(self, fct, period=0):
        """
        Create a collector with a function to call and a period.
        If the period is 0, the collector will be executed on each call.
        """
        self.__function = fct
        self.__period = period
        self.__last_collect = 0

    def __should_collect(self):
        """ Should we execute the collect? """

        return self.__period == 0 or time.time() >= self.__last_collect + self.__period

    def collect(self):
        """ Execute the collect if required, return None otherwise. """
        try:
            if self.__should_collect():
                if self.__period != 0:
                    self.__last_collect = time.time()
                return self.__function()
            else:
                return
        except Exception as ex:
            logger.info('Error while collecting data: {0}'.format(ex))
            traceback.print_exc()
            return


class VPNService(object):
    """ The VPNService contains all logic to be able to send the heartbeat and check whether the VPN should be opened """

    @Inject
    def __init__(self, message_client=INJECTED):
        # type: (MessageClient) -> None
        config = ConfigParser()
        config.read(constants.get_config_file())

        self._message_client = message_client
        if self._message_client is not None:
            self._message_client.add_event_handler(self._event_receiver)
            self._message_client.set_state_handler(self._check_state)

        self._last_cycle = 0.0
        self._cloud_enabled = True
        self._sleep_time = 0.0  # type: Optional[float]
        self._previous_sleep_time = 0
        self._vpn_open = False
        self._debug_data = {'energy': {},
                            'master': {}}  # type: Dict[str,Dict[float,Any]]
        self._eeprom_events = deque()  # type: Deque[bool]
        self._gateway = Gateway()
        self._vpn_controller = VpnController()
        self._cloud = Cloud(config.get('OpenMotics', 'vpn_check_url') % config.get('OpenMotics', 'uuid'))

        self._collectors = {'thermostats': DataCollector(self._gateway.get_thermostats, 60),
                            'inputs': DataCollector(self._gateway.get_inputs_status),
                            # outputs was deprecated and replaced by outputs_status to get more detailed information
                            # don't re-use 'outputs' key as it will cause backwards compatibility changes on the cloud
                            'outputs_status': DataCollector(self._gateway.get_outputs_status),
                            'shutters': DataCollector(self._gateway.get_shutters_status),
                            'pulses': DataCollector(self._gateway.get_pulse_counter_diff, 60),
                            'power': DataCollector(self._gateway.get_real_time_power),
                            'errors': DataCollector(self._gateway.get_errors, 600),
                            'local_ip': DataCollector(self._gateway.get_local_ip_address, 1800)}

    @staticmethod
    def ping(target, verbose=True):
        """ Check if the target can be pinged. Returns True if at least 1/4 pings was successful. """
        if target is None:
            return False

        # The popen_timeout has been added as a workaround for the hanging subprocess
        # If NTP date changes the time during a execution of a sub process this hangs forever.
        def popen_timeout(command, timeout):
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(timeout):
                time.sleep(1)
                if p.poll() is not None:
                    stdout_data, stderr_data = p.communicate()
                    if p.returncode == 0:
                        return True
                    raise Exception('Non-zero exit code. Stdout: {0}, stderr: {1}'.format(stdout_data, stderr_data))
            logger.warning('Got timeout during ping to {0}'.format(target))
            p.kill()
            return False

        if verbose is True:
            logger.info("Testing ping to {0}".format(target))
        try:
            # Ping returns status code 0 if at least 1 ping is successful
            return popen_timeout(["ping", "-c", "3", target], 10)
        except Exception as ex:
            logger.error("Error during ping: {0}".format(ex))
            return False

    @staticmethod
    def has_connectivity():
        # Check connectivity by using ping to recover from a messed up network stack on the BeagleBone
        # Prefer using OpenMotics infrastructure first

        if VPNService.ping('cloud.openmotics.com'):
            # OpenMotics infrastructure can be pinged
            # > Connectivity
            return True
        can_ping_internet_by_fqdn = VPNService.ping('example.com') or VPNService.ping('google.com')
        if can_ping_internet_by_fqdn:
            # Public internet servers can be pinged by FQDN
            # > Assume maintenance on OpenMotics infrastructure. Sufficient connectivity
            return True
        can_ping_internet_by_ip = VPNService.ping('8.8.8.8') or VPNService.ping('1.1.1.1')
        if can_ping_internet_by_ip:
            # Public internet servers can be pinged by IP, but not by FQDN
            # > Assume DNS resolving issues. Insufficient connectivity
            return False
        # Public internet servers cannot be pinged by IP, nor by FQDN
        can_ping_default_gateway = VPNService.ping(VPNService._get_gateway())
        if can_ping_default_gateway:
            # > Assume ISP outage. Sufficient connectivity
            return True
        # > Assume broken TCP stack. No connectivity
        return False

    def _get_debug_dumps(self, dump_type):
        # type: (str) -> Dict[float,Dict[str,Any]]
        debug_data = self._debug_data[dump_type]
        found_timestamps = []
        for filename in glob.glob('/tmp/debug_{0}_*.json'.format(dump_type)):
            timestamp = os.path.getmtime(filename)
            if timestamp not in debug_data:
                with open(filename, 'r') as debug_file:
                    try:
                        debug_data[timestamp] = json.load(debug_file)
                    except ValueError as ex:
                        logger.warning('Error parsing crash dump: {0}'.format(ex))
                        debug_data[timestamp] = {}
            found_timestamps.append(timestamp)
        for timestamp in debug_data.keys():
            if timestamp not in found_timestamps:
                del debug_data[timestamp]
        return debug_data

    def _clean_debug_dumps(self, dump_type):
        # type: (str) -> None
        for filename in glob.glob('/tmp/debug_{0}_*.json'.format(dump_type)):
            timestamp = os.path.getmtime(filename)
            if timestamp in self._debug_data[dump_type]:
                try:
                    os.remove(filename)
                except Exception as ex:
                    logger.error('Could not remove debug file {0}: {1}'.format(filename, ex))

    @staticmethod
    def _get_gateway():
        """ Get the default gateway. """
        try:
            return subprocess.check_output("ip r | grep '^default via' | awk '{ print $3; }'", shell=True)
        except Exception as ex:
            logger.error("Error during get_gateway: {0}".format(ex))
            return

    def _check_state(self):
        return {'cloud_disabled': not self._cloud_enabled,
                'sleep_time': self._sleep_time,
                'cloud_last_connect': None if self._cloud is None else self._cloud.get_last_connect(),
                'vpn_open': self._vpn_open,
                'last_cycle': self._last_cycle}

    def _event_receiver(self, event, payload):
        _ = payload
        if event == OMBusEvents.DIRTY_EEPROM:
            self._eeprom_events.appendleft(True)

    @staticmethod
    def _unload_queue(queue):
        events = []
        try:
            while True:
                events.append(queue.pop())
        except IndexError:
            pass
        return events

    def _set_vpn(self, should_open):
        is_running = VpnController.check_vpn()
        if should_open and not is_running:
            logger.info("opening vpn")
            VpnController.start_vpn()
        elif not should_open and is_running:
            logger.info("closing vpn")
            VpnController.stop_vpn()
        is_running = VpnController.check_vpn()
        self._vpn_open = is_running and self._vpn_controller.vpn_connected
        if self._message_client is not None:
            self._message_client.send_event(OMBusEvents.VPN_OPEN, self._vpn_open)

    def start(self):
        self._check_vpn()

    def _check_vpn(self):
        # type: () -> None
        while True:
            self._last_cycle = time.time()
            try:
                start_time = time.time()

                # Check whether connection to the Cloud is enabled/disabled
                cloud_enabled = Config.get('cloud_enabled')
                if cloud_enabled is False:
                    self._sleep_time = None
                    self._set_vpn(False)
                    if self._message_client is not None:
                        self._message_client.send_event(OMBusEvents.VPN_OPEN, False)
                        self._message_client.send_event(OMBusEvents.CLOUD_REACHABLE, False)

                    time.sleep(DEFAULT_SLEEP_TIME)
                    continue

                call_data = {'events': {}}  # type: Dict[str,Dict[str,Any]]

                # Events  # TODO: Replace this by websocket events in the future
                dirty_events = VPNService._unload_queue(self._eeprom_events)
                if dirty_events:
                    call_data['events']['DIRTY_EEPROM'] = True

                # Collect data to be send to the Cloud
                for collector_name in self._collectors:
                    collector = self._collectors[collector_name]
                    data = collector.collect()
                    if data is not None:
                        call_data[collector_name] = data

                dumps = {}
                dumps.update(self._get_debug_dumps('energy'))
                dumps.update(self._get_debug_dumps('master'))

                dump_info = {k: v.get('info', {}) for k, v in dumps.items()}
                call_data['debug'] = {'dumps': {}, 'dump_info': dump_info}

                # Include full dumps when support is enabled
                if Config.get('cloud_support', False):
                    call_data['debug']['dumps'] = dumps

                # Send data to the cloud and see if the VPN should be opened
                feedback = self._cloud.call_home(call_data)

                if feedback['success']:
                    self._clean_debug_dumps('energy')
                    self._clean_debug_dumps('master')

                if self._cloud.get_last_connect() < time.time() - CHECK_CONNECTIVITY_TIMEOUT:
                    connectivity = VPNService.has_connectivity()
                    self._message_client.send_event(OMBusEvents.CONNECTIVITY, connectivity)
                    if not connectivity and self._cloud.get_last_connect() < time.time() - REBOOT_TIMEOUT:
                        reboot_gateway()
                else:
                    self._message_client.send_event(OMBusEvents.CONNECTIVITY, True)
                # Open or close the VPN
                self._set_vpn(feedback['open_vpn'])

                # Getting some sleep
                exec_time = time.time() - start_time
                if exec_time > 2:
                    logger.warning('Heartbeat took more than 2s to complete: {0:.2f}s'.format(exec_time))
                sleep_time = self._cloud.get_sleep_time()
                if self._previous_sleep_time != sleep_time:
                    logger.info('Set sleep interval to {0}s'.format(sleep_time))
                    self._previous_sleep_time = sleep_time
                time.sleep(sleep_time)
            except Exception as ex:
                logger.error("Error during vpn check loop: {0}".format(ex))
                time.sleep(1)


if __name__ == '__main__':
    setup_logger()
    initialize(message_client_name='vpn_service')

    logger.info("Starting VPN service")
    vpn_service = VPNService()
    vpn_service.start()
