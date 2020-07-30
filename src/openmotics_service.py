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
from __future__ import absolute_import

from platform_utils import System
System.import_libs()

import logging
import time
from signal import SIGTERM, signal

from bus.om_bus_client import MessageClient
from bus.om_bus_service import MessageService
from gateway.initialize import initialize
from gateway.migrations.rooms import RoomsMigrator
from ioc import INJECTED, Inject


if False:  # MYPY
    from gateway.metrics_controller import MetricsController
    from gateway.metrics_collector import MetricsCollector
    from gateway.webservice import WebInterface, WebService
    from gateway.scheduling import SchedulingController
    from gateway.observer import Observer
    from gateway.gateway_api import GatewayApi
    from gateway.maintenance_controller import MaintenanceController
    from gateway.thermostat.thermostat_controller import ThermostatController
    from gateway.shutter_controller import ShutterController
    from gateway.output_controller import OutputController
    from gateway.input_controller import InputController
    from gateway.sensor_controller import SensorController
    from gateway.pulse_counter_controller import PulseCounterController
    from gateway.group_action_controller import GroupActionController
    from gateway.watchdog import Watchdog
    from gateway.module_controller import ModuleController
    from gateway.comm_led_controller import CommunicationLedController
    from gateway.hal.master_controller import MasterController
    from gateway.hal.frontpanel_controller import FrontpanelController
    from plugins.base import PluginController
    from power.power_communicator import PowerCommunicator
    from master.classic.passthrough import PassthroughService
    from cloud.events import EventSender
    from serial_utils import RS485

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
    @Inject
    def fix_dependencies(
                metrics_controller=INJECTED,  # type: MetricsController
                message_client=INJECTED,  # type: MessageClient
                web_interface=INJECTED,  # type: WebInterface
                scheduling_controller=INJECTED,  # type: SchedulingController
                observer=INJECTED,  # type: Observer
                gateway_api=INJECTED,  # type: GatewayApi
                metrics_collector=INJECTED,  # type: MetricsCollector
                plugin_controller=INJECTED,  # type: PluginController
                web_service=INJECTED,  # type: WebService
                event_sender=INJECTED,  # type: EventSender
                maintenance_controller=INJECTED,  # type: MaintenanceController
                output_controller=INJECTED,  # type: OutputController
                thermostat_controller=INJECTED,  # type: ThermostatController
                shutter_controller=INJECTED,  # type: ShutterController
                frontpanel_controller=INJECTED  # type: FrontpanelController
            ):

        # TODO: Fix circular dependencies
        # TODO: Introduce some kind of generic event/message bus

        thermostat_controller.subscribe_events(web_interface.send_event_websocket)
        thermostat_controller.subscribe_events(event_sender.enqueue_event)
        thermostat_controller.subscribe_events(plugin_controller.process_observer_event)
        message_client.add_event_handler(metrics_controller.event_receiver)
        message_client.add_event_handler(frontpanel_controller.event_receiver)
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
        maintenance_controller.subscribe_maintenance_stopped(gateway_api.maintenance_mode_stopped)
        output_controller.subscribe_events(metrics_collector.process_observer_event)
        output_controller.subscribe_events(plugin_controller.process_observer_event)
        output_controller.subscribe_events(web_interface.send_event_websocket)
        output_controller.subscribe_events(event_sender.enqueue_event)
        # TODO: remove observer inputs
        observer.subscribe_events(metrics_collector.process_observer_event)
        observer.subscribe_events(plugin_controller.process_observer_event)
        observer.subscribe_events(web_interface.send_event_websocket)
        observer.subscribe_events(event_sender.enqueue_event)
        shutter_controller.subscribe_events(metrics_collector.process_observer_event)
        shutter_controller.subscribe_events(plugin_controller.process_observer_event)
        shutter_controller.subscribe_events(web_interface.send_event_websocket)
        shutter_controller.subscribe_events(event_sender.enqueue_event)

    @staticmethod
    @Inject
    def start(
                master_controller=INJECTED,  # type: MasterController
                maintenance_controller=INJECTED,  # type: MaintenanceController
                power_communicator=INJECTED,  # type: PowerCommunicator
                power_serial=INJECTED,  # type: RS485
                metrics_controller=INJECTED,  # type: MetricsController
                passthrough_service=INJECTED,  # type: PassthroughService
                scheduling_controller=INJECTED,  # type: SchedulingController
                metrics_collector=INJECTED,  # type: MetricsCollector
                web_service=INJECTED,  # type: WebService
                web_interface=INJECTED,  # type: WebInterface
                watchdog=INJECTED,  # type: Watchdog
                plugin_controller=INJECTED,  # type: PluginController
                communication_led_controller=INJECTED,  # type: CommunicationLedController
                event_sender=INJECTED,  # type: EventSender
                thermostat_controller=INJECTED,  # type: ThermostatController
                output_controller=INJECTED,  # type: OutputController
                input_controller=INJECTED,  # type: InputController
                pulse_counter_controller=INJECTED,  # type: PulseCounterController
                sensor_controller=INJECTED,  # type: SensorController
                shutter_controller=INJECTED,  # type: ShutterController
                group_action_controller=INJECTED,  # type: GroupActionController
                frontpanel_controller=INJECTED,  # type: FrontpanelController
                module_controller=INJECTED  # type: ModuleController
            ):
        """ Main function. """
        logger.info('Starting OM core service...')

        # MasterController should be running
        master_controller.start()

        # Sync ORM with sources of thruth
        output_controller.run_sync_orm()
        input_controller.run_sync_orm()
        pulse_counter_controller.run_sync_orm()
        sensor_controller.run_sync_orm()
        shutter_controller.run_sync_orm()

        # Execute master migration(s)
        RoomsMigrator.migrate()

        # Start rest of the stack
        maintenance_controller.start()
        if power_communicator:
            power_serial.start()
            power_communicator.start()
        metrics_controller.start()
        if passthrough_service:
            passthrough_service.start()
        scheduling_controller.start()
        module_controller.start()
        thermostat_controller.start()
        metrics_collector.start()
        web_service.start()
        frontpanel_controller.start()
        communication_led_controller.start()
        event_sender.start()
        watchdog.start()
        plugin_controller.start()
        output_controller.start()
        input_controller.start()
        pulse_counter_controller.start()
        sensor_controller.start()
        shutter_controller.start()
        group_action_controller.start()

        web_interface.set_service_state(True)
        signal_request = {'stop': False}

        def stop(signum, frame):
            """ This function is called on SIGTERM. """
            _ = signum, frame
            logger.info('Stopping OM core service...')
            watchdog.stop()
            output_controller.stop()
            input_controller.stop()
            pulse_counter_controller.stop()
            sensor_controller.stop()
            shutter_controller.stop()
            group_action_controller.stop()
            web_service.stop()
            if power_communicator:
                power_communicator.stop()
            master_controller.stop()
            maintenance_controller.stop()
            metrics_collector.stop()
            metrics_controller.stop()
            thermostat_controller.stop()
            plugin_controller.stop()
            communication_led_controller.stop()
            frontpanel_controller.stop()
            event_sender.stop()
            logger.info('Stopping OM core service... Done')
            signal_request['stop'] = True

        signal(SIGTERM, stop)
        logger.info('Starting OM core service... Done')
        while not signal_request['stop']:
            time.sleep(1)


if __name__ == "__main__":
    setup_logger()
    initialize(message_client_name='openmotics_service')

    logger.info("Starting OpenMotics service")
    # TODO: move message service to separate process
    message_service = MessageService()
    message_service.start()

    OpenmoticsService.fix_dependencies()
    OpenmoticsService.start()
