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
from __future__ import absolute_import

import argparse
import logging

from openmotics_cli import service

logger = logging.getLogger('openmotics')


@service('openmotics_service')
def openmotics_service(args):
    # type: (argparse.Namespace) -> None
    logger.info('Starting OpenMotics service')

    # TODO: move message service to separate process
    from bus.om_bus_service import MessageService
    message_service = MessageService()
    message_service.start()

    from gateway.services.openmotics import OpenmoticsService
    OpenmoticsService.fix_dependencies()
    OpenmoticsService.start()


def main():
    # type: () -> None
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    openmotics_service(args)


if __name__ == '__main__':
    main()
