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
""" Includes the WebService class """


import base64
import cherrypy
import constants
import logging
import msgpack
import os
import requests
import subprocess
import sys
import threading
import time
import uuid
import ujson as json
from ioc import Injectable, Inject, INJECTED, Singleton
from cherrypy.lib.static import serve_file
from decorator import decorator
from bus.om_bus_events import OMBusEvents
from gateway.shutters import ShutterController
from gateway.maintenance_communicator import InMaintenanceModeException
from power.power_communicator import InAddressModeException
from platform_utils import System
from serial_utils import CommunicationTimedOutException
from gateway.websockets import OMPlugin, OMSocketTool, MetricsSocket, EventsSocket, MaintenanceSocket
from models import Feature

logger = logging.getLogger("openmotics")


class FloatWrapper(float):
    """ Wrapper for float value that limits the number of digits when printed. """

    def __repr__(self):
        return '%.2f' % self


class BadRequestException(Exception):
        pass


def limit_floats(struct):
    """
    Usage: json.dumps(limit_floats(struct)). This limits the number of digits in the json string.
    :param struct: Structure of which floats will be shortended
    """
    if isinstance(struct, (list, tuple)):
        return [limit_floats(element) for element in struct]
    elif isinstance(struct, dict):
        return dict((key, limit_floats(value)) for key, value in struct.items())
    elif isinstance(struct, float):
        return FloatWrapper(struct)
    else:
        return struct


def error_generic(status, message, *args, **kwargs):
    _ = args, kwargs
    cherrypy.response.headers["Content-Type"] = "application/json"
    cherrypy.response.status = status
    return json.dumps({"success": False, "msg": message})


def error_unexpected():
    cherrypy.response.headers["Content-Type"] = "application/json"
    cherrypy.response.status = 500  # Internal Server Error
    return json.dumps({"success": False, "msg": "unknown_error"})


cherrypy.config.update({'error_page.404': error_generic,
                        'error_page.401': error_generic,
                        'error_page.503': error_generic,
                        'request.error_response': error_unexpected})


def params_parser(params, param_types):
    for key in set(params).intersection(set(param_types)):
        value = params[key]
        if value is None:
            continue
        if isinstance(value, basestring) and value.lower() in ['null', 'none', '']:
            params[key] = None
        else:
            if isinstance(param_types[key], list):
                if value not in param_types[key]:
                    raise ValueError('Value has invalid value')
            elif param_types[key] == bool:
                params[key] = str(value).lower() not in ['false', '0', '0.0', 'no']
            elif param_types[key] == 'json':
                params[key] = json.loads(value)
            elif param_types[key] == int:
                # Double convertion. Params come in as strings, and int('0.0') fails, while int(float('0.0')) works as expected
                params[key] = int(float(value))
            else:
                params[key] = param_types[key](value)


def params_handler(**kwargs):
    """ Convert specified request params. """
    request = cherrypy.request
    try:
        params_parser(request.params, kwargs)
    except ValueError:
        cherrypy.response.headers['Content-Type'] = 'application/json'
        cherrypy.response.status = 406  # No Acceptable
        cherrypy.response.body = json.dumps({'success': False,
                                             'msg': 'invalid_parameters'})
        request.handler = None


def timestamp_handler():
    request = cherrypy.request
    if 'fe_time' in request.params:
        del request.params["fe_time"]


def cors_handler():
    if cherrypy.request.method == 'OPTIONS':
        cherrypy.request.handler = None
    cherrypy.response.headers['Access-Control-Allow-Origin'] = '*'
    cherrypy.response.headers['Access-Control-Allow-Headers'] = 'Authorization'
    cherrypy.response.headers['Access-Control-Allow-Methods'] = 'GET'


def authentication_handler(pass_token=False):
    request = cherrypy.request
    if request.method == 'OPTIONS':
        return
    try:
        token = None
        if 'token' in request.params:
            token = request.params.pop('token')
        if token is None:
            header = request.headers.get('Authorization')
            if header is not None and 'Bearer ' in header:
                token = header.replace('Bearer ', '')
        if token is None:
            header = request.headers.get('Sec-WebSocket-Protocol')
            if header is not None and 'authorization.bearer.' in header:
                unpadded_base64_token = header.replace('authorization.bearer.', '')
                base64_token = unpadded_base64_token + '=' * (-len(unpadded_base64_token) % 4)
                try:
                    token = base64.decodestring(base64_token).decode('utf-8')
                except Exception:
                    pass
        _self = request.handler.callable.__self__
        if request.remote.ip != '127.0.0.1':
            check_token = _self._user_controller.check_token if hasattr(_self, '_user_controller') else _self.webinterface.check_token
            if not check_token(token):
                raise RuntimeError()
        if pass_token is True:
            request.params['token'] = token
    except Exception:
        cherrypy.response.headers['Content-Type'] = 'application/json'
        cherrypy.response.status = 401  # Unauthorized
        cherrypy.response.body = '"invalid_token"'
        request.handler = None


cherrypy.tools.timestamp_filter = cherrypy.Tool('before_handler', timestamp_handler)
cherrypy.tools.cors = cherrypy.Tool('before_handler', cors_handler, priority=10)
cherrypy.tools.authenticated = cherrypy.Tool('before_handler', authentication_handler)
cherrypy.tools.params = cherrypy.Tool('before_handler', params_handler)


@decorator
def _openmotics_api(f, *args, **kwargs):
    start = time.time()
    timings = {}
    status = 200  # OK
    try:
        return_data = f(*args, **kwargs)
        data = limit_floats(dict({'success': True}.items() + return_data.items()))
    except cherrypy.HTTPError as ex:
        status = ex.status
        data = {'success': False, 'msg': ex._message}
    except (InMaintenanceModeException, InAddressModeException):
        status = 503  # Service Unavailable
        data = {'success': False, 'msg': 'maintenance_mode'}
    except CommunicationTimedOutException:
        logger.error('Communication timeout during API call %s', f.__name__)
        status = 200  # OK
        data = {'success': False, 'msg': 'Internal communication timeout'}
    except Exception as ex:
        logger.exception('Unexpected error during API call %s', f.__name__)
        status = 200  # OK
        data = {'success': False, 'msg': str(ex)}
    timings['process'] = ('Processing', time.time() - start)
    serialization_start = time.time()
    contents = json.dumps(data)
    timings['serialization'] = 'Serialization', time.time() - serialization_start
    cherrypy.response.headers['Content-Type'] = 'application/json'
    cherrypy.response.headers['Server-Timing'] = ','.join(['{0}={1}; "{2}"'.format(key, value[1] * 1000, value[0])
                                                           for key, value in timings.iteritems()])
    if hasattr(f, 'deprecated') and f.deprecated is not None:
        cherrypy.response.headers['Warning'] = 'Warning: 299 - "Deprecated, replaced by: {0}"'.format(f.deprecated)
    cherrypy.response.status = status
    return contents


def openmotics_api(auth=False, check=None, pass_token=False, plugin_exposed=True, deprecated=None):
    def wrapper(func):
        func.deprecated = deprecated
        func = _openmotics_api(func)
        if auth is True:
            func = cherrypy.tools.authenticated(pass_token=pass_token)(func)
        if check is not None:
            func = cherrypy.tools.params(**check)(func)
        func.exposed = True
        func.plugin_exposed = plugin_exposed
        func.check = check
        return func
    return wrapper


def types(**kwargs):
    return kwargs


@Injectable.named('web_interface')
@Singleton
class WebInterface(object):
    """ This class defines the web interface served by cherrypy. """

    @Inject
    def __init__(self, user_controller=INJECTED, gateway_api=INJECTED, maintenance_controller=INJECTED,
                 message_client=INJECTED, configuration_controller=INJECTED, scheduling_controller=INJECTED,
                 thermostat_controller=INJECTED, light_controller=INJECTED):
        """
        Constructor for the WebInterface.

        :type user_controller: gateway.users.UserController
        :type gateway_api: gateway.gateway_api.GatewayApi
        :type maintenance_controller: gateway.hal.maintenance_controller.MaintenanceController
        :type message_client: bus.om_bus_client.MessageClient
        :type configuration_controller: gateway.config.ConfigController
        :type scheduling_controller: gateway.scheduling.SchedulingController
        :type thermostat_controller: gateway.thermostat.thermostat_controller.ThermostatController
        :type light_controller: gateway.controller.lights.LightController
        """
        self._user_controller = user_controller
        self._config_controller = configuration_controller
        self._scheduling_controller = scheduling_controller
        self._thermostat_controller = thermostat_controller
        self._light_controller = light_controller
        self._plugin_controller = None

        self._gateway_api = gateway_api
        self._maintenance_controller = maintenance_controller
        self._message_client = message_client
        self._plugin_controller = None
        self._metrics_collector = None
        self._metrics_controller = None

        self._ws_metrics_registered = False
        self._power_dirty = False

    def in_authorized_mode(self):
        return self._message_client.get_state('led_service', {}).get('authorized_mode', False)

    def distribute_metric(self, metric):
        try:
            answers = cherrypy.engine.publish('get-metrics-receivers')
            if not answers:
                return
            receivers = answers.pop()
            for client_id in receivers.keys():
                receiver_info = receivers.get(client_id)
                if receiver_info is None:
                    continue
                try:
                    if cherrypy.request.remote.ip != '127.0.0.1' and not self._user_controller.check_token(receiver_info['token']):
                        raise cherrypy.HTTPError(401, 'invalid_token')
                    sources = self._metrics_controller.get_filter('source', receiver_info['source'])
                    metric_types = self._metrics_controller.get_filter('metric_type', receiver_info['metric_type'])
                    if metric['source'] in sources and metric['type'] in metric_types:
                        receiver_info['socket'].send(msgpack.dumps(metric), binary=True)
                except cherrypy.HTTPError as ex:  # As might be caught from the `check_token` function
                    receiver_info['socket'].close(ex.code, ex.message)
                except Exception as ex:
                    logger.error('Failed to distribute metrics to WebSocket: %s', ex)
                    cherrypy.engine.publish('remove-metrics-receiver', client_id)
        except Exception as ex:
            logger.error('Failed to distribute metrics to WebSockets: %s', ex)

    def send_event_websocket(self, event):
        try:
            answers = cherrypy.engine.publish('get-events-receivers')
            if not answers:
                return
            receivers = answers.pop()
            for client_id in receivers.keys():
                receiver_info = receivers.get(client_id)
                if receiver_info is None:
                    continue
                try:
                    if event.type not in receiver_info['subscribed_types']:
                        continue
                    if cherrypy.request.remote.ip != '127.0.0.1' and not self._user_controller.check_token(receiver_info['token']):
                        raise cherrypy.HTTPError(401, 'invalid_token')
                    receiver_info['socket'].send(msgpack.dumps(event.serialize()), binary=True)
                except cherrypy.HTTPError as ex:  # As might be caught from the `check_token` function
                    receiver_info['socket'].close(ex.code, ex.message)
                except Exception as ex:
                    logger.error('Failed to distribute events to WebSocket: %s', ex)
                    cherrypy.engine.publish('remove-events-receiver', client_id)
        except Exception as ex:
            logger.error('Failed to distribute events to WebSockets: %s', ex)

    def set_plugin_controller(self, plugin_controller):
        """
        Set the plugin controller.

        :type plugin_controller: plugins.base.PluginController
        """
        self._plugin_controller = plugin_controller

    def set_metrics_collector(self, metrics_collector):
        """ Set the metrics collector """
        self._metrics_collector = metrics_collector

    def set_metrics_controller(self, metrics_controller):
        """ Sets the metrics controller """
        self._metrics_controller = metrics_controller

    @cherrypy.expose
    def index(self):
        """
        Index page of the web service (Gateway GUI)
        :returns: Contents of index.html
        :rtype: str
        """
        return serve_file('/opt/openmotics/static/index.html', content_type='text/html')

    @openmotics_api(check=types(accept_terms=bool, timeout=int), plugin_exposed=False)
    def login(self, username, password, accept_terms=None, timeout=None):
        """
        Login to the web service, returns a token if successful, returns HTTP status code 401 otherwise.

        :param username: Name of the user.
        :type username: str
        :param password: Password of the user.
        :type password: str
        :param accept_terms: True if the terms are accepted
        :type accept_terms: bool | None
        :param timeout: Optional session timeout. 30d >= x >= 1h
        :type timeout: int
        :returns: Authentication token
        :rtype: str
        """

        success, data = self._user_controller.login(username, password, accept_terms, timeout)
        if success is True:
            return {'token': data}
        if data == 'terms_not_accepted':
            return {'next_step': 'accept_terms'}
        raise cherrypy.HTTPError(401, "invalid_credentials")

    @openmotics_api(auth=True, pass_token=True, plugin_exposed=False)
    def logout(self, token):
        """
        Logout from the web service.

        :returns: 'status': 'OK'
        :rtype: str
        """
        self._user_controller.logout(token)
        return {'status': 'OK'}

    @openmotics_api(plugin_exposed=False)
    def create_user(self, username, password):
        """
        Create a new user using a username and a password. Only possible in authorized mode.

        :param username: Name of the user.
        :type username: str
        :param password: Password of the user.
        :type password: str
        """
        if not self.in_authorized_mode():
            raise cherrypy.HTTPError(401, "unauthorized")
        self._user_controller.create_user(username, password, 'admin', True)
        return {}

    @openmotics_api(plugin_exposed=False)
    def get_usernames(self):
        """
        Get the names of the users on the gateway. Only possible in authorized mode.

        :returns: 'usernames': list of usernames (String).
        :rtype: dict
        """
        if not self.in_authorized_mode():
            raise cherrypy.HTTPError(401, "unauthorized")
        return {'usernames': self._user_controller.get_usernames()}

    @openmotics_api(plugin_exposed=False)
    def remove_user(self, username):
        """
        Remove a user. Only possible in authorized mode.

        :param username: Name of the user to remove.
        :type username: str
        """
        if not self.in_authorized_mode():
            raise cherrypy.HTTPError(401, "unauthorized")
        self._user_controller.remove_user(username)
        return {}

    @openmotics_api(auth=True, plugin_exposed=False)
    def open_maintenance(self):
        """
        Open maintenance mode, return the port of the maintenance socket.

        :returns: 'port': Port on which the maintenance ssl socket is listening (Integer between 6000 and 7000).
        :rtype: dict
        """
        port = self._maintenance_controller.open_maintenace_socket()
        return {'port': port}

    @openmotics_api(auth=True)
    def reset_master(self):
        """
        Perform a cold reset on the master.

        :returns: 'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.reset_master()

    @openmotics_api(auth=True)
    def module_discover_start(self):
        """
        Start the module discover mode on the master.

        :returns: 'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.module_discover_start()

    @openmotics_api(auth=True)
    def module_discover_stop(self):
        """
        Stop the module discover mode on the master.

        :returns: 'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.module_discover_stop()

    @openmotics_api(auth=True)
    def module_discover_status(self):
        """
        Gets the status of the module discover mode on the master.

        :returns 'running': true|false
        :rtype: dict
        """
        return self._gateway_api.module_discover_status()

    @openmotics_api(auth=True)
    def get_module_log(self):
        """
        Get the log messages from the module discovery mode. This returns the current log
        messages and clear the log messages.

        :returns: 'log': list of tuples (log_level, message).
        :rtype: dict
        """
        return self._gateway_api.get_module_log()

    @openmotics_api(auth=True)
    def get_modules(self):
        """
        Get a list of all modules attached and registered with the master.

        :returns: Dict with:
        * 'outputs' (list of module types: O,R,D),
        * 'inputs' (list of input module types: I,T,L,C)
        * 'shutters' (List of modules types: S).
        :rtype: dict
        """
        return self._gateway_api.get_modules()

    @openmotics_api(auth=True)
    def get_modules_information(self):
        """
        Gets an overview of all modules and information
        :return: Dict containing information per address
        """
        return {'modules': self._gateway_api.get_modules_information()}

    @openmotics_api(auth=True)
    def get_features(self):
        """
        Returns all available features this Gateway supports. This allows to make flexible clients
        """
        features = [
            'metrics',  # Advanced metrics (including metrics over websockets)
            'dirty_flag',  # A dirty flag that can be used to trigger syncs on power & master
            'scheduling',  # Gateway backed scheduling
            'factory_reset',  # The gateway can be complete reset to factory standard
            'isolated_plugins',  # Plugins run in a separate process, so allow fine-graded control
            'websocket_maintenance',  # Maintenance over websockets
        ]

        master_version = self._gateway_api.get_master_version()
        if master_version >= (3, 143, 77):
            features.append('default_timer_disabled')
        if master_version >= (3, 143, 79):
            features.append('100_steps_dimmer')
        if master_version >= (3, 143, 88):
            features.append('input_states')

        thermostats_gateway = Feature.get_or_none(name='thermostats_gateway')
        if thermostats_gateway is not None and thermostats_gateway.enabled:
            features.append('thermostats_gateway')

        return {'features': features}

    @openmotics_api(auth=True, check=types(type=int, id=int))
    def flash_leds(self, type, id):
        """
        Flash the leds on the module for an output/input/sensor.

        :param type: The module type: output/dimmer (0), input (1), sensor/temperatur (2).
        :type type: int
        :param id: The id of the output/input/sensor.
        :type id: int
        :returns: 'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.flash_leds(type, id)

    @openmotics_api(auth=True)
    def get_status(self):
        """
        Get the status of the master.

        :returns: 'time': hour and minutes (HH:MM), 'date': day, month, year (DD:MM:YYYY), \
            'mode': Integer, 'version': a.b.c and 'hw_version': hardware version (Integer).
        :rtype: dict
        """
        return self._gateway_api.get_status()

    @openmotics_api(auth=True)
    def get_input_status(self):
        """
        Get the status of the inputs.

        :returns: 'status': list of dictionaries with the following keys: id, status.
        """
        return {'status': self._gateway_api.get_input_status()}

    @openmotics_api(auth=True)
    def get_output_status(self):
        """
        Get the status of the outputs.

        :returns: 'status': list of dictionaries with the following keys: id, status, dimmer and ctimer.
        """
        return {'status': self._gateway_api.get_outputs_status()}

    @openmotics_api(auth=True, check=types(id=int, is_on=bool, dimmer=int, timer=int))
    def set_output(self, id, is_on, dimmer=None, timer=None):
        """
        Set the status, dimmer and timer of an output.

        :param id: The id of the output to set
        :type id: int
        :param is_on: Whether the output should be on
        :type is_on: bool
        :param dimmer: The dimmer value to set, None if unchanged
        :type dimmer: int or None
        :param timer: The timer value to set, None if unchanged
        :type timer: int
        """
        return self._gateway_api.set_output_status(id, is_on, dimmer, timer)

    @openmotics_api(auth=True)
    def set_all_lights_off(self):
        """
        Turn all lights off.
        """
        return self._gateway_api.set_all_lights_off()

    @openmotics_api(auth=True, check=types(floor=int))
    def set_all_lights_floor_off(self, floor):
        """
        Turn all lights on a given floor off.

        :param floor: The id of the floor
        :type floor: int
        """
        return self._gateway_api.set_all_lights_floor_off(floor)

    @openmotics_api(auth=True, check=types(floor=int))
    def set_all_lights_floor_on(self, floor):
        """
        Turn all lights on a given floor on.

        :param floor: The id of the floor
        :type floor: int
        """
        return self._gateway_api.set_all_lights_floor_on(floor)

    @openmotics_api(auth=True)
    def get_last_inputs(self):
        """
        Get the 5 last pressed inputs during the last 5 minutes.

        :returns: 'inputs': list of tuples (input, output).
        :rtype: dict
        """
        # for backwards compatibility reasons a list of input, output tuples is returned
        inputs = [(changed_input, None) for changed_input in self._gateway_api.get_last_inputs()]
        return {'inputs': inputs}

    @openmotics_api(auth=True)
    def get_shutter_status(self):
        """
        Get the status of the shutters.

        :returns: 'status': list of dictionaries with the following keys: id, position.
        :rtype: dict
        """
        return self._gateway_api.get_shutter_status()

    @openmotics_api(auth=True, check=types(id=int, position=int))
    def do_shutter_down(self, id, position=None):
        """
        Make a shutter go down. The shutter stops automatically when the down or specified position is reached

        :param id: The id of the shutter.
        :type id: int
        :param position: The desired end position
        :type position: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_down(id, position)

    @openmotics_api(auth=True, check=types(id=int, position=int))
    def do_shutter_up(self, id, position=None):
        """
        Make a shutter go up. The shutter stops automatically when the up or specified position is reached

        :param id: The id of the shutter.
        :type id: int
        :param position: The desired end position
        :type position: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_up(id, position)

    @openmotics_api(auth=True, check=types(id=int))
    def do_shutter_stop(self, id):
        """
        Make a shutter stop.

        :param id: The id of the shutter.
        :type id: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_stop(id)

    @openmotics_api(auth=True, check=types(id=int, position=int))
    def do_shutter_goto(self, id, position):
        """
        Make a shutter go up or down to the specified position.

        :param id: The id of the shutter.
        :type id: int
        :param position: The desired end position
        :type position: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_goto(id, position)

    @openmotics_api(auth=True, check=types(id=int, position=int, direction=[ShutterController.Direction.UP, ShutterController.Direction.DOWN, ShutterController.Direction.STOP]))
    def shutter_report_position(self, id, position, direction=None):
        """
        Reports the actual position of a shutter

        :param id: The id of the shutter.
        :type id: int
        :param position: The actual position
        :type position: int
        :param direction: The direction
        :type direction: str
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.shutter_report_position(id, position, direction)

    @openmotics_api(auth=True, check=types(id=int))
    def do_shutter_group_down(self, id):
        """
        Make a shutter group go down. The shutters stop automatically when the down position is
        reached (after the predefined number of seconds).

        :param id: The id of the shutter group.
        :type id: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_group_down(id)

    @openmotics_api(auth=True, check=types(id=int))
    def do_shutter_group_up(self, id):
        """
        Make a shutter group go up. The shutters stop automatically when the up position is
        reached (after the predefined number of seconds).

        :param id: The id of the shutter group.
        :type id: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_group_up(id)

    @openmotics_api(auth=True, check=types(id=int))
    def do_shutter_group_stop(self, id):
        """
        Make a shutter group stop.

        :param id: The id of the shutter group.
        :type id: int
        :returns:'status': 'OK'.
        :rtype: dict
        """
        return self._gateway_api.do_shutter_group_stop(id)

    @openmotics_api(auth=True)
    def get_thermostat_status(self):
        """
        Get the status of the thermostats.

        :returns: global status information about the thermostats: 'thermostats_on', \
            'automatic' and 'setpoint' and 'status': a list with status information for all \
            thermostats, each element in the list is a dict with the following keys: \
            'id', 'act', 'csetp', 'output0', 'output1', 'outside', 'mode'.
        :rtype: dict
        """
        return self._thermostat_controller.v0_get_thermostat_status()

    @openmotics_api(auth=True, check=types(thermostat=int, temperature=float))
    def set_current_setpoint(self, thermostat, temperature):
        """
        Set the current setpoint of a thermostat.

        :param thermostat: The id of the thermostat to set
        :type thermostat: int
        :param temperature: The temperature to set in degrees Celcius
        :type temperature: float
        :return: 'status': 'OK'.
        :rtype: dict
        """
        return self._thermostat_controller.v0_set_current_setpoint(thermostat, temperature)

    @openmotics_api(auth=True, check=types(thermostat_on=bool, automatic=bool, setpoint=int, cooling_mode=bool, cooling_on=bool))
    def set_thermostat_mode(self, thermostat_on, automatic=None, setpoint=None, cooling_mode=False, cooling_on=False):
        """
        Set the global mode of the thermostats. Thermostats can be on or off (thermostat_on),
        can be in cooling or heating (cooling_mode), cooling can be turned on or off (cooling_on).
        The automatic and setpoint parameters are here for backwards compatibility and will be
        applied to all thermostats. To control the automatic and setpoint parameters per thermostat
        use the v0_set_per_thermostat_mode call instead.

        :param thermostat_on: Whether the thermostats are on
        :type thermostat_on: bool
        :param automatic: Automatic mode (True) or Manual mode (False).  This parameter is here for
            backwards compatibility, use v0_set_per_thermostat_mode instead.
        :type automatic: bool or None
        :param setpoint: The current setpoint.  This parameter is here for backwards compatibility,
            use v0_set_per_thermostat_mode instead.
        :type setpoint: int or None
        :param cooling_mode: Cooling mode (True) of Heating mode (False)
        :type cooling_mode: bool or None
        :param cooling_on: Turns cooling ON when set to true.
        :param cooling_on: bool or None
        :return: 'status': 'OK'.
        :rtype: dict
        """
        self._thermostat_controller.v0_set_thermostat_mode(thermostat_on, cooling_mode, cooling_on, automatic, setpoint)

        return {'status': 'OK'}

    @openmotics_api(auth=True, check=types(thermostat_id=int, automatic=bool, setpoint=int))
    def set_per_thermostat_mode(self, thermostat_id, automatic, setpoint):
        """
        Set the thermostat mode of a given thermostat. Thermostats can be set to automatic or
        manual, in case of manual a setpoint (0 to 5) can be provided.

        :param thermostat_id: The thermostat id
        :type thermostat_id: int
        :param automatic: Automatic mode (True) or Manual mode (False).
        :type automatic: bool
        :param setpoint: The current setpoint.
        :type setpoint: int
        """
        return self._thermostat_controller.v0_set_per_thermostat_mode(thermostat_id, automatic, setpoint)

    @openmotics_api(auth=True)
    def get_airco_status(self):
        """
        Get the mode of the airco attached to a all thermostats.

        :returns: dict with ASB0-ASB31.
        :rtype: dict
        """
        return self._thermostat_controller.v0_get_airco_status()

    @openmotics_api(auth=True, check=types(thermostat_id=int, airco_on=bool))
    def set_airco_status(self, thermostat_id, airco_on):
        """
        Set the mode of the airco attached to a given thermostat.

        :param thermostat_id: The thermostat id.
        :type thermostat_id: int
        :param airco_on: Turns the airco on if True.
        :type airco_on: bool
        :returns: dict with 'status'
        :rtype: dict
        """
        return self._thermostat_controller.v0_set_airco_status(thermostat_id, airco_on)

    @openmotics_api(auth=True)
    def get_sensor_temperature_status(self):
        """
        Get the current temperature of all sensors.

        :returns: 'status': list of 32 temperatures, 1 for each sensor.
        :rtype: dict
        """
        return {'status': self._gateway_api.get_sensors_temperature_status()}

    @openmotics_api(auth=True)
    def get_sensor_humidity_status(self):
        """
        Get the current humidity of all sensors.

        :returns: 'status': List of 32 bytes, 1 for each sensor.
        :rtype: dict
        """
        return {'status': self._gateway_api.get_sensors_humidity_status()}

    @openmotics_api(auth=True)
    def get_sensor_brightness_status(self):
        """
        Get the current brightness of all sensors.

        :returns: 'status': List of 32 bytes, 1 for each sensor.
        :rtype: dict
        """
        return {'status': self._gateway_api.get_sensors_brightness_status()}

    @openmotics_api(auth=True, check=types(sensor_id=int, temperature=float, humidity=float, brightness=int))
    def set_virtual_sensor(self, sensor_id, temperature, humidity, brightness):
        """
        Set the temperature, humidity and brightness value of a virtual sensor.

        :param sensor_id: The id of the sensor.
        :type sensor_id: int
        :param temperature: The temperature to set in degrees Celcius
        :type temperature: float
        :param humidity: The humidity to set in percentage
        :type humidity: float
        :param brightness: The brightness to set in percentage
        :type brightness: int
        :returns: dict with 'status'.
        :rtype: dict
        """
        return self._gateway_api.set_virtual_sensor(sensor_id, temperature, humidity, brightness)

    @openmotics_api(auth=True, check=types(action_type=int, action_number=int))
    def do_basic_action(self, action_type, action_number):
        """
        Execute a basic action.

        :param action_type: The type of the action as defined by the master api.
        :type action_type: int
        :param action_number: The number provided to the basic action, its meaning depends on the action_type.
        :type action_number: int
        """
        return self._gateway_api.do_basic_action(action_type, action_number)

    @openmotics_api(auth=True, check=types(group_action_id=int))
    def do_group_action(self, group_action_id):
        """
        Execute a group action.

        :param group_action_id: The id of the group action
        :type group_action_id: int
        """
        return self._gateway_api.do_group_action(group_action_id)

    @openmotics_api(auth=True, check=types(status=bool))
    def set_master_status_leds(self, status):
        """
        Set the status of the leds on the master.

        :param status: whether the leds should be on (true) or off (false).
        :type status: bool
        """
        return self._gateway_api.set_master_status_leds(status)

    @cherrypy.expose
    @cherrypy.tools.authenticated()
    def get_full_backup(self):
        """
        Get a backup (tar) of the master eeprom and the sqlite databases.

        :returns: Tar containing 4 files: master.eep, config.db, scheduled.db, power.db and
            eeprom_extensions.db as a string of bytes.
        :rtype: dict
        """
        cherrypy.response.headers['Content-Type'] = 'application/octet-stream'
        return self._gateway_api.get_full_backup()

    @openmotics_api(auth=True, plugin_exposed=False)
    def restore_full_backup(self, backup_data):
        """
        Restore a full backup containing the master eeprom and the sqlite databases.

        :param backup_data: The full backup to restore: tar containing 4 files: master.eep, config.db, \
            scheduled.db, power.db and eeprom_extensions.db as a string of bytes.
        :type backup_data: multipart/form-data encoded bytes.
        :returns: dict with 'output' key.
        :rtype: dict
        """
        data = backup_data.file.read()
        if not data:
            raise RuntimeError('backup_data is empty')
        return self._gateway_api.restore_full_backup(data)

    @cherrypy.expose
    @cherrypy.tools.authenticated()
    def get_master_backup(self):
        """
        Get a backup of the eeprom of the master.

        :returns: This function does not return a dict, unlike all other API functions: it \
            returns a string of bytes (size = 64kb).
        :rtype: bytearray
        """
        cherrypy.response.headers['Content-Type'] = 'application/octet-stream'
        return self._gateway_api.get_master_backup()

    @openmotics_api(auth=True)
    def master_restore(self, data):
        """
        Restore a backup of the eeprom of the master.

        :param data: The eeprom backup to restore.
        :type data: multipart/form-data encoded bytes (size = 64 kb).
        :returns: 'output': array with the addresses that were written.
        :rtype: dict
        """
        data = data.file.read()
        return self._gateway_api.master_restore(data)

    @openmotics_api(auth=True)
    def get_errors(self):
        """
        Get the number of seconds since the last successul communication with the master and
        power modules (master_last_success, power_last_success) and the error list per module
        (input and output modules). The modules are identified by O1, O2, I1, I2, ...

        :returns: 'errors': list of tuples (module, nr_errors), 'master_last_success': UNIX \
            timestamp of the last succesful master communication and 'power_last_success': UNIX \
            timestamp of the last successful power communication.
        :rtype: dict
        """
        try:
            errors = self._gateway_api.master_error_list()
        except Exception:
            # In case of communications problems with the master.
            errors = []

        master_last = self._gateway_api.master_last_success()
        power_last = self._gateway_api.power_last_success()

        return {'errors': errors,
                'master_last_success': master_last,
                'power_last_success': power_last}

    @openmotics_api(auth=True)
    def master_clear_error_list(self):
        """
        Clear the number of errors.
        """
        return self._gateway_api.master_clear_error_list

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_output_configuration(self, id, fields=None):
        """
        Get a specific output_configuration defined by its id.

        :param id: The id of the output_configuration
        :type id: int
        :param fields: The fields of the output_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': output_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'floor' (Byte), 'module_type' (String[1]), 'name' (String[16]), 'room' (Byte), 'timer' (Word), 'type' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_output_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_output_configurations(self, fields=None):
        """
        Get all output_configurations.

        :param fields: The field of the output_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of output_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'floor' (Byte), 'module_type' (String[1]), 'name' (String[16]), 'room' (Byte), 'timer' (Word), 'type' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_output_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_output_configuration(self, config):
        """
        Set one output_configuration.

        :param config: The output_configuration to set: dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'floor' (Byte), 'name' (String[16]), 'room' (Byte), 'timer' (Word), 'type' (Byte)
        :type config: dict
        """
        self._gateway_api.set_output_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_output_configurations(self, config):
        """
        Set multiple output_configurations.

        :param config: The list of output_configurations to set: list of output_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'floor' (Byte), 'name' (String[16]), 'room' (Byte), 'timer' (Word), 'type' (Byte)
        :type config: list
        """
        self._gateway_api.set_output_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_shutter_configuration(self, id, fields=None):
        """
        Get a specific shutter_configuration defined by its id.

        :param id: The id of the shutter_configuration
        :type id: Id
        :param fields: The fields of the shutter_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': shutter_configuration dict: contains 'id' (Id), 'group_1' (Byte), 'group_2' (Byte), 'name' (String[16]), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte), 'up_down_config' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_shutter_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_shutter_configurations(self, fields=None):
        """
        Get all shutter_configurations.

        :param fields: The fields of the shutter_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of shutter_configuration dict: contains 'id' (Id), 'group_1' (Byte), 'group_2' (Byte), 'name' (String[16]), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte), 'up_down_config' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_shutter_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_shutter_configuration(self, config):
        """
        Set one shutter_configuration.

        :param config: The shutter_configuration to set: shutter_configuration dict: contains 'id' (Id), 'group_1' (Byte), 'group_2' (Byte), 'name' (String[16]), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte), 'up_down_config' (Byte)
        :type config: dict
        """
        self._gateway_api.set_shutter_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_shutter_configurations(self, config):
        """
        Set multiple shutter_configurations.

        :param config: The list of shutter_configurations to set: list of shutter_configuration dict: contains 'id' (Id), 'group_1' (Byte), 'group_2' (Byte), 'name' (String[16]), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte), 'up_down_config' (Byte)
        :type config: list
        """
        self._gateway_api.set_shutter_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_shutter_group_configuration(self, id, fields=None):
        """
        Get a specific shutter_group_configuration defined by its id.

        :param id: The id of the shutter_group_configuration
        :type id: int
        :param fields: The field of the shutter_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': shutter_group_configuration dict: contains 'id' (Id), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_shutter_group_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_shutter_group_configurations(self, fields=None):
        """
        Get all shutter_group_configurations.

        :param fields: The field of the shutter_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of shutter_group_configuration dict: contains 'id' (Id), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_shutter_group_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_shutter_group_configuration(self, config):
        """
        Set one shutter_group_configuration.

        :param config: The shutter_group_configuration to set: shutter_group_configuration dict: contains 'id' (Id), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte)
        :type config: dict
        """
        self._gateway_api.set_shutter_group_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_shutter_group_configurations(self, config):
        """
        Set multiple shutter_group_configurations.

        :param config: The list of shutter_group_configurations to set: list of shutter_group_configuration dict: contains 'id' (Id), 'room' (Byte), 'timer_down' (Byte), 'timer_up' (Byte)
        :type config: list
        """
        self._gateway_api.set_shutter_group_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_input_configuration(self, id, fields=None):
        """
        Get a specific input_configuration defined by its id.

        :param id: The id of the input_configuration
        :type id: int
        :param fields: The field of the input_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': input_configuration dict: contains 'id' (Id), 'action' (Byte), 'basic_actions' (Actions[15]), 'invert' (Byte), 'module_type' (String[1]), 'name' (String[8]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_input_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_input_configurations(self, fields=None):
        """
        Get all input_configurations.

        :param fields: The field of the input_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of input_configuration dict: contains 'id' (Id), 'action' (Byte), 'basic_actions' (Actions[15]), 'invert' (Byte), 'module_type' (String[1]), 'name' (String[8]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_input_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_input_configuration(self, config):
        """
        Set one input_configuration.

        :param config: The input_configuration to set: input_configuration dict: contains 'id' (Id), 'action' (Byte), 'basic_actions' (Actions[15]), 'invert' (Byte), 'name' (String[8]), 'room' (Byte)
        :type config: dict
        """
        self._gateway_api.set_input_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_input_configurations(self, config):
        """
        Set multiple input_configurations.

        :param config: The list of input_configurations to set: list of input_configuration dict: contains 'id' (Id), 'action' (Byte), 'basic_actions' (Actions[15]), 'invert' (Byte), 'name' (String[8]), 'room' (Byte)
        :type config: list
        """
        self._gateway_api.set_input_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_thermostat_configuration(self, id, fields=None):
        """
        Get a specific thermostat_configuration defined by its id.

        :param id: The id of the thermostat_configuration
        :type id: int
        :param fields: The field of the thermostat_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': thermostat_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_thermostat_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_thermostat_configurations(self, fields=None):
        """
        Get all thermostat_configurations.

        :param fields: The field of the thermostat_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of thermostat_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_thermostat_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_thermostat_configuration(self, config):
        """
        Set one thermostat_configuration.

        :param config: The thermostat_configuration to set: thermostat_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :type config: dict
        """
        self._thermostat_controller.v0_set_thermostat_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_thermostat_configurations(self, config):
        """
        Set multiple thermostat_configurations.

        :param config: The list of thermostat_configurations to set: list of thermostat_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :type config: list
        """
        self._thermostat_controller.v0_set_thermostat_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_sensor_configuration(self, id, fields=None):
        """
        Get a specific sensor_configuration defined by its id.

        :param id: The id of the sensor_configuration
        :type id: int
        :param fields: The field of the sensor_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': sensor_configuration dict: contains 'id' (Id), 'name' (String[16]), 'offset' (SignedTemp(-7.5 to 7.5 degrees)), 'room' (Byte), 'virtual' (Boolean)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_sensor_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_sensor_configurations(self, fields=None):
        """
        Get all sensor_configurations.

        :param fields: The field of the sensor_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of sensor_configuration dict: contains 'id' (Id), 'name' (String[16]), 'offset' (SignedTemp(-7.5 to 7.5 degrees)), 'room' (Byte), 'virtual' (Boolean)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_sensor_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_sensor_configuration(self, config):
        """
        Set one sensor_configuration.

        :param config: The sensor_configuration to set: sensor_configuration dict: contains 'id' (Id), 'name' (String[16]), 'offset' (SignedTemp(-7.5 to 7.5 degrees)), 'room' (Byte), 'virtual' (Boolean)
        :type config: dict
        """
        self._gateway_api.set_sensor_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_sensor_configurations(self, config):
        """
        Set multiple sensor_configurations.

        :param config: The list of sensor_configurations to set: list of sensor_configuration dict: contains 'id' (Id), 'name' (String[16]), 'offset' (SignedTemp(-7.5 to 7.5 degrees)), 'room' (Byte), 'virtual' (Boolean)
        :type config: list
        """
        self._gateway_api.set_sensor_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_pump_group_configuration(self, id, fields=None):
        """
        Get a specific pump_group_configuration defined by its id.

        :param id: The id of the pump_group_configuration
        :type id: int
        :param fields: The field of the pump_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_pump_group_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_pump_group_configurations(self, fields=None):
        """
        Get all pump_group_configurations.

        :param fields: The field of the pump_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_pump_group_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_pump_group_configuration(self, config):
        """
        Set one pump_group_configuration.

        :param config: The pump_group_configuration to set: pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :type config: dict
        """
        self._thermostat_controller.v0_set_pump_group_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_pump_group_configurations(self, config):
        """
        Set multiple pump_group_configurations.

        :param config: The list of pump_group_configurations to set: list of pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :type config: list
        """
        self._thermostat_controller.v0_set_pump_group_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_cooling_configuration(self, id, fields=None):
        """
        Get a specific cooling_configuration defined by its id.

        :param id: The id of the cooling_configuration
        :type id: int
        :param fields: The field of the cooling_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': cooling_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_cooling_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_cooling_configurations(self, fields=None):
        """
        Get all cooling_configurations.

        :param fields: The field of the cooling_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of cooling_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_cooling_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_cooling_configuration(self, config):
        """
        Set one cooling_configuration.

        :param config: The cooling_configuration to set: cooling_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :type config: dict
        """
        self._thermostat_controller.v0_set_cooling_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_cooling_configurations(self, config):
        """
        Set multiple cooling_configurations.

        :param config: The list of cooling_configurations to set: list of cooling_configuration dict: contains 'id' (Id), 'auto_fri' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_mon' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sat' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_sun' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_thu' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_tue' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'auto_wed' ([temp_n(Temp),start_d1(Time),stop_d1(Time),temp_d1(Temp),start_d2(Time),stop_d2(Time),temp_d2(Temp)]), 'name' (String[16]), 'output0' (Byte), 'output1' (Byte), 'permanent_manual' (Boolean), 'pid_d' (Byte), 'pid_i' (Byte), 'pid_int' (Byte), 'pid_p' (Byte), 'room' (Byte), 'sensor' (Byte), 'setp0' (Temp), 'setp1' (Temp), 'setp2' (Temp), 'setp3' (Temp), 'setp4' (Temp), 'setp5' (Temp)
        :type config: list
        """
        self._thermostat_controller.v0_set_cooling_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_cooling_pump_group_configuration(self, id, fields=None):
        """
        Get a specific cooling_pump_group_configuration defined by its id.

        :param id: The id of the cooling_pump_group_configuration
        :type id: int
        :param fields: The field of the cooling_pump_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_cooling_pump_group_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_cooling_pump_group_configurations(self, fields=None):
        """
        Get all cooling_pump_group_configurations.

        :param fields: The field of the cooling_pump_group_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_cooling_pump_group_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_cooling_pump_group_configuration(self, config):
        """
        Set one cooling_pump_group_configuration.

        :param config: The cooling_pump_group_configuration to set: cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :type config: dict
        """
        self._thermostat_controller.v0_set_cooling_pump_group_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_cooling_pump_group_configurations(self, config):
        """
        Set multiple cooling_pump_group_configurations.

        :param config: The list of cooling_pump_group_configurations to set: list of cooling_pump_group_configuration dict: contains 'id' (Id), 'outputs' (CSV[32]), 'room' (Byte)
        :type config: list
        """
        self._thermostat_controller.v0_set_cooling_pump_group_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_global_rtd10_configuration(self, fields=None):
        """
        Get the global_rtd10_configuration.

        :param fields: The field of the global_rtd10_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': global_rtd10_configuration dict: contains 'output_value_cooling_16' (Byte), 'output_value_cooling_16_5' (Byte), 'output_value_cooling_17' (Byte), 'output_value_cooling_17_5' (Byte), 'output_value_cooling_18' (Byte), 'output_value_cooling_18_5' (Byte), 'output_value_cooling_19' (Byte), 'output_value_cooling_19_5' (Byte), 'output_value_cooling_20' (Byte), 'output_value_cooling_20_5' (Byte), 'output_value_cooling_21' (Byte), 'output_value_cooling_21_5' (Byte), 'output_value_cooling_22' (Byte), 'output_value_cooling_22_5' (Byte), 'output_value_cooling_23' (Byte), 'output_value_cooling_23_5' (Byte), 'output_value_cooling_24' (Byte), 'output_value_heating_16' (Byte), 'output_value_heating_16_5' (Byte), 'output_value_heating_17' (Byte), 'output_value_heating_17_5' (Byte), 'output_value_heating_18' (Byte), 'output_value_heating_18_5' (Byte), 'output_value_heating_19' (Byte), 'output_value_heating_19_5' (Byte), 'output_value_heating_20' (Byte), 'output_value_heating_20_5' (Byte), 'output_value_heating_21' (Byte), 'output_value_heating_21_5' (Byte), 'output_value_heating_22' (Byte), 'output_value_heating_22_5' (Byte), 'output_value_heating_23' (Byte), 'output_value_heating_23_5' (Byte), 'output_value_heating_24' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_global_rtd10_configuration(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_global_rtd10_configuration(self, config):
        """
        Set the global_rtd10_configuration.

        :param config: The global_rtd10_configuration to set: global_rtd10_configuration dict: contains 'output_value_cooling_16' (Byte), 'output_value_cooling_16_5' (Byte), 'output_value_cooling_17' (Byte), 'output_value_cooling_17_5' (Byte), 'output_value_cooling_18' (Byte), 'output_value_cooling_18_5' (Byte), 'output_value_cooling_19' (Byte), 'output_value_cooling_19_5' (Byte), 'output_value_cooling_20' (Byte), 'output_value_cooling_20_5' (Byte), 'output_value_cooling_21' (Byte), 'output_value_cooling_21_5' (Byte), 'output_value_cooling_22' (Byte), 'output_value_cooling_22_5' (Byte), 'output_value_cooling_23' (Byte), 'output_value_cooling_23_5' (Byte), 'output_value_cooling_24' (Byte), 'output_value_heating_16' (Byte), 'output_value_heating_16_5' (Byte), 'output_value_heating_17' (Byte), 'output_value_heating_17_5' (Byte), 'output_value_heating_18' (Byte), 'output_value_heating_18_5' (Byte), 'output_value_heating_19' (Byte), 'output_value_heating_19_5' (Byte), 'output_value_heating_20' (Byte), 'output_value_heating_20_5' (Byte), 'output_value_heating_21' (Byte), 'output_value_heating_21_5' (Byte), 'output_value_heating_22' (Byte), 'output_value_heating_22_5' (Byte), 'output_value_heating_23' (Byte), 'output_value_heating_23_5' (Byte), 'output_value_heating_24' (Byte)
        :type config: dict
        """
        self._thermostat_controller.v0_set_global_rtd10_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_rtd10_heating_configuration(self, id, fields=None):
        """
        Get a specific rtd10_heating_configuration defined by its id.

        :param id: The id of the rtd10_heating_configuration
        :type id: int
        :param fields: The field of the rtd10_heating_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_rtd10_heating_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_rtd10_heating_configurations(self, fields=None):
        """
        Get all rtd10_heating_configurations.

        :param fields: The field of the rtd10_heating_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_rtd10_heating_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_rtd10_heating_configuration(self, config):
        """
        Set one rtd10_heating_configuration.

        :param config: The rtd10_heating_configuration to set: rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :type config: dict
        """
        self._thermostat_controller.v0_set_rtd10_heating_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_rtd10_heating_configurations(self, config):
        """
        Set multiple rtd10_heating_configurations.

        :param config: The list of rtd10_heating_configurations to set: list of rtd10_heating_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :type config: list
        """
        self._thermostat_controller.v0_set_rtd10_heating_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_rtd10_cooling_configuration(self, id, fields=None):
        """
        Get a specific rtd10_cooling_configuration defined by its id.

        :param id: The id of the rtd10_cooling_configuration
        :type id: int
        :param fields: The field of the rtd10_cooling_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_rtd10_cooling_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_rtd10_cooling_configurations(self, fields=None):
        """
        Get all rtd10_cooling_configurations.

        :param fields: The field of the rtd10_cooling_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :rtype: dict
        """
        return {'config': self._thermostat_controller.v0_get_rtd10_cooling_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_rtd10_cooling_configuration(self, config):
        """
        Set one rtd10_cooling_configuration.

        :param config: The rtd10_cooling_configuration to set: rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :type config: dict
        """
        self._thermostat_controller.v0_set_rtd10_cooling_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_rtd10_cooling_configurations(self, config):
        """
        Set multiple rtd10_cooling_configurations.

        :param config: The list of rtd10_cooling_configurations to set: list of rtd10_cooling_configuration dict: contains 'id' (Id), 'mode_output' (Byte), 'mode_value' (Byte), 'on_off_output' (Byte), 'poke_angle_output' (Byte), 'poke_angle_value' (Byte), 'room' (Byte), 'temp_setpoint_output' (Byte), 'ventilation_speed_output' (Byte), 'ventilation_speed_value' (Byte)
        :type config: list
        """
        self._thermostat_controller.v0_set_rtd10_cooling_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_group_action_configuration(self, id, fields=None):
        """
        Get a specific group_action_configuration defined by its id.

        :param id: The id of the group_action_configuration
        :type id: int
        :param fields: The field of the group_action_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': group_action_configuration dict: contains 'id' (Id), 'actions' (Actions[16]), 'name' (String[16])
        :rtype: dict
        """
        return {'config': self._gateway_api.get_group_action_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_group_action_configurations(self, fields=None):
        """
        Get all group_action_configurations.

        :param fields: The field of the group_action_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of group_action_configuration dict: contains 'id' (Id), 'actions' (Actions[16]), 'name' (String[16])
        :rtype: dict
        """
        return {'config': self._gateway_api.get_group_action_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_group_action_configuration(self, config):
        """
        Set one group_action_configuration.

        :param config: The group_action_configuration to set: group_action_configuration dict: contains 'id' (Id), 'actions' (Actions[16]), 'name' (String[16])
        :type config: dict
        """
        self._gateway_api.set_group_action_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_group_action_configurations(self, config):
        """
        Set multiple group_action_configurations.

        :param config: The list of group_action_configurations to set: list of group_action_configuration dict: contains 'id' (Id), 'actions' (Actions[16]), 'name' (String[16])
        :type config: list
        """
        self._gateway_api.set_group_action_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_scheduled_action_configuration(self, id, fields=None):
        """
        Get a specific scheduled_action_configuration defined by its id.

        :param id: The id of the scheduled_action_configuration
        :type id: int
        :param fields: The field of the scheduled_action_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_scheduled_action_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_scheduled_action_configurations(self, fields=None):
        """
        Get all scheduled_action_configurations.

        :param fields: The field of the scheduled_action_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_scheduled_action_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_scheduled_action_configuration(self, config):
        """
        Set one scheduled_action_configuration.

        :param config: The scheduled_action_configuration to set: scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        :type config: dict
        """
        self._gateway_api.set_scheduled_action_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_scheduled_action_configurations(self, config):
        """
        Set multiple scheduled_action_configurations.

        :param config: The list of scheduled_action_configurations to set: list of scheduled_action_configuration dict: contains 'id' (Id), 'action' (Actions[1]), 'day' (Byte), 'hour' (Byte), 'minute' (Byte)
        :type config: list
        """
        self._gateway_api.set_scheduled_action_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_pulse_counter_configuration(self, id, fields=None):
        """
        Get a specific pulse_counter_configuration defined by its id.

        :param id: The id of the pulse_counter_configuration
        :type id: int
        :param fields: The field of the pulse_counter_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': pulse_counter_configuration dict: contains 'id' (Id), 'input' (Byte), 'name' (String[16]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_pulse_counter_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_pulse_counter_configurations(self, fields=None):
        """
        Get all pulse_counter_configurations.

        :param fields: The field of the pulse_counter_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of pulse_counter_configuration dict: contains 'id' (Id), 'input' (Byte), 'name' (String[16]), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_pulse_counter_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_pulse_counter_configuration(self, config):
        """
        Set one pulse_counter_configuration.

        :param config: The pulse_counter_configuration to set: pulse_counter_configuration dict: contains 'id' (Id), 'input' (Byte), 'name' (String[16]), 'room' (Byte)
        :type config: dict
        """
        self._gateway_api.set_pulse_counter_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_pulse_counter_configurations(self, config):
        """
        Set multiple pulse_counter_configurations.

        :param config: The list of pulse_counter_configurations to set: list of pulse_counter_configuration dict: contains 'id' (Id), 'input' (Byte), 'name' (String[16]), 'room' (Byte)
        :type config: list
        """
        self._gateway_api.set_pulse_counter_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_startup_action_configuration(self, fields=None):
        """
        Get the startup_action_configuration.

        :param fields: The field of the startup_action_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': startup_action_configuration dict: contains 'actions' (Actions[100])
        :rtype: dict
        """
        return {'config': self._gateway_api.get_startup_action_configuration(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_startup_action_configuration(self, config):
        """
        Set the startup_action_configuration.

        :param config: The startup_action_configuration to set: startup_action_configuration dict: contains 'actions' (Actions[100])
        :type config: dict
        """
        self._gateway_api.set_startup_action_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_dimmer_configuration(self, fields=None):
        """
        Get the dimmer_configuration.

        :param fields: The field of the dimmer_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': dimmer_configuration dict: contains 'dim_memory' (Byte), 'dim_step' (Byte), 'dim_wait_cycle' (Byte), 'min_dim_level' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_dimmer_configuration(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_dimmer_configuration(self, config):
        """
        Set the dimmer_configuration.

        :param config: The dimmer_configuration to set: dimmer_configuration dict: contains 'dim_memory' (Byte), 'dim_step' (Byte), 'dim_wait_cycle' (Byte), 'min_dim_level' (Byte)
        :type config: dict
        """
        self._gateway_api.set_dimmer_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_global_thermostat_configuration(self, fields=None):
        """
        Get the global_thermostat_configuration.

        :param fields: The field of the global_thermostat_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': global_thermostat_configuration dict: contains 'outside_sensor' (Byte), 'pump_delay' (Byte), 'switch_to_cooling_output_0' (Byte), 'switch_to_cooling_output_1' (Byte), 'switch_to_cooling_output_2' (Byte), 'switch_to_cooling_output_3' (Byte), 'switch_to_cooling_value_0' (Byte), 'switch_to_cooling_value_1' (Byte), 'switch_to_cooling_value_2' (Byte), 'switch_to_cooling_value_3' (Byte), 'switch_to_heating_output_0' (Byte), 'switch_to_heating_output_1' (Byte), 'switch_to_heating_output_2' (Byte), 'switch_to_heating_output_3' (Byte), 'switch_to_heating_value_0' (Byte), 'switch_to_heating_value_1' (Byte), 'switch_to_heating_value_2' (Byte), 'switch_to_heating_value_3' (Byte), 'threshold_temp' (Temp)
        :rtype: str
        """
        return {'config': self._thermostat_controller.v0_get_global_thermostat_configuration(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_global_thermostat_configuration(self, config):
        """
        Set the global_thermostat_configuration.

        :param config: The global_thermostat_configuration to set: global_thermostat_configuration dict: contains 'outside_sensor' (Byte), 'pump_delay' (Byte), 'switch_to_cooling_output_0' (Byte), 'switch_to_cooling_output_1' (Byte), 'switch_to_cooling_output_2' (Byte), 'switch_to_cooling_output_3' (Byte), 'switch_to_cooling_value_0' (Byte), 'switch_to_cooling_value_1' (Byte), 'switch_to_cooling_value_2' (Byte), 'switch_to_cooling_value_3' (Byte), 'switch_to_heating_output_0' (Byte), 'switch_to_heating_output_1' (Byte), 'switch_to_heating_output_2' (Byte), 'switch_to_heating_output_3' (Byte), 'switch_to_heating_value_0' (Byte), 'switch_to_heating_value_1' (Byte), 'switch_to_heating_value_2' (Byte), 'switch_to_heating_value_3' (Byte), 'threshold_temp' (Temp)
        :type config: dict
        """
        self._thermostat_controller.v0_set_global_thermostat_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_can_led_configuration(self, id, fields=None):
        """
        Get a specific can_led_configuration defined by its id.

        :param id: The id of the can_led_configuration
        :type id: int
        :param fields: The field of the can_led_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_can_led_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_can_led_configurations(self, fields=None):
        """
        Get all can_led_configurations.

        :param fields: The field of the can_led_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_can_led_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_can_led_configuration(self, config):
        """
        Set one can_led_configuration.

        :param config: The can_led_configuration to set: can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        :type config: dict
        """
        self._gateway_api.set_can_led_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_can_led_configurations(self, config):
        """
        Set multiple can_led_configurations.

        :param config: The list of can_led_configurations to set: list of can_led_configuration dict: contains 'id' (Id), 'can_led_1_function' (Enum), 'can_led_1_id' (Byte), 'can_led_2_function' (Enum), 'can_led_2_id' (Byte), 'can_led_3_function' (Enum), 'can_led_3_id' (Byte), 'can_led_4_function' (Enum), 'can_led_4_id' (Byte), 'room' (Byte)
        :type config: list
        """
        self._gateway_api.set_can_led_configurations(config)
        return {}

    @openmotics_api(auth=True, check=types(id=int, fields='json'))
    def get_room_configuration(self, id, fields=None):
        """
        Get a specific room_configuration defined by its id.

        :param id: The id of the room_configuration
        :type id: int
        :param fields: The field of the room_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': room_configuration dict: contains 'id' (Id), 'floor' (Byte), 'name' (String)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_room_configuration(id, fields)}

    @openmotics_api(auth=True, check=types(fields='json'))
    def get_room_configurations(self, fields=None):
        """
        Get all room_configurations.

        :param fields: The field of the room_configuration to get. (None gets all fields)
        :type fields: list
        :returns: 'config': list of room_configuration dict: contains 'id' (Id), 'floor' (Byte), 'name' (String)
        :rtype: dict
        """
        return {'config': self._gateway_api.get_room_configurations(fields)}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_room_configuration(self, config):
        """
        Set one room_configuration.

        :param config: The room_configuration to set: room_configuration dict: contains 'id' (Id), 'floor' (Byte), 'name' (String)
        :type config: dict
        """
        self._gateway_api.set_room_configuration(config)
        return {}

    @openmotics_api(auth=True, check=types(config='json'))
    def set_room_configurations(self, config):
        """
        Set multiple room_configurations.

        :param config: The list of room_configurations to set: list of room_configuration dict: contains 'id' (Id), 'floor' (Byte), 'name' (String)
        :type config: list
        """
        self._gateway_api.set_room_configurations(config)
        return {}

    @openmotics_api(auth=True)
    def get_reset_dirty_flag(self):
        """
        Gets the dirty flags, and immediately clears them
        """
        power_dirty = self._power_dirty
        self._power_dirty = False
        return {'eeprom': self._gateway_api.get_reset_eeprom_dirty_flag(),
                'power': power_dirty}

    @openmotics_api(auth=True)
    def get_power_modules(self):
        """
        Get information on the power modules. The times format is a comma seperated list of
        HH:MM formatted times times (index 0 = start Monday, index 1 = stop Monday,
        index 2 = start Tuesday, ...).

        :returns: 'modules': list of dictionaries with the following keys: 'id', 'name', \
            'address', 'input0', 'input1', 'input2', 'input3', 'input4', 'input5', 'input6', \
            'input7', 'sensor0', 'sensor1', 'sensor2', 'sensor3', 'sensor4', 'sensor5', 'sensor6', \
            'sensor7', 'times0', 'times1', 'times2', 'times3', 'times4', 'times5', 'times6', 'times7'.
        :rtype: dict
        """
        return {'modules': self._gateway_api.get_power_modules()}

    @openmotics_api(auth=True)
    def set_power_modules(self, modules):
        """
        Set information for the power modules.

        :param modules: json encoded list of dicts with keys: 'id', 'name', 'input0', 'input1', \
            'input2', 'input3', 'input4', 'input5', 'input6', 'input7', 'sensor0', 'sensor1', \
            'sensor2', 'sensor3', 'sensor4', 'sensor5', 'sensor6', 'sensor7', 'times0', 'times1', \
            'times2', 'times3', 'times4', 'times5', 'times6', 'times7'.
        :type modules: str
        """
        return self._gateway_api.set_power_modules(json.loads(modules))

    @openmotics_api(auth=True)
    def get_realtime_power(self):
        """
        Get the realtime power measurements.

        :returns: module id as the keys: [voltage, frequency, current, power].
        :rtype: dict
        """
        return self._gateway_api.get_realtime_power()

    @openmotics_api(auth=True)
    def get_total_energy(self):
        """
        Get the total energy (Wh) consumed by the power modules.

        :returns: modules id as key: [day, night].
        :rtype: dict
        """
        return self._gateway_api.get_total_energy()

    @openmotics_api(auth=True)
    def start_power_address_mode(self):
        """
        Start the address mode on the power modules.
        """
        return self._gateway_api.start_power_address_mode()

    @openmotics_api(auth=True)
    def stop_power_address_mode(self):
        """
        Stop the address mode on the power modules.
        """
        self._power_dirty = True
        return self._gateway_api.stop_power_address_mode()

    @openmotics_api(auth=True)
    def in_power_address_mode(self):
        """
        Check if the power modules are in address mode.

        :returns: 'address_mode': Boolean
        :rtype: dict
        """
        return self._gateway_api.in_power_address_mode()

    @openmotics_api(auth=True, check=types(module_id=int, voltage=float))
    def set_power_voltage(self, module_id, voltage):
        """
        Set the voltage for a given module.

        :param module_id: The id of the power module.
        :type module_id: int
        :param voltage: The voltage to set for the power module.
        :type voltage: float
        """
        return self._gateway_api.set_power_voltage(module_id, voltage)

    @openmotics_api(auth=True, check=types(amount=int))
    def set_pulse_counter_amount(self, amount):
        """
        Set the number of pulse counters. The minimum is 24, these are the pulse counters
        that can be linked to an input. An amount greater than 24 will result in virtual
        pulse counter that can be set through the API.

        :param amount: The number of pulse counters.
        :type amount: int
        :returns: 'amount': number of pulse counters.
        :rtype: dict
        """
        return {'amount': self._gateway_api.set_pulse_counter_amount(amount)}

    @openmotics_api(auth=True)
    def get_pulse_counter_status(self):
        """
        Get the pulse counter values.

        :returns: 'counters': array with the pulse counter values.
        :rtype: dict
        """
        return {'counters': self._gateway_api.get_pulse_counter_status()}

    @openmotics_api(auth=True, check=types(pulse_counter_id=int, value=int))
    def set_pulse_counter_status(self, pulse_counter_id, value):
        """
        Sets a pulse counter to a value. This can only be done for virtual pulse counters,
        with a pulse_counter_id >= 24.

        :param pulse_counter_id: The id of the pulse counter.
        :type pulse_counter_id: int
        :param value: The new value for the pulse counter.
        :type value: int
        :returns: 'value': the updated value of the pulse counter.
        :rtype: dict
        """
        return {'value': self._gateway_api.set_pulse_counter_status(pulse_counter_id, value)}

    @openmotics_api(auth=True, check=types(module_id=int, input_id=int))
    def get_energy_time(self, module_id, input_id=None):
        """
        Gets 1 period of given module and optional input (no input means all).

        :param module_id: The id of the power module.
        :type module_id: int
        :param input_id: The id of the input on the given power module
        :type input_id: int or None
        :returns: A dict with the input_id(s) as key, and as value another dict with
                  (up to 80) voltage and current samples.
        :rtype: dict
        """
        return self._gateway_api.get_energy_time(module_id, input_id)

    @openmotics_api(auth=True, check=types(module_id=int, input_id=int))
    def get_energy_frequency(self, module_id, input_id=None):
        """
        Gets the frequency components for a given module and optional input (no input means all)

        :param module_id: The id of the power module
        :type module_id: int
        :param input_id: The id of the input on the given power module
        :type input_id: int | None
        :returns: A dict with the input_id(s) as key, and as value another dict with for
                  voltage and current the 20 frequency components
        :rtype: dict
        """
        return self._gateway_api.get_energy_frequency(module_id, input_id)

    @openmotics_api(auth=True, check=types(address=int), plugin_exposed=False)
    def do_raw_energy_command(self, address, mode, command, data):
        """
        Perform a raw energy module command, for debugging purposes.

        :param address: The address of the energy module
        :type address: int
        :param mode: 1 char: S or G
        :type mode: str
        :param command: 3 char power command
        :type command: str
        :param data: comma seperated list of Bytes
        :type data: str
        :returns: dict with 'data': comma separated list of Bytes
        :rtype: dict
        """
        if mode not in ['S', 'G']:
            raise ValueError("mode not in [S, G]: %s" % mode)

        if len(command) != 3:
            raise ValueError('Command should be 3 chars, got "%s"' % command)

        if data:
            bdata = [int(c) for c in data.split(",")]
        else:
            bdata = []

        ret = self._gateway_api.do_raw_energy_command(address, mode, command, bdata)
        return {'data': ",".join([str(d) for d in ret])}

    @openmotics_api(auth=True)
    def get_version(self):
        """
        Get the version of the openmotics software.

        :returns: 'version': String (a.b.c).
        :rtype: dict
        """
        return {'version': self._gateway_api.get_main_version(),
                'gateway': '2.14.1'}

    @openmotics_api(auth=True, plugin_exposed=False)
    def update(self, version, md5, update_data):
        """
        Perform an update.

        :param version: the new version number.
        :type version: str
        :param md5: the md5 sum of update_data.
        :type md5: str
        :param update_data: a tgz file containing the update script (update.sh) and data.
        :type update_data: multipart/form-data encoded byte string.
        """
        update_data = update_data.file.read()

        if not os.path.exists(constants.get_update_dir()):
            os.mkdir(constants.get_update_dir())

        with open(constants.get_update_file(), "wb") as update_file:
            update_file.write(update_data)
        with open(constants.get_update_output_file(), "w") as output_file:
            output_file.write('\n')  # Truncate file

        subprocess.Popen(constants.get_update_cmd(version, md5), close_fds=True)

        return {}

    @openmotics_api(auth=True)
    def get_update_output(self):
        """
        Get the output of the last update.

        :returns: 'output': String with the output from the update script.
        :rtype: dict
        """
        with open(constants.get_update_output_file(), "r") as output_file:
            output = output_file.read()
        version = self._gateway_api.get_main_version()

        return {'output': output,
                'version': version}

    @openmotics_api(auth=True)
    def set_timezone(self, timezone):
        """
        Set the timezone for the gateway.

        :param timezone: in format 'Continent/City'.
        :type timezone: str
        """
        self._gateway_api.set_timezone(timezone)
        self._gateway_api.sync_master_time()
        return {}

    @openmotics_api(auth=True)
    def get_timezone(self):
        """
        Get the timezone for the gateway.

        :returns: 'timezone': the timezone in 'Continent/City' format (String).
        :rtype: dict
        """
        return {'timezone': self._gateway_api.get_timezone()}

    @openmotics_api(auth=True, check=types(headers='json', auth='json', timeout=int), plugin_exposed=False)
    def do_url_action(self, url, method='GET', headers=None, data=None, auth=None, timeout=10):
        """
        Execute an url action.

        :param url: The url to fetch.
        :type url: str
        :param method: (optional) The http method (defaults to GET).
        :type method: str | None
        :param headers: (optional) The http headers to send (format: json encoded dict)
        :type headers: str | None
        :param data: (optional) Bytes to send in the body of the request.
        :type data: str | None
        :param auth: (optional) Json encoded tuple (username, password).
        :type auth: str | None
        :param timeout: (optional) Timeout in seconds for the http request (default = 10 sec).
        :type timeout: int | None
        :returns: 'headers': response headers, 'data': response body.
        :rtype: dict
        """
        response = requests.request(method, url,
                                    headers=headers,
                                    data=data,
                                    auth=auth,
                                    timeout=timeout)

        if response.status_code != requests.codes.ok:
            raise RuntimeError("Got bad resonse code: %d" % response.status_code)
        return {'headers': response.headers._store,
                'data': response.text}

    @openmotics_api(auth=True, check=types(timestamp=int, action='json'), deprecated='add_schedule')
    def schedule_action(self, timestamp, action):
        self.add_schedule(name=action['description'],
                          start=timestamp,
                          schedule_type='LOCAL_API',
                          arguments={'name': action['action'],
                                     'parameters': action['params']})
        return {}

    @openmotics_api(auth=True, check=types(name=str, start=int, schedule_type=str, arguments='json', repeat='json', duration=int, end=int))
    def add_schedule(self, name, start, schedule_type, arguments=None, repeat=None, duration=None, end=None):
        self._scheduling_controller.add_schedule(name, start, schedule_type, arguments, repeat, duration, end)
        return {}

    @openmotics_api(auth=True, deprecated='list_schedules')
    def list_scheduled_actions(self):
        return {'actions': [{'timestamp': schedule.start,
                             'from_now': schedule.start - time.time(),
                             'id': schedule.id,
                             'description': schedule.name,
                             'action': json.dumps({'action': schedule.arguments['name'],
                                                   'params': schedule.arguments['parameters']})}
                            for schedule in self._scheduling_controller.schedules
                            if schedule.schedule_type == 'LOCAL_API']}

    @openmotics_api(auth=True)
    def list_schedules(self):
        return {'schedules': [schedule.serialize() for schedule in self._scheduling_controller.schedules]}

    @openmotics_api(auth=True, check=types(id=int), deprecated='remove_schedule')
    def remove_scheduled_action(self, id):
        self._scheduling_controller.remove_schedule(id)
        return {}

    @openmotics_api(auth=True, check=types(schedule_id=int))
    def remove_schedule(self, schedule_id):
        self._scheduling_controller.remove_schedule(schedule_id)
        return {}

    @openmotics_api(auth=True, plugin_exposed=False)
    def create_or_update_light(self, name, type, plugin_name, external_id):
        """
        Creates a new light.

        :param name: Name of the light
        :type name: str
        :param type: Type of  of the light
        :type type: type
        :param external_id: External id of the light
        :type external_id: int
        :param plugin_name: plugin name of the plugin this light belongs to
        :type plugin_name: str
        """
        return self._light_controller.create_or_update_light(name, type, plugin_name, external_id)

    @openmotics_api(auth=True, plugin_exposed=False)
    def delete_light(self, plugin_name, external_id):
        """
        Deletes a light.

        :param external_id: External id of the light
        :type external_id: int
        :param plugin_name: plugin name of the plugin this light belongs to
        :type plugin_name: str
        """
        return self._light_controller.delete_light_by_external_id(plugin_name, external_id)

    @openmotics_api(auth=True)
    def get_plugins(self):
        """
        Get the installed plugins.

        :returns: 'plugins': dict with name, version and interfaces where name and version \
            are strings and interfaces is a list of tuples (interface, version) which are both strings.
        :rtype: dict
        """
        plugins = self._plugin_controller.get_plugins()
        ret = [{'name': p.name,
                'version': p.version,
                'interfaces': p.interfaces,
                'status': 'RUNNING' if p.is_running() else 'STOPPED'} for p in plugins]
        return {'plugins': ret}

    @openmotics_api(auth=True, plugin_exposed=False)
    def get_plugin_logs(self):
        """
        Get the logs for all plugins.

        :returns: 'logs': dict with the names of the plugins as keys and the logs (String) as \
            value.
        :rtype: dict
        """
        return {'logs': self._plugin_controller.get_logs()}

    @openmotics_api(auth=True, plugin_exposed=False)
    def install_plugin(self, md5, package_data):
        """
        Install a new plugin. The package_data should include a __init__.py file and
        will be installed in /opt/openmotics/python/plugins/<name>.

        :param md5: md5 sum of the package_data.
        :type md5: String
        :param package_data: a tgz file containing the content of the plugin package.
        :type package_data: multipart/form-data encoded byte string.
        """
        return {'msg': self._plugin_controller.install_plugin(md5, package_data.file.read())}

    @openmotics_api(auth=True, plugin_exposed=False)
    def remove_plugin(self, name):
        """
        Remove a plugin. This removes the package data and configuration data of the plugin.

        :param name: Name of the plugin to remove.
        :type name: str
        """
        return self._plugin_controller.remove_plugin(name)

    @openmotics_api(auth=True, plugin_exposed=False)
    def stop_plugin(self, name):
        """
        Stops a plugin
        """
        running = self._plugin_controller.stop_plugin(name)
        return {'status': 'RUNNING' if running else 'STOPPED'}

    @openmotics_api(auth=True, plugin_exposed=False)
    def start_plugin(self, name):
        """
        Starts a plugin
        """
        running = self._plugin_controller.start_plugin(name)
        return {'status': 'RUNNING' if running else 'STOPPED'}

    @openmotics_api(auth=True, check=types(settings='json'), plugin_exposed=False)
    def get_settings(self, settings):
        """
        Gets a given setting
        """
        values = {}
        for setting in settings:
            value = self._config_controller.get_setting(setting)
            if value is not None:
                values[setting] = value
        return {'values': values}

    @openmotics_api(auth=True, check=types(value='json'), plugin_exposed=False)
    def set_setting(self, setting, value):
        """
        Configures a setting
        """
        if setting not in ['cloud_enabled', 'cloud_metrics_enabled|energy', 'cloud_metrics_enabled|counter',
                           'cloud_support']:
            raise RuntimeError('Setting {0} cannot be set'.format(setting))
        self._config_controller.set_setting(setting, value)
        return {}

    @openmotics_api(auth=True)
    def self_test(self):
        """
        Perform a Gateway self-test.
        """
        if not self.in_authorized_mode():
            raise cherrypy.HTTPError(401, "unauthorized")
        subprocess.Popen(constants.get_self_test_cmd(), close_fds=True)
        return {}

    @openmotics_api(auth=True)
    def get_metric_definitions(self, source=None, metric_type=None):
        sources = self._metrics_controller.get_filter('source', source)
        metric_types = self._metrics_controller.get_filter('metric_type', metric_type)
        definitions = {}
        for _source, _metric_types in self._metrics_controller.definitions.iteritems():
            if _source in sources:
                definitions[_source] = {}
                for _metric_type, definition in _metric_types.iteritems():
                    if _metric_type in metric_types:
                        definitions[_source][_metric_type] = definition
        return {'definitions': definitions}

    @openmotics_api(auth=True, plugin_exposed=False)
    def cleanup_eeprom(self):
        self._gateway_api.cleanup_eeprom()
        return {}

    @openmotics_api(auth=True, plugin_exposed=False)
    def factory_reset(self):
        self._gateway_api.factory_reset()
        return {}

    @openmotics_api(auth=False)
    def health_check(self):
        """ Requests the state of the various services and checks the returned value for the global state """
        health = {'openmotics': {'state': True}}
        try:
            state = self._message_client.get_state('vpn_service', {})
            health['vpn_service'] = {'state': state.get('last_cycle', 0) > time.time() - 300}
        except Exception as ex:
            logger.error('Error loading vpn_service health: %s', ex)
            health['vpn_service'] = {'state': False}
        try:
            state = self._message_client.get_state('led_service', {})
            state_ok = True
            for run in ['run_gpio', 'run_i2c', 'run_buttons', 'run_state_check']:
                state_ok = state_ok and (state.get(run, 0) > time.time() - 5)
            health['led_service'] = {'state': state_ok}
        except Exception as ex:
            logger.error('Error loading led_service health: %s', ex)
            health['led_service'] = {'state': False}
        return {'health': health,
                'health_version': 1.0}

    @openmotics_api(auth=True)
    def indicate(self):
        """ Blinks the Status led on the Gateway to indicate the module """
        self._message_client.send_event(OMBusEvents.INDICATE_GATEWAY, None)
        return {}

    @cherrypy.expose
    @cherrypy.tools.cors()
    @cherrypy.tools.authenticated(pass_token=True)
    def ws_metrics(self, token, source=None, metric_type=None, interval=None):
        cherrypy.request.ws_handler.metadata = {'token': token,
                                                'client_id': uuid.uuid4().hex,
                                                'source': source,
                                                'metric_type': metric_type,
                                                'interval': None if interval is None else int(interval),
                                                'interface': self}

    @cherrypy.expose
    @cherrypy.tools.cors()
    @cherrypy.tools.authenticated(pass_token=True)
    def ws_events(self, token):
        cherrypy.request.ws_handler.metadata = {'token': token,
                                                'client_id': uuid.uuid4().hex,
                                                'interface': self}

    @cherrypy.expose
    @cherrypy.tools.cors()
    @cherrypy.tools.authenticated(pass_token=True)
    def ws_maintenance(self, token):
        cherrypy.request.ws_handler.metadata = {'token': token,
                                                'client_id': uuid.uuid4().hex,
                                                'interface': self}


@Injectable.named('web_service')
@Singleton
class WebService(object):
    """ The web service serves the gateway api over http. """

    name = 'web'

    @Inject
    def __init__(self, web_interface=INJECTED, configuration_controller=INJECTED, verbose=False):
        self._webinterface = web_interface
        self._config_controller = configuration_controller
        self._https_server = None
        self._http_server = None
        self._running = False
        if not verbose:
            logging.getLogger("cherrypy").propagate = False

    def run(self):
        """ Run the web service: start cherrypy. """
        try:
            logger.info('Starting webserver...')
            OMPlugin(cherrypy.engine).subscribe()
            cherrypy.tools.websocket = OMSocketTool()

            config = {'/terms': {'tools.staticdir.on': True,
                                 'tools.staticdir.dir': '/opt/openmotics/python/terms'},
                      '/static': {'tools.staticdir.on': True,
                                  'tools.staticdir.dir': '/opt/openmotics/static'},
                      '/ws_metrics': {'tools.websocket.on': True,
                                      'tools.websocket.handler_cls': MetricsSocket},
                      '/ws_events': {'tools.websocket.on': True,
                                     'tools.websocket.handler_cls': EventsSocket},
                      '/ws_maintenance': {'tools.websocket.on': True,
                                          'tools.websocket.handler_cls': MaintenanceSocket},
                      '/': {'tools.timestamp_filter.on': True,
                            'tools.cors.on': self._config_controller.get_setting('cors_enabled', False),
                            'tools.sessions.on': False}}

            cherrypy.tree.mount(root=self._webinterface,
                                config=config)

            cherrypy.config.update({'engine.autoreload.on': False})
            cherrypy.server.unsubscribe()

            self._https_server = cherrypy._cpserver.Server()
            self._https_server.socket_port = 443
            self._https_server._socket_host = '0.0.0.0'
            self._https_server.socket_timeout = 60
            System.setup_cherrypy_ssl(self._https_server,
                                      private_key_filename=constants.get_ssl_private_key_file(),
                                      certificate_filename=constants.get_ssl_certificate_file())
            self._https_server.subscribe()

            self._http_server = cherrypy._cpserver.Server()
            self._http_server.socket_port = 80
            self._http_server._socket_host = '127.0.0.1'
            self._http_server.socket_timeout = 60
            self._http_server.subscribe()

            cherrypy.engine.autoreload_on = False

            cherrypy.engine.start()
            self._https_server.httpserver.error_log = WebService._http_server_logger
            self._http_server.httpserver.error_log = WebService._http_server_logger
            self._running = True
            logger.info('Starting webserver... Done')
            cherrypy.engine.block()
            logger.info('Webserver stopped')
            self._running = False
        except Exception:
            logger.exception("Could not start webservice. Dying...")
            sys.exit(1)

    @staticmethod
    def _http_server_logger(msg='', level=20, traceback=False):
        """
        This workaround is to lower some CherryPy "TICK"-SSL errors' severity that are incorrectly
        logged in our version of CherryPy. It is already resolved in a newer version, but we
        still need to upgrade
        """
        # TODO upgrade cherrypy
        _ = level, traceback
        logger.debug(msg)

    def start(self):
        """ Start the web service in a new thread. """
        thread = threading.Thread(target=self.run)
        thread.setName("Web service thread")
        thread.daemon = True
        thread.start()
        counter = 20
        while self._running is False and counter > 0:
            time.sleep(0.5)
            counter -= 1

    def stop(self, timeout=1):
        """ Stop the web service. """
        logger.info('Stopping webserver...')
        cherrypy.engine.exit()  # Shutdown the cherrypy server: no new requests
        start = time.time()
        while self._running and time.time() - start < timeout:
            time.sleep(0.1)
        logger.info('Stopping webserver... Done')

    def update_tree(self, mounts):
        self._http_server.stop()
        self._https_server.stop()
        for mount in mounts:
            cherrypy.tree.mount(**mount)
        self._http_server.start()
        self._https_server.start()
        self._https_server.httpserver.error_log = WebService._http_server_logger
        self._http_server.httpserver.error_log = WebService._http_server_logger
