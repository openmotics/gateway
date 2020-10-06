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

import constants
from gateway.tools.update import update

logger = logging.getLogger('update.py')


def setup_update_output():
    logging.basicConfig(level=logging.INFO, filemode='w', format='%(message)s',
                        filename=constants.get_update_output_file())
    logger.setLevel(logging.DEBUG)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    parser.add_argument('md5', nargs='?')
    args = parser.parse_args()

    setup_update_output()
    update(args)


if __name__ == '__main__':
    main()
