#!/usr/bin/env python
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
"""
Module to work update a Core
"""

from __future__ import absolute_import

import argparse

from serial import Serial
from six.moves.configparser import ConfigParser

import constants
from gateway.settings import setup_global_arguments
from ioc import Injectable
from openmotics_cli import settings


@settings()
def cmd_update(args):
    # type: (argparse.Namespace) -> None
    config = ConfigParser()
    config.read(constants.get_config_file())
    core_cli_serial_port = config.get('OpenMotics', 'cli_serial')
    Injectable.value(cli_serial=Serial(core_cli_serial_port, 115200))
    Injectable.value(master_communicator=None)
    Injectable.value(maintenance_communicator=None)

    from master.core.core_updater import CoreUpdater
    CoreUpdater.update(hex_filename=args.firmware_filename)


def main():
    # type: () -> None
    parser = argparse.ArgumentParser()
    parser.add_argument('firmware_filename')
    setup_global_arguments(parser)

    args = parser.parse_args()
    cmd_update(args)


if __name__ == '__main__':
    main()
