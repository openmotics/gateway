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
The main module for the OpenMotics
"""
from platform_utils import System, Platform
System.import_libs()

import logging
import time
import constants
from models import Database, Feature
from ioc import Injectable, Inject, INJECTED
from bus.om_bus_service import MessageService
from bus.om_bus_client import MessageClient
from serial import Serial
from signal import signal, SIGTERM
from ConfigParser import ConfigParser
from threading import Lock
from serial_utils import RS485
from gateway.observer import Observer
from urlparse import urlparse
from peewee_migrate import Router

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

    @staticmethod
    def build_graph():
        config = ConfigParser()
        config.read(constants.get_config_file())

        config_lock = Lock()
        scheduling_lock = Lock()
        metrics_lock = Lock()

        config_database_file = constants.get_config_database_file()

        # TODO: Clean up dependencies more to reduce complexity

        # IOC announcements
        # When below modules are imported, the classes are registerd in the IOC graph. This is required for
        # instances that are used in @Inject decorated functions below, and is also needed to specify
        # abstract implementations depending on e.g. the platform (classic vs core) or certain settings (classic
        # thermostats vs gateway thermostats)
        from power import power_communicator, power_controller
        from plugins import base
        from gateway import (metrics_controller, webservice, scheduling, observer, gateway_api, metrics_collector,
                             maintenance_controller, comm_led_controller, users, pulses, config as config_controller,
                             metrics_caching)
        from cloud import events
        _ = (metrics_controller, webservice, scheduling, observer, gateway_api, metrics_collector,
             maintenance_controller, base, events, power_communicator, comm_led_controller, users,
             power_controller, pulses, config_controller, metrics_caching)
        if Platform.get_platform() == Platform.Type.CORE_PLUS:
            from gateway.hal import master_controller_core
            from master_core import maintenance, core_communicator, ucan_communicator
            from master import eeprom_extension  # TODO: Obsolete, need to be removed
            _ = master_controller_core, maintenance, core_communicator, ucan_communicator
        else:
            from gateway.hal import master_controller_classic
            from master import maintenance, master_communicator, eeprom_extension
            _ = master_controller_classic, maintenance, master_communicator, eeprom_extension

        thermostats_gateway = Feature.get_or_none(name='thermostats_gateway')
        if thermostats_gateway is not None and thermostats_gateway.enabled:
            from gateway.thermostat.gateway import thermostat_controller_gateway
            _ = thermostat_controller_gateway
        else:
            from gateway.thermostat.master import thermostat_controller_master
            _ = thermostat_controller_master

        # IPC
        Injectable.value(message_client=MessageClient('openmotics_service'))

        # Cloud API
        parsed_url = urlparse(config.get('OpenMotics', 'vpn_check_url'))
        Injectable.value(gateway_uuid=config.get('OpenMotics', 'uuid'))
        Injectable.value(cloud_endpoint=parsed_url.hostname)
        Injectable.value(cloud_port=parsed_url.port)
        Injectable.value(cloud_ssl=parsed_url.scheme == 'https')
        Injectable.value(cloud_api_version=0)

        # User Controller
        Injectable.value(user_db=config_database_file)
        Injectable.value(user_db_lock=config_lock)
        Injectable.value(token_timeout=3600)
        Injectable.value(config={'username': config.get('OpenMotics', 'cloud_user'),
                                 'password': config.get('OpenMotics', 'cloud_pass')})

        # Configuration Controller
        Injectable.value(config_db=config_database_file)
        Injectable.value(config_db_lock=config_lock)

        # Energy Controller
        power_serial_port = config.get('OpenMotics', 'power_serial')
        Injectable.value(power_db=constants.get_power_database_file())
        if power_serial_port:
            Injectable.value(power_serial=RS485(Serial(power_serial_port, 115200, timeout=None)))
        else:
            Injectable.value(power_serial=None)
            Injectable.value(power_communicator=None)
            Injectable.value(power_controller=None)

        # Pulse Controller
        Injectable.value(pulse_db=constants.get_pulse_counter_database_file())

        # Scheduling Controller
        Injectable.value(scheduling_db=constants.get_scheduling_database_file())
        Injectable.value(scheduling_db_lock=scheduling_lock)

        # Master Controller
        controller_serial_port = config.get('OpenMotics', 'controller_serial')
        Injectable.value(controller_serial=Serial(controller_serial_port, 115200))
        if Platform.get_platform() == Platform.Type.CORE_PLUS:
            from master_core.memory_file import MemoryFile, MemoryTypes
            core_cli_serial_port = config.get('OpenMotics', 'cli_serial')
            Injectable.value(cli_serial=Serial(core_cli_serial_port, 115200))
            Injectable.value(passthrough_service=None)  # Mark as "not needed"
            Injectable.value(memory_files={MemoryTypes.EEPROM: MemoryFile(MemoryTypes.EEPROM),
                                           MemoryTypes.FRAM: MemoryFile(MemoryTypes.FRAM)})
            # TODO: Remove; should not be needed for Core
            Injectable.value(eeprom_db=constants.get_eeprom_extension_database_file())
        else:
            passthrough_serial_port = config.get('OpenMotics', 'passthrough_serial')
            Injectable.value(eeprom_db=constants.get_eeprom_extension_database_file())
            if passthrough_serial_port:
                Injectable.value(passthrough_serial=Serial(passthrough_serial_port, 115200))
                from master.passthrough import PassthroughService
                _ = PassthroughService  # IOC announcement
            else:
                Injectable.value(passthrough_service=None)

        # Metrics Controller
        Injectable.value(metrics_db=constants.get_metrics_database_file())
        Injectable.value(metrics_db_lock=metrics_lock)

        # Webserver / Presentation layer
        Injectable.value(ssl_private_key=constants.get_ssl_private_key_file())
        Injectable.value(ssl_certificate=constants.get_ssl_certificate_file())

    @staticmethod
    @Inject
    def fix_dependencies(metrics_controller=INJECTED, message_client=INJECTED, web_interface=INJECTED,
                         scheduling_controller=INJECTED, observer=INJECTED, gateway_api=INJECTED,
                         metrics_collector=INJECTED, plugin_controller=INJECTED, web_service=INJECTED,
                         event_sender=INJECTED, maintenance_controller=INJECTED, thermostat_controller=INJECTED,
                         light_controller=INJECTED):

        # TODO: Fix circular dependencies
        thermostat_controller.subscribe_events(web_interface.send_event_websocket)
        thermostat_controller.subscribe_events(event_sender.enqueue_event)
        thermostat_controller.subscribe_events(plugin_controller.process_observer_event)

        light_controller.subscribe_events(plugin_controller.process_observer_event)

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

    @staticmethod
    @Inject
    def start(master_controller=INJECTED, maintenance_controller=INJECTED,
              observer=INJECTED, power_communicator=INJECTED, metrics_controller=INJECTED, passthrough_service=INJECTED,
              scheduling_controller=INJECTED, metrics_collector=INJECTED, web_service=INJECTED, gateway_api=INJECTED, plugin_controller=INJECTED,
              communication_led_controller=INJECTED, event_sender=INJECTED, thermostat_controller=INJECTED):
        """ Main function. """
        logger.info('Starting OM core service...')

        master_controller.start()
        maintenance_controller.start()
        observer.start()
        power_communicator.start()
        metrics_controller.start()
        if passthrough_service:
            passthrough_service.start()
        scheduling_controller.start()
        thermostat_controller.start()
        metrics_collector.start()
        web_service.start()
        gateway_api.start()
        plugin_controller.start()
        communication_led_controller.start()
        event_sender.start()

        signal_request = {'stop': False}

        def stop(signum, frame):
            """ This function is called on SIGTERM. """
            _ = signum, frame
            logger.info('Stopping OM core service...')
            master_controller.stop()
            maintenance_controller.stop()
            web_service.stop()
            metrics_collector.stop()
            metrics_controller.stop()
            thermostat_controller.stop()
            plugin_controller.stop()
            event_sender.stop()
            logger.info('Stopping OM core service... Done')
            signal_request['stop'] = True

        signal(SIGTERM, stop)
        logger.info('Starting OM core service... Done')
        while not signal_request['stop']:
            time.sleep(1)


if __name__ == "__main__":
    setup_logger()

    logger.info("Applying migrations")
    # Run all unapplied migrations
    db = Database.get_db()
    router = Router(db, migrate_dir='/opt/openmotics/python/migrations')
    router.run()

    logger.info("Starting OpenMotics service")
    # TODO: move message service to separate process
    message_service = MessageService()
    message_service.start()

    OpenmoticsService.build_graph()
    OpenmoticsService.fix_dependencies()
    OpenmoticsService.start()
