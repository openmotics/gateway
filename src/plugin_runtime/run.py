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
import os
import sys

# TODO setup load paths properly
sys.path.insert(0, os.path.abspath(os.path.join(__file__, '..', '..')))

from gateway.settings import setup_global_arguments
from openmotics_cli import settings


logger = logging.getLogger('update.py')


@settings()
def cmd_start(args):
    # type: (argparse.Namespace) -> None
    from plugin_runtime.runtime import start_runtime
    start_runtime(args.plugin_path)


def main():
    # type: () -> None
    parser = argparse.ArgumentParser()
    parser.add_argument('command')
    parser.add_argument('plugin_path')
    setup_global_arguments(parser)

    args = parser.parse_args()
    if args.command != 'start':
        parser.print_help()
        os._exit(1)

    cmd_start(args)


if __name__ == '__main__':
    main()
