# Copyright (C) 2016 OpenMotics BVBA
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
The main module for the OpenMotics
"""
from platform_utils import System, Platform
System.import_eggs()

import logging
import time
import constants
from wiring import Graph, SingletonScope
from bus.om_bus_service import MessageService
from bus.om_bus_client import MessageClient
from cloud.cloud_api_client import CloudAPIClient
from cloud.events import EventSender
from serial import Serial
from signal import signal, SIGTERM
from ConfigParser import ConfigParser
from threading import Lock
from serial_utils import RS485
from gateway.webservice import WebInterface, WebService
from gateway.comm_led_controller import CommunicationLedController
from gateway.gateway_api import GatewayApi
from gateway.users import UserController
from gateway.metrics_controller import MetricsController
from gateway.metrics_collector import MetricsCollector
from gateway.metrics_caching import MetricsCacheController
from gateway.config import ConfigurationController
from gateway.scheduling import SchedulingController
from gateway.pulses import PulseCounterController
from gateway.observer import Observer, Event
from gateway.shutters import ShutterController
from gateway.hal.master_controller_classic import MasterClassicController
from gateway.hal.master_controller_core import MasterCoreController
from gateway.maintenance_controller import MaintenanceController
from urlparse import urlparse
from master.eeprom_controller import EepromController, EepromFile
from master.eeprom_extension import EepromExtension
from master.maintenance import MaintenanceClassicService
from master.master_communicator import MasterCommunicator
from master.passthrough import PassthroughService
from master_core.core_communicator import CoreCommunicator
from master_core.ucan_communicator import UCANCommunicator
from master_core.memory_file import MemoryFile
from master_core.maintenance import MaintenanceCoreService
from power.power_communicator import PowerCommunicator
from power.power_controller import PowerController
from plugins.base import PluginController

logger = logging.getLogger("openmotics")


def setup_logger():
    """ Setup the OpenMotics logger. """

    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


class OpenmoticsService(object):

    def __init__(self):
        self.graph = Graph()

    def _build_graph(self):
        config = ConfigParser()
        config.read(constants.get_config_file())

        config_lock = Lock()
        scheduling_lock = Lock()
        metrics_lock = Lock()

        config_database_file = constants.get_config_database_file()

        # TODO: Write below code with wiring Modules etc
        # TODO: Clean up dependencies more to reduce complexity

        # IPC
        self.graph.register_instance('message_client', MessageClient('openmotics_service'))

        # Cloud API
        parsed_url = urlparse(config.get('OpenMotics', 'vpn_check_url'))
        self.graph.register_instance('gateway_uuid', config.get('OpenMotics', 'uuid'))
        self.graph.register_instance('cloud_endpoint', parsed_url.hostname)
        self.graph.register_instance('cloud_port', parsed_url.port)
        self.graph.register_instance('cloud_ssl', parsed_url.scheme == 'https')
        self.graph.register_instance('cloud_api_version', 0)
        self.graph.register_factory('cloud_api_client', CloudAPIClient)

        # Events
        self.graph.register_factory('event_sender', EventSender, scope=SingletonScope)

        # User Controller
        self.graph.register_instance('user_db', config_database_file)
        self.graph.register_instance('user_db_lock', config_lock)
        self.graph.register_instance('token_timeout', 3600)
        self.graph.register_instance('config', {'username': config.get('OpenMotics', 'cloud_user'),
                                                'password': config.get('OpenMotics', 'cloud_pass')})
        self.graph.register_factory('user_controller', UserController, scope=SingletonScope)

        # Configuration Controller
        self.graph.register_instance('config_db', config_database_file)
        self.graph.register_instance('config_db_lock', config_lock)
        self.graph.register_factory('config_controller', ConfigurationController, scope=SingletonScope)

        # Energy Controller
        power_serial_port = config.get('OpenMotics', 'power_serial')
        self.graph.register_instance('power_db', constants.get_power_database_file())
        if power_serial_port:
            self.graph.register_instance('power_serial', RS485(Serial(power_serial_port, 115200, timeout=None)))
            self.graph.register_factory('power_communicator', PowerCommunicator, scope=SingletonScope)
            self.graph.register_factory('power_controller', PowerController, scope=SingletonScope)
        else:
            self.graph.register_instance('power_serial', None)
            self.graph.register_instance('power_communicator', None)
            self.graph.register_instance('power_controller', None)

        # Pulse Controller
        self.graph.register_instance('pulse_db', constants.get_pulse_counter_database_file())
        self.graph.register_factory('pulse_controller', PulseCounterController, scope=SingletonScope)

        # Scheduling Controller
        self.graph.register_instance('scheduling_db', constants.get_scheduling_database_file())
        self.graph.register_instance('scheduling_db_lock', scheduling_lock)
        self.graph.register_factory('scheduling_controller', SchedulingController, scope=SingletonScope)

        # Master Controller
        controller_serial_port = config.get('OpenMotics', 'controller_serial')
        self.graph.register_instance('controller_serial', Serial(controller_serial_port, 115200))
        if Platform.get_platform() == Platform.Type.CORE_PLUS:
            core_cli_serial_port = config.get('OpenMotics', 'cli_serial')
            self.graph.register_factory('master_controller', MasterCoreController, scope=SingletonScope)
            self.graph.register_factory('master_core_communicator', CoreCommunicator, scope=SingletonScope)
            self.graph.register_factory('ucan_communicator', UCANCommunicator, scope=SingletonScope)
            self.graph.register_factory('memory_file', MemoryFile, scope=SingletonScope)
            self.graph.register_instance('ucan_communicator_verbose', False)
            self.graph.register_instance('core_communicator_verbose', False)
            self.graph.register_instance('cli_serial', Serial(core_cli_serial_port, 115200))
            self.graph.register_instance('passthrough_service', None)  # Mark as "not needed"
            # TODO: Remove; should not be needed for Core
            self.graph.register_factory('eeprom_controller', EepromController, scope=SingletonScope)
            self.graph.register_factory('eeprom_file', EepromFile, scope=SingletonScope)
            self.graph.register_factory('eeprom_extension', EepromExtension, scope=SingletonScope)
            self.graph.register_instance('eeprom_db', constants.get_eeprom_extension_database_file())
            self.graph.register_factory('master_classic_communicator', CoreCommunicator, scope=SingletonScope)
        else:
            passthrough_serial_port = config.get('OpenMotics', 'passthrough_serial')
            self.graph.register_instance('eeprom_db', constants.get_eeprom_extension_database_file())
            self.graph.register_factory('master_controller', MasterClassicController, scope=SingletonScope)
            self.graph.register_factory('master_classic_communicator', MasterCommunicator, scope=SingletonScope)
            self.graph.register_factory('eeprom_controller', EepromController, scope=SingletonScope)
            self.graph.register_factory('eeprom_file', EepromFile, scope=SingletonScope)
            self.graph.register_factory('eeprom_extension', EepromExtension, scope=SingletonScope)
            if passthrough_serial_port:
                self.graph.register_instance('passthrough_serial', Serial(passthrough_serial_port, 115200))
                self.graph.register_factory('passthrough_service', PassthroughService, scope=SingletonScope)
            else:
                self.graph.register_instance('passthrough_service', None)

        # Maintenance Controller
        self.graph.register_factory('maintenance_controller', MaintenanceController, scope=SingletonScope)
        if Platform.get_platform() == Platform.Type.CORE_PLUS:
            self.graph.register_factory('maintenance_service', MaintenanceCoreService, scope=SingletonScope)
        else:
            self.graph.register_factory('maintenance_service', MaintenanceClassicService, scope=SingletonScope)

        # Metrics Controller
        self.graph.register_instance('metrics_db', constants.get_metrics_database_file())
        self.graph.register_instance('metrics_db_lock', metrics_lock)
        self.graph.register_factory('metrics_collector', MetricsCollector, scope=SingletonScope)
        self.graph.register_factory('metrics_controller', MetricsController, scope=SingletonScope)
        self.graph.register_factory('metrics_cache_controller', MetricsCacheController, scope=SingletonScope)

        # Plugin Controller
        self.graph.register_factory('plugin_controller', PluginController, scope=SingletonScope)

        # Shutter Controller
        self.graph.register_factory('shutter_controller', ShutterController, scope=SingletonScope)

        # Webserver / Presentation layer
        self.graph.register_instance('ssl_private_key', constants.get_ssl_private_key_file())
        self.graph.register_instance('ssl_certificate', constants.get_ssl_certificate_file())
        self.graph.register_factory('web_interface', WebInterface, scope=SingletonScope)
        self.graph.register_factory('web_service', WebService, scope=SingletonScope)
        self.graph.register_factory('communication_led_controller', CommunicationLedController, scope=SingletonScope)

        # Middlewares
        self.graph.register_factory('observer', Observer, scope=SingletonScope)
        self.graph.register_factory('gateway_api', GatewayApi, scope=SingletonScope)

        self.graph.validate()

    def _fix_dependencies(self):
        # TODO: Fix circular dependencies
        metrics_controller = self.graph.get('metrics_controller')
        message_client = self.graph.get('message_client')
        web_interface = self.graph.get('web_interface')
        scheduling_controller = self.graph.get('scheduling_controller')
        observer = self.graph.get('observer')
        gateway_api = self.graph.get('gateway_api')
        metrics_collector = self.graph.get('metrics_collector')
        plugin_controller = self.graph.get('plugin_controller')
        web_service = self.graph.get('web_service')
        event_sender = self.graph.get('event_sender')
        maintenance_controller = self.graph.get('maintenance_controller')

        message_client.add_event_handler(metrics_controller.event_receiver)
        web_interface.set_plugin_controller(plugin_controller)
        web_interface.set_metrics_collector(metrics_collector)
        web_interface.set_metrics_controller(metrics_controller)
        gateway_api.set_plugin_controller(plugin_controller)
        metrics_controller.add_receiver(metrics_controller.receiver)
        metrics_controller.add_receiver(web_interface.distribute_metric)
        scheduling_controller.set_webinterface(web_interface)
        metrics_collector.set_controllers(metrics_controller, plugin_controller)
        plugin_controller.set_webservice(web_service)
        plugin_controller.set_metrics_controller(metrics_controller)
        plugin_controller.set_metrics_collector(metrics_collector)
        observer.set_gateway_api(gateway_api)
        observer.subscribe_events(metrics_collector.process_observer_event)
        observer.subscribe_events(plugin_controller.process_observer_event)
        observer.subscribe_events(web_interface.send_event_websocket)
        observer.subscribe_events(event_sender.enqueue_event)

        # TODO: make sure all subscribers only subscribe to the observer, not master directly
        observer.subscribe_master(Observer.LegacyMasterEvents.ON_INPUT_CHANGE, metrics_collector.on_input)
        observer.subscribe_master(Observer.LegacyMasterEvents.ON_SHUTTER_UPDATE, plugin_controller.process_shutter_status)

        maintenance_controller.subscribe_maintenance_stopped(gateway_api.maintenance_mode_stopped)

    def start(self):
        """ Main function. """
        logger.info('Starting OM core service...')

        self._build_graph()
        self._fix_dependencies()

        service_names = ['master_controller', 'maintenance_controller',
                         'observer', 'power_communicator', 'metrics_controller', 'passthrough_service',
                         'scheduling_controller', 'metrics_collector', 'web_service', 'gateway_api', 'plugin_controller',
                         'communication_led_controller', 'event_sender']
        for name in service_names:
            service = self.graph.get(name)
            if service is not None:
                service.start()

        signal_request = {'stop': False}

        def stop(signum, frame):
            """ This function is called on SIGTERM. """
            _ = signum, frame
            logger.info('Stopping OM core service...')
            services_to_stop = ['master_controller', 'maintenance_controller',
                                'web_service', 'metrics_collector', 'metrics_controller', 'plugin_controller', 'event_sender']
            for service_to_stop in services_to_stop:
                self.graph.get(service_to_stop).stop()
            logger.info('Stopping OM core service... Done')
            signal_request['stop'] = True

        signal(SIGTERM, stop)
        logger.info('Starting OM core service... Done')
        while not signal_request['stop']:
            time.sleep(1)


if __name__ == "__main__":
    setup_logger()
    logger.info("Starting OpenMotics service")

    # TODO: move message service to separate process
    message_service = MessageService()
    message_service.start()

    openmotics_service = OpenmoticsService()
    openmotics_service.start()
