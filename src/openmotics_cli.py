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
import functools
import logging
import os

import constants
from gateway.settings import setup_global_arguments, setup_settings
from ioc import INJECTED, Inject
from platform_utils import System

logger = logging.getLogger('openmotics')


parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()

operator_parser = subparsers.add_parser('operator')
operator_subparsers = operator_parser.add_subparsers()


def setup_logger():
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)


def setup_decorator(setup):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(args, **kwargs):
            setup_settings(args)
            setup_logger()
            System.import_libs()
            setup()
            return Inject(f)(args, **kwargs)
        return wrapper
    return decorator


def settings():
    """
    Wrap a command function with settings.
    """
    def setup():
        pass
    return setup_decorator(setup)


def platform(name):
    """
    Wrap a command function with setup_platform and injections.
    """
    def setup():
        from gateway.initialize import setup_platform
        setup_platform(name)
    return setup_decorator(setup)


def minimal_master():
    """
    Wrap a command function a minimal master platform and injections.
    """
    def setup():
        from gateway.initialize import setup_minimal_master_platform
        setup_minimal_master_platform()
    return setup_decorator(setup)


def minimal_power():
    """
    Wrap a command function a minimal power platform and injections.
    """
    def setup():
        from gateway.initialize import setup_minimal_power_platform
        setup_minimal_power_platform()
    return setup_decorator(setup)


def service(name):
    """
    Wrap a command function with setup_service and injections.
    """
    def setup():
        from gateway.initialize import setup_service
        setup_service(name)
    return setup_decorator(setup)


# Commands


def cmd_version(args):
    import gateway
    print(gateway.__version__)


version_parser = subparsers.add_parser('version')
version_parser.set_defaults(cmd=cmd_version)


@settings()
def cmd_factory_reset(args):
    lock_file = constants.get_init_lockfile()
    if os.path.isfile(lock_file) and not args.force:
        print('already_in_progress')
        exit(1)
    with open(lock_file, 'w') as fd:
        fd.write('factory_reset')


factory_reset_parser = operator_subparsers.add_parser('factory-reset')
factory_reset_parser.add_argument('--force', action='store_true')
factory_reset_parser.set_defaults(cmd=cmd_factory_reset)


@platform('openmotics_shell')
def cmd_shell(args, gateway_api=INJECTED):
    _ = args
    import IPython
    IPython.embed(header='''
    Use `gateway_api` to interact with the gateway.
    ''')


shell_parser = operator_subparsers.add_parser('shell')
shell_parser.set_defaults(cmd=cmd_shell)


def main():
    setup_global_arguments(parser)
    args = parser.parse_args()
    args.cmd(args)


if __name__ == '__main__':
    main()
