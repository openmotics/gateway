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

from platform_utils import Platform, System
System.import_libs()

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from threading import Lock

from peewee_migrate import Router
from serial import Serial
from six.moves.configparser import ConfigParser, NoOptionError
from six.moves.urllib.parse import urlparse, urlunparse

import constants
from bus.om_bus_client import MessageClient
from gateway.hal.frontpanel_controller_classic import FrontpanelClassicController
from gateway.hal.frontpanel_controller_core import FrontpanelCoreController
from gateway.hal.master_controller_classic import MasterClassicController
from gateway.hal.master_controller_core import MasterCoreController
from gateway.hal.master_controller_dummy import MasterDummyController
from gateway.models import Database, Feature
from gateway.thermostat.gateway.thermostat_controller_gateway import \
    ThermostatControllerGateway
from gateway.thermostat.master.thermostat_controller_master import \
    ThermostatControllerMaster
from ioc import INJECTED, Inject, Injectable
from master.classic.maintenance import MaintenanceClassicCommunicator
from master.classic.master_communicator import MasterCommunicator
from master.core.core_communicator import CoreCommunicator
from master.core.maintenance import MaintenanceCoreCommunicator
from master.core.memory_file import MemoryFile, MemoryTypes
from power.power_communicator import PowerCommunicator
from power.power_controller import P1Controller, PowerController
from power.power_store import PowerStore
from serial_utils import RS485


if False:  # MYPY
    from typing import Any, Optional
    from gateway.hal.master_controller import MasterController

logger = logging.getLogger('openmotics')


def initialize(message_client_name):
    # type: (Optional[str]) -> None
    logger.info('Initializing')
    init_lock = constants.get_init_lockfile()
    logger.info('Waiting for lock')
    with lock_file(init_lock) as fd:
        content = fd.read()
        apply_migrations()
        setup_platform(message_client_name)
        if content == '':
            logger.info('Initializing, done')
        elif content == 'factory_reset':
            logger.info('Running factory reset...')
            factory_reset()
            logger.info('Running factory reset, done')
        else:
            logger.warning('unknown initialization {}'.format(content))


@contextmanager
def lock_file(file):
    # type: (str) -> Any
    with open(file, 'a+') as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
            os.unlink(file)
        except Exception:
            fcntl.flock(fd, fcntl.LOCK_UN)
            raise


def apply_migrations():
    # type: () -> None
    logger.info('Applying migrations')
    # Run all unapplied migrations
    db = Database.get_db()
    gateway_src = os.path.abspath(os.path.join(__file__, '..'))
    router = Router(db, migrate_dir=os.path.join(gateway_src, 'migrations/orm'))
    router.run()


@Inject
def factory_reset(master_controller=INJECTED):
    # type: (MasterController) -> None
    import glob
    import shutil

    logger.info('Rebooting master...')
    master_controller.cold_reset()
    time.sleep(6)

    logger.info('Wiping master eeprom...')
    master_controller.start()
    master_controller.factory_reset()
    master_controller.stop()

    logger.info('Removing databases...')
    # Delete databases.
    for f in constants.get_all_database_files():
        if os.path.exists(f):
            os.remove(f)

    # Delete plugins
    logger.info('Removing plugins...')
    plugin_dir = constants.get_plugin_dir()
    plugins = [name for name in os.listdir(plugin_dir)
               if os.path.isdir(os.path.join(plugin_dir, name))]
    for plugin in plugins:
        shutil.rmtree(plugin_dir + plugin)

    config_files = constants.get_plugin_configfiles()
    for config_file in glob.glob(config_files):
        os.remove(config_file)


def setup_platform(message_client_name):
    # type: (Optional[str]) -> None
    setup_target_platform(Platform.get_platform(), message_client_name)


def setup_target_platform(target_platform, message_client_name):
    # type: (str, Optional[str]) -> None
    config = ConfigParser()
    config.read(constants.get_config_file())

    config_lock = Lock()
    metrics_lock = Lock()

    config_database_file = constants.get_config_database_file()

    # Debugging options
    try:
        debug_logger = config.get('OpenMotics', 'debug_logger')
        if debug_logger:
            logging.getLogger(debug_logger).setLevel(logging.DEBUG)
    except NoOptionError:
        pass

    # Webserver / Presentation layer
    try:
        https_port = int(config.get('OpenMotics', 'https_port'))
    except NoOptionError:
        https_port = 443
    try:
        http_port = int(config.get('OpenMotics', 'http_port'))
    except NoOptionError:
        http_port = 80
    Injectable.value(https_port=https_port)
    Injectable.value(http_port=http_port)
    Injectable.value(ssl_private_key=constants.get_ssl_private_key_file())
    Injectable.value(ssl_certificate=constants.get_ssl_certificate_file())

    # TODO: Clean up dependencies more to reduce complexity

    # IOC announcements
    # When below modules are imported, the classes are registerd in the IOC graph. This is required for
    # instances that are used in @Inject decorated functions below, and is also needed to specify
    # abstract implementations depending on e.g. the platform (classic vs core) or certain settings (classic
    # thermostats vs gateway thermostats)
    from plugins import base
    from gateway import (metrics_controller, webservice, scheduling, observer, gateway_api, metrics_collector,
                         maintenance_controller, user_controller, pulse_counter_controller,
                         metrics_caching, watchdog, output_controller, room_controller, sensor_controller,
                         shutter_controller, group_action_controller, module_controller, ventilation_controller)
    from cloud import events
    _ = (metrics_controller, webservice, scheduling, observer, gateway_api, metrics_collector,
         maintenance_controller, base, events, user_controller,
         pulse_counter_controller, metrics_caching, watchdog, output_controller, room_controller,
         sensor_controller, shutter_controller, group_action_controller, module_controller, ventilation_controller)

    # IPC
    message_client = None
    if message_client_name is not None:
        message_client = MessageClient(message_client_name)
    Injectable.value(message_client=message_client)

    # Cloud API
    Injectable.value(gateway_uuid=config.get('OpenMotics', 'uuid'))

    try:
        parsed_url = urlparse(config.get('OpenMotics', 'vpn_check_url'))
    except NoOptionError:
        parsed_url = urlparse('')
    Injectable.value(cloud_endpoint=parsed_url.hostname)
    Injectable.value(cloud_port=parsed_url.port)
    Injectable.value(cloud_ssl=parsed_url.scheme == 'https')
    Injectable.value(cloud_api_version=0)

    cloud_url = urlunparse((parsed_url.scheme, parsed_url.netloc, '', '', '', ''))
    Injectable.value(cloud_url=cloud_url or None)

    # User Controller
    Injectable.value(user_db=config_database_file)
    Injectable.value(user_db_lock=config_lock)
    Injectable.value(token_timeout=3600)
    Injectable.value(config={'username': config.get('OpenMotics', 'cloud_user'),
                             'password': config.get('OpenMotics', 'cloud_pass')})

    # Metrics Controller
    Injectable.value(metrics_db=constants.get_metrics_database_file())
    Injectable.value(metrics_db_lock=metrics_lock)

    # Energy Controller
    try:
        power_serial_port = config.get('OpenMotics', 'power_serial')
    except NoOptionError:
        power_serial_port = ''
    if power_serial_port:
        Injectable.value(power_db=constants.get_power_database_file())
        Injectable.value(power_store=PowerStore())
        # TODO: make non blocking?
        Injectable.value(power_serial=RS485(Serial(power_serial_port, 115200, timeout=None)))
        Injectable.value(power_communicator=PowerCommunicator())
        Injectable.value(power_controller=PowerController())
        Injectable.value(p1_controller=P1Controller())
    else:
        Injectable.value(power_serial=None)
        Injectable.value(power_store=None)
        Injectable.value(power_communicator=None)  # TODO: remove from gateway_api
        Injectable.value(power_controller=None)
        Injectable.value(p1_controller=None)

    # Pulse Controller
    Injectable.value(pulse_db=constants.get_pulse_counter_database_file())

    # Master Controller
    try:
        controller_serial_port = config.get('OpenMotics', 'controller_serial')
    except NoOptionError:
        controller_serial_port = ''

    if controller_serial_port:
        Injectable.value(controller_serial=Serial(controller_serial_port, 115200, exclusive=True))
    if target_platform in [Platform.Type.DUMMY, Platform.Type.ESAFE]:
        Injectable.value(maintenance_communicator=None)
        Injectable.value(passthrough_service=None)
        Injectable.value(master_controller=MasterDummyController())
    elif target_platform in Platform.CoreTypes:
        # FIXME don't create singleton for optional controller?
        from master.core import ucan_communicator, slave_communicator
        _ = ucan_communicator, slave_communicator
        core_cli_serial_port = config.get('OpenMotics', 'cli_serial')
        Injectable.value(cli_serial=Serial(core_cli_serial_port, 115200))
        Injectable.value(passthrough_service=None)  # Mark as "not needed"
        # TODO: Remove; should not be needed for Core
        Injectable.value(eeprom_db=constants.get_eeprom_extension_database_file())

        Injectable.value(master_communicator=CoreCommunicator())
        Injectable.value(maintenance_communicator=MaintenanceCoreCommunicator())
        Injectable.value(memory_files={MemoryTypes.EEPROM: MemoryFile(MemoryTypes.EEPROM),
                                       MemoryTypes.FRAM: MemoryFile(MemoryTypes.FRAM)})
        Injectable.value(master_controller=MasterCoreController())
    elif target_platform in Platform.ClassicTypes:
        # FIXME don't create singleton for optional controller?
        from master.classic import eeprom_extension
        _ = eeprom_extension
        leds_i2c_address = config.get('OpenMotics', 'leds_i2c_address')
        passthrough_serial_port = config.get('OpenMotics', 'passthrough_serial')
        Injectable.value(eeprom_db=constants.get_eeprom_extension_database_file())
        Injectable.value(leds_i2c_address=int(leds_i2c_address, 16))
        if passthrough_serial_port:
            Injectable.value(passthrough_serial=Serial(passthrough_serial_port, 115200))
            from master.classic.passthrough import PassthroughService
            _ = PassthroughService  # IOC announcement
        else:
            Injectable.value(passthrough_service=None)
        Injectable.value(master_communicator=MasterCommunicator())
        Injectable.value(maintenance_communicator=MaintenanceClassicCommunicator())
        Injectable.value(master_controller=MasterClassicController())
    else:
        logger.warning('Unhandled master implementation for %s', target_platform)

    if target_platform in [Platform.Type.DUMMY, Platform.Type.ESAFE]:
        Injectable.value(frontpanel_controller=None)
    elif target_platform in Platform.CoreTypes:
        Injectable.value(frontpanel_controller=FrontpanelCoreController())
    elif target_platform in Platform.ClassicTypes:
        Injectable.value(frontpanel_controller=FrontpanelClassicController())
    else:
        logger.warning('Unhandled frontpanel implementation for %s', target_platform)

    # Thermostats
    thermostats_gateway_feature = Feature.get_or_none(name='thermostats_gateway')
    thermostats_gateway_enabled = thermostats_gateway_feature is not None and thermostats_gateway_feature.enabled
    if target_platform not in Platform.ClassicTypes or thermostats_gateway_enabled:
        Injectable.value(thermostat_controller=ThermostatControllerGateway())
    else:
        Injectable.value(thermostat_controller=ThermostatControllerMaster())


def setup_minimal_vpn_platform(message_client_name):
    # type: (str) -> None
    # IPC
    message_client = None
    if message_client_name is not None:
        message_client = MessageClient(message_client_name)
    Injectable.value(message_client=message_client)


def setup_minimal_master_platform(port):
    # type: (str) -> None
    config = ConfigParser()
    config.read(constants.get_config_file())

    platform = Platform.get_platform()
    Injectable.value(controller_serial=Serial(port, 115200))

    if platform == Platform.Type.DUMMY:
        Injectable.value(maintenance_communicator=None)
        Injectable.value(master_controller=MasterDummyController())
    elif platform in Platform.CoreTypes:
        from master.core import ucan_communicator
        _ = ucan_communicator
        core_cli_serial_port = config.get('OpenMotics', 'cli_serial')
        Injectable.value(cli_serial=Serial(core_cli_serial_port, 115200))
        Injectable.value(master_communicator=CoreCommunicator())
        Injectable.value(maintenance_communicator=None)
        Injectable.value(memory_files={MemoryTypes.EEPROM: MemoryFile(MemoryTypes.EEPROM),
                                       MemoryTypes.FRAM: MemoryFile(MemoryTypes.FRAM)})
        Injectable.value(master_controller=MasterCoreController())
    elif platform in Platform.ClassicTypes:
        Injectable.value(eeprom_db=constants.get_eeprom_extension_database_file())
        from master.classic import eeprom_extension
        _ = eeprom_extension
        Injectable.value(master_communicator=MasterCommunicator())
        Injectable.value(maintenance_communicator=None)
        Injectable.value(master_controller=MasterClassicController())
    else:
        logger.warning('Unhandled master implementation for %s', platform)


def setup_minimal_power_platform():
    # type: () -> None
    config = ConfigParser()
    config.read(constants.get_config_file())
    power_serial_port = config.get('OpenMotics', 'power_serial')
    if power_serial_port:
        Injectable.value(power_db=constants.get_power_database_file())
        Injectable.value(power_store=PowerStore())
        Injectable.value(power_serial=RS485(Serial(power_serial_port, 115200, timeout=None)))
        Injectable.value(power_communicator=PowerCommunicator())
        Injectable.value(power_controller=PowerController())
        Injectable.value(p1_controller=P1Controller())
    else:
        Injectable.value(power_store=None)
        Injectable.value(power_communicator=None)
        Injectable.value(power_controller=None)
        Injectable.value(p1_controller=None)
        Injectable.value(power_serial=None)
