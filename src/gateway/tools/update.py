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
""" The update modules provides the update functionality. """

from __future__ import absolute_import

from platform_utils import Platform, System
System.import_libs()

import fcntl
import glob
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

import requests
from six.moves.configparser import ConfigParser, NoOptionError
from six.moves.urllib.parse import urlparse, urlunparse

import constants

logging.basicConfig(level=logging.INFO, filemode='w', format='%(message)s', filename=constants.get_update_output_file())
logger = logging.getLogger('update.py')
logger.setLevel(logging.DEBUG)

PREFIX = '/opt/openmotics'
SUPERVISOR_SERVICES = ('openmotics', 'vpn_service')
FIRMWARE_FILES = {'gateway_service': 'gateway.tgz',
                  'gateway_frontend': 'gateway_frontend.tgz',
                  'gateway_os': 'gateway_os.tgz',
                  'master_classic': 'm_classic_firmware.hex',
                  'master_coreplus': 'm_core_firmware.hex',
                  'power': 'p_firmware.hex',
                  'energy': 'e_firmware.hex',
                  'can': 'c_firmware.hex',
                  'output': 'o_firmware.hex',
                  'shutter': 'r_firmware.hex',
                  'input': 'i_firmware.hex',
                  'dimmer': 'd_firmware.hex',
                  'temperature': 't_firmware.hex',
                  'can_gen3': 'c_firmware_gen3.hex',
                  'output_gen3': 'o_firmware_gen3.hex',
                  'shutter_gen3': 'r_firmware_gen3.hex',
                  'input_gen3': 'i_firmware_gen3.hex',
                  'dimmer_gen3': 'd_firmware_gen3.hex',
                  'temperature_gen3': 't_firmware_gen3.hex',
                  'ucan': 'uc_firmware.hex'}
MODULE_TYPES = {'can': 'c',
                'output': 'o',
                'shutter': 'r',
                'input': 'i',
                'dimmer': 'd',
                'temperature': 't',
                'can_gen3': 'c3',
                'output_gen3': 'o3',
                'shutter_gen3': 'r3',
                'input_gen3': 'i3',
                'dimmer_gen3': 'd3',
                'temperature_gen3': 't3',
                'ucan': 'uc'}


def cmd(command, **kwargs):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    output, ret = cmd_wait_output(proc)
    if ret != 0:
        raise Exception('Command {} failed'.format(command))
    return output


def cmd_wait_output(proc):
    output = ''
    for line in proc.stdout:
        if line:
            logger.debug(line.rstrip('\n'))
        output += line
    ret = proc.wait()
    return output, ret != 0


def extract_legacy_update(update_file, expected_md5):
    md5_hasher = hashlib.md5()
    with open(update_file, 'rb') as fd:
        for chunk in iter(lambda: fd.read(128 * md5_hasher.block_size), ''):
            md5_hasher.update(chunk)
    calculated_md5 = md5_hasher.hexdigest()
    if calculated_md5 != expected_md5:
        raise ValueError('update.tgz md5:%s does not match expected md5:%s' % (calculated_md5, expected_md5))

    cmd(['tar', 'xzf', update_file])


def fetch_metadata(config, version, expected_md5):
    """
    Example metadata:
    {'version': '1.2.3',
     'firmwares': [{'type': 'master_coreplus',
                    'version': '3.12.3'
                    'dependencies': ['gateway_service >= 3.1.1'],
                    'sha256': 'abcdef',
                    'url': 'https://foo.bar/master-coreplus_3.12.3.hex'})
    """
    response = requests.get(get_metadata_url(config, version), timeout=2)
    if response.status_code != 200:
        raise ValueError('failed to get update metadata')
    hasher = hashlib.md5()
    hasher.update(response.content)
    calculated_md5 = hasher.hexdigest()
    if expected_md5 != calculated_md5:
        logger.error(response.content)
        raise ValueError('update metadata md5:%s does not match expected md5:%s' % (calculated_md5, expected_md5))
    return response.json()


def get_metadata_url(config, version):
    gateway_uuid = config.get('OpenMotics', 'uuid')
    try:
        uri = urlparse(config.get('OpenMotics', 'update_url'))
    except NoOptionError:
        path = '/api/v1/base/updates/metadata'
        vpn_uri = urlparse(config.get('OpenMotics', 'vpn_check_url'))
        uri = urlunparse((vpn_uri.scheme, vpn_uri.netloc, path, '', '', ''))
    path = '{}/{}'.format(uri.path, version)
    query = 'uuid={0}'.format(gateway_uuid)
    return urlunparse((uri.scheme, uri.netloc, path, '', query, ''))


def get_master_type():
    if Platform.get_platform() == Platform.Type.CORE_PLUS:
        return 'master_coreplus'
    else:
        return 'master_classic'


def download_firmware(firmware_type, url, expected_sha256):
    response = requests.get(url, stream=True, timeout=60)
    firmware_file = FIRMWARE_FILES[firmware_type]
    logger.info('Downloading {}...'.format(firmware_file))
    with open(firmware_file, 'wb') as f:
        shutil.copyfileobj(response.raw, f)

    hasher = hashlib.sha256()
    with open(firmware_file, 'rb') as f:
        hasher.update(f.read())
    calculated_sha256 = hasher.hexdigest()
    if expected_sha256 != calculated_sha256:
        raise ValueError('firmware %s sha256:%s does not match expected sha256:%s' % (firmware_file, calculated_sha256, expected_sha256))


def check_services():
    for service in SUPERVISOR_SERVICES:
        status_output, _ = cmd_wait_output(System.run_service_action('status', service))
        if 'no such process' in status_output.decode('utf-8').lower():
            raise Exception('Could not find service "{}"'.format(service))


def stop_services():
    for service in SUPERVISOR_SERVICES:
        proc = System.run_service_action('stop', service)
        proc.wait()


def start_services():
    for service in SUPERVISOR_SERVICES:
        try:
            cmd_wait_output(System.run_service_action('start', service))
        except Exception:
            logger.warning('Starting {} failed'.format(service))


def check_gateway_health(timeout=60):
    since = time.time()
    pending = ['unknown']
    while since > time.time() - timeout:
        try:
            response = requests.get('http://127.0.0.1/health_check', timeout=2)
            data = response.json()
            if data['success']:
                pending = [k for k, v in data['health'].items() if not v['state']]
                if not pending:
                    return
        except Exception:
            pass
        time.sleep(10)
    logger.error('health check failed {}'.format(pending))
    raise Exception('Gateway services failed to start')


def is_up_to_date(name, new_version):
    current_version = None
    current_version_filename = os.path.join(PREFIX, 'etc/{0}.version'.format(name))
    try:
        if os.path.exists:
            with open(current_version_filename, 'r') as current_version_file:
                current_version = current_version_file.read().strip()
    except Exception:
        return False  # If the current version can't be loaded just update it
    if current_version == new_version:
        logger.info('Skipping already up-to-date {0}'.format(name))
        return True
    return False


def mark_installed_version(name, version):
    current_version_filename = os.path.join(PREFIX, 'etc/{0}.version'.format(name))
    with open(current_version_filename, 'w+') as current_version_file:
        current_version_file.write('{0}\n'.format(version))


def check_master_communication():
    master_tool = os.path.join(PREFIX, 'python/master_tool.py')
    cmd(['python', master_tool, '--reset'])
    try:
        cmd(['python', master_tool, '--sync'])
    except Exception:
        time.sleep(2)
        try:
            cmd(['python', master_tool, '--sync'])
        except Exception:
            logger.info('No communication, resetting master')
            cmd(['python', master_tool, '--hard-reset'])
            time.sleep(2)
            try:
                cmd(['python', master_tool, '--sync'])
            except Exception:
                time.sleep(2)
    finally:
        cmd(['python', master_tool, '--sync'])


def update_master_firmware(master_type, hexfile, version):
    master_tool = os.path.join(PREFIX, 'python/master_tool.py')
    arguments = []
    if master_type == 'master_classic':
        arguments += ['--master-firmware-classic', hexfile]
    elif master_type == 'master_coreplus':
        arguments += ['--master-firmware-core', hexfile]
    try:
        output = subprocess.check_output(['python', master_tool, '--version'])
        current_version, _, _ = output.decode('utf-8').rstrip().partition(' ')
        if current_version == version:
            logger.info('Master is already v{}, skipped'.format(version))
        else:
            logger.info('Master {} -> {}'.format(current_version, version if version else 'unknown'))
            cmd(['python', master_tool, '--update'] + arguments)
            cmd(['cp', hexfile, os.path.join(PREFIX, 'firmware.hex')])
    except Exception as exc:
        logger.exception('Updating Master firmware failed')
        return exc


def update_energy_firmware(module, hexfile, version, arguments):
    power_bootloader = os.path.join(PREFIX, 'python/power_bootloader.py')
    try:
        if version:
            arguments += ['--version', version]
        cmd(['python', power_bootloader, '--all', '--file', hexfile] + arguments)
        cmd(['cp', hexfile, os.path.join(PREFIX, os.path.basename(hexfile))])
    except Exception as exc:
        logger.exception('Updating {} firmware failed'.format(module))
        return exc


def update_module_firmware(module, hexfile, version):
    check_master_communication()
    modules_bootloader = os.path.join(PREFIX, 'python/modules_bootloader.py')
    try:
        arguments = []
        if version:
            arguments += ['--version', version]
        cmd(['python', modules_bootloader, '-t', MODULE_TYPES[module], '-f', hexfile] + arguments)
        cmd(['cp', hexfile, os.path.join(PREFIX, os.path.basename(hexfile))])
    except Exception as exc:
        logger.exception('Updating {} firmware failed'.format(module))
        return exc


def update_gateway_os(tarball, version):
    try:
        cmd(['mount', '-o', 'remount,rw', '/'])
        cmd(['tar', '-xz', '--no-same-owner', '-f', tarball, '-C', '/'])
        cmd(['sync'])
        cmd(['bash', '/usr/bin/os_update.sh'])
        mark_installed_version('gateway_os', version)
    except Exception as exc:
        logger.exception('Updating Gateway OS failed')
        return exc
    finally:
        cmd(['mount', '-o', 'remount,ro', '/'])


def update_gateway_backend(tarball, date, version):
    try:
        backup_dir = os.path.join(PREFIX, 'backup')
        python_dir = os.path.join(PREFIX, 'python')
        etc_dir = os.path.join(PREFIX, 'etc')
        cmd(['mkdir', '-p', backup_dir])
        cmd('ls -tp | grep "/$" | tail -n +3 | while read file; do rm -r $file; done', shell=True, cwd=backup_dir)

        # TODO: symlink, blue green deployment
        cmd(['mkdir', '-p', os.path.join(backup_dir, date)])
        cmd(['mv', python_dir, os.path.join(backup_dir, date)])
        cmd(['cp', '-r', etc_dir, os.path.join(backup_dir, date)])

        # Cleanup for old versions.
        old_dist_dir = os.path.join(PREFIX, 'dist-packages')
        if os.path.exists(old_dist_dir):
            cmd(['mv', old_dist_dir, os.path.join(backup_dir, date)])

        logger.info('Extracting gateway')
        cmd(['mkdir', '-p', python_dir])
        cmd(['tar', '-v', '-xzf', tarball, '-C', python_dir])
        cmd(['sync'])

        plugins = glob.glob('{}/{}/python/plugins/*/'.format(backup_dir, date))
        if plugins:
            logger.info('Restoring plugins')
            for plugin in plugins:
                cmd(['mv', '-v', plugin, os.path.join(python_dir, 'plugins')])

        logger.info('Running post-update')
        cmd(['bash', os.path.join(python_dir, 'post-update.sh')])
        cmd(['sync'])
    except Exception as exc:
        logger.exception('Updating Gateway service failed')
        return exc


def update_gateway_frontend(tarball, date, version):
    try:
        if is_up_to_date('gateway_frontend', version):
            return
        backup_dir = os.path.join(PREFIX, 'backup')
        static_dir = os.path.join(PREFIX, 'static')
        cmd(['mkdir', '-p', backup_dir])
        cmd('ls -tp | grep "/$" | tail -n +3 | while read file; do rm -r $file; done', shell=True, cwd=backup_dir)

        # TODO: symlink, A-B deployment
        cmd(['mkdir', '-p', os.path.join(backup_dir, date)])
        cmd(['mv', static_dir, os.path.join(backup_dir, date)])

        logger.info('Extracting gateway')
        cmd(['mkdir', '-p', static_dir])
        cmd(['tar', '-v', '-xzf', tarball, '-C', static_dir])
        mark_installed_version('gateway_frontend', version)
        cmd(['sync'])
    except Exception as exc:
        logger.exception('Updating Gateway service failed')
        return exc


def update(version, expected_md5):
    """
    Execute the actual update: extract the archive and execute the bash update script.

    :param version: the new version (after the update).
    :param expected_md5: the md5 sum provided by the server.
    """
    version_mapping = {}

    try:
        config = ConfigParser()
        config.read(constants.get_config_file())
        from_version = config.get('OpenMotics', 'version')
        logger.info('==================================')
        logger.info('Starting update {} -> {}'.format(from_version, version))

        update_file = constants.get_update_file()
        update_dir = os.path.dirname(update_file)
        # Change to update directory.
        os.chdir(update_dir)

        if os.path.exists(update_file):
            logger.info(' -> Extracting update.tgz')
            extract_legacy_update(update_file, expected_md5)
        else:
            logger.info(' -> Fetching metadata')
            meta = fetch_metadata(config, version, expected_md5)
            logger.info(' -> Downloading firmware for update {}'.format(meta['version']))
            for data in meta['firmwares']:
                download_firmware(data['type'], data['url'], data['sha256'])
                version_mapping[data['type']] = data['version']
    except Exception:
        logger.exception('failed to preprepare update')
        raise SystemExit(1)

    errors = []
    services_running = True
    try:
        date = datetime.now().strftime('%Y%m%d%H%M%S')

        # TODO: should update and re-execute itself before proceeding?

        logger.info(' -> Checking services')
        check_services()

        logger.info(' -> Stopping services')
        stop_services()
        services_running = False

        gateway_os = FIRMWARE_FILES['gateway_os']
        if os.path.exists(gateway_os):
            os_version = version_mapping.get('gateway_os')
            logger.info(' -> Updating Gateway OS to {0}'.format(os_version if os_version else 'unknown version'))
            error = update_gateway_os(gateway_os, os_version)
            if error:
                errors.append(error)

        gateway_service = FIRMWARE_FILES['gateway_service']
        if os.path.exists(gateway_service):
            service_version = version_mapping.get('gateway_service')
            logger.info(' -> Updating Gateway service to {0}'.format(service_version if service_version else 'unknown version'))
            error = update_gateway_backend(gateway_service, date, service_version)
            if error:
                errors.append(error)

        master_type = get_master_type()
        master_firmware = FIRMWARE_FILES[master_type]
        if os.path.exists(master_firmware):
            master_version = version_mapping.get(master_type)
            logger.info(' -> Updating Master firmware to {0}'.format(master_version if master_version else 'unknown version'))
            error = update_master_firmware(master_type, master_firmware, master_version)
            if error:
                errors.append(error)

        for module, filename, arguments in [('energy', FIRMWARE_FILES['energy'], []),
                                            ('power', FIRMWARE_FILES['power'], ['--8'])]:
            if os.path.exists(filename):
                energy_version = version_mapping.get(module)
                logger.info(' -> Updating {0} firmware to {1}'.format(module, energy_version if energy_version else 'unknown version'))
                error = update_energy_firmware(module, filename, energy_version, arguments)
                if error:
                    errors.append(error)

        for module in MODULE_TYPES:
            module_firmware = FIRMWARE_FILES[module]
            module_version = version_mapping.get(module)
            if os.path.exists(module_firmware):
                logger.info(' -> Updating {0} firmware to {1}'.format(module, module_version if module_version else 'unknown version'))
                error = update_module_firmware(module, module_firmware, module_version)
                if error:
                    errors.append(error)

        logger.info('Checking master communication')
        check_master_communication()

        gateway_frontend = FIRMWARE_FILES['gateway_frontend']
        if os.path.exists(gateway_frontend):
            frontend_version = version_mapping.get('gateway_frontend')
            logger.info(' -> Updating Gateway frontend to {0}'.format(frontend_version if frontend_version else 'unknown version'))
            error = update_gateway_frontend(gateway_frontend, date, frontend_version)
            if error:
                errors.append(error)

        logger.info(' -> Starting services')
        start_services()
        services_running = True

        logger.info(' -> Waiting for health check')
        check_gateway_health()

    except Exception as exc:
        logger.exception('Unexpected exception updating')
        errors.append(exc)
        # TODO: rollback
    finally:
        if not services_running:
            logger.info(' -> Starting services')
            start_services()

        logger.info(' -> Running cleanup')
        cmd('rm -v -rf {}/*'.format(update_dir), shell=True)

        if errors:
            logger.error('Exceptions:')
            for error in errors:
                logger.error('- {0}'.format(error))
            raise SystemExit(1)

        config.set('OpenMotics', 'version', version)
        temp_file = constants.get_config_file() + '.update'
        with open(temp_file, 'wb') as configfile:
            config.write(configfile)
        shutil.move(temp_file, constants.get_config_file())
        cmd(['sync'])

        if os.path.exists('/tmp/post_update_reboot'):
            logger.info('Scheduling reboot in 5 minutes')
            subprocess.Popen('sleep 300 && reboot', shell=True)

        logger.info('DONE')
        logger.info('exit 0')


def main():
    """ The main function. """
    if len(sys.argv) != 3:
        print('Usage: python ' + __file__ + ' version md5sum')
        sys.exit(1)

    (version, expected_md5) = (sys.argv[1], sys.argv[2])

    # Start with version line, this is expected by the cloud.
    logger.info(version)

    lockfile = constants.get_update_lockfile()
    with open(lockfile, 'wc') as wfd:
        try:
            fcntl.flock(wfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            logger.exception('failed to aquire update lock')
            logger.error('FAILED')
            logger.error('exit 1')
            raise SystemExit(1)
        try:
            update(version, expected_md5)
        except SystemExit:
            logger.error('FAILED')
            logger.error('exit 1')
        finally:
            fcntl.flock(wfd, fcntl.LOCK_UN)
            os.unlink(lockfile)


if __name__ == '__main__':
    main()
