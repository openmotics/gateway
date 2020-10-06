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

import os
import sys

from ioc import INJECTED, Inject, Injectable

if False:  # MYPY
    import argparse

# Defaults
OPT_OPENMOTICS = '/opt/openmotics'
SITE_PACKAGES = 'lib/python{0}.{1}/site-packages'.format(sys.version_info.major,
                                                         sys.version_info.minor)


class Settings(object):
    def __init__(self, args):
        # type: (argparse.Namespace) -> None
        self.python_dir = args.python_dir

    @staticmethod
    @Inject
    def get_python_site_packages(settings=INJECTED):
        # type: (Settings) -> str
        return os.path.join(settings.python_dir, SITE_PACKAGES)


def setup_global_arguments(parser):
    # type: (argparse.ArgumentParser) -> None
    parser.add_argument('--python-dir',
                        default=os.path.join(OPT_OPENMOTICS, 'python-deps'),
                        help='Location of python dependencies')


def setup_settings(args):
    # type: (argparse.Namespace) -> None
    Injectable.value(settings=Settings(args))
