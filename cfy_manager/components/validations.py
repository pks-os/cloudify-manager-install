#########
# Copyright (c) 2017 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

import os
import sys
import platform
import subprocess
import netifaces
from getpass import getuser
from collections import namedtuple
from ipaddress import ip_address
from distutils.version import LooseVersion

from .components_constants import (
    PRIVATE_IP,
    PUBLIC_IP,
    VALIDATIONS,
    SKIP_VALIDATIONS,
    SSL_INPUTS,
    SSL_ENABLED,
    SERVICES_TO_INSTALL,
    ENABLE_REMOTE_CONNECTIONS,
    POSTGRES_PASSWORD,
    ACTIVE_MANAGER_IP
)
from .service_components import (
    DATABASE_SERVICE,
    MANAGER_SERVICE
)
from .service_names import (
    MANAGER,
    POSTGRESQL_CLIENT,
    POSTGRESQL_SERVER,
    CLUSTER
)

from ..config import config
from ..logger import get_logger
from ..constants import USER_CONFIG_PATH
from ..exceptions import ValidationError

from ..utils.common import run, sudo
from ..utils.network import is_port_open

logger = get_logger(VALIDATIONS)

_errors = []


def _get_os_distro():
    distro, version, _ = \
        platform.linux_distribution(full_distribution_name=False)
    return distro.lower(), version.split('.')[0]


def _get_host_total_memory():
    """
    MemTotal:        7854400 kB
    MemFree:         1811840 kB
    MemAvailable:    3250176 kB
    Buffers:          171164 kB
    Cached:          1558216 kB
    SwapCached:       119180 kB
    """
    with open('/proc/meminfo') as memfile:
        memory = memfile.read()
    for attribute in memory.splitlines():
        if attribute.lower().startswith('memtotal'):
            return int(attribute.split(':')[1].strip().split(' ')[0]) / 1024


def _get_available_host_disk_space():
    """Available space in GB on the filesystem containing /opt"""
    result = os.statvfs('/opt')
    bytes_available = result.f_bavail * result.f_bsize
    return bytes_available // (1024 * 1024 * 1024)


def _validate_supported_distros():
    logger.info('Validating supported distributions...')
    distro, version = _get_os_distro()
    supported_distros = config[VALIDATIONS]['supported_distros']
    supported_distro_versions = \
        config[VALIDATIONS]['supported_distro_versions']
    if distro not in supported_distros:
        _errors.append(
            'Cloudify manager does not support the current distro (`{0}`),'
            'supported distros are: {1}'.format(distro, supported_distros)
        )
    if version not in supported_distro_versions:
        _errors.append(
            'Cloudify manager does not support the current distro version '
            '(`{0}`), supported versions are: {1}'.format(
                version, supported_distro_versions
            )
        )


def _validate_ip(ip_to_validate, check_local_interfaces=False):
    """
    Validate the IP address given is valid.

    :param ip_to_validate: IP address to validate
    :param check_local_interfaces: If true, will check local interfaces
           associated with the IP address
    :return: Will break in case of error
    """
    logger.info('Validating IP address...')

    try:
        # ip_address() requires a unicode string
        ip_address(unicode(ip_to_validate, 'utf-8'))
    except ValueError:
        logger.debug('Failed creating an IP address from "{}"'.format(
            ip_to_validate), exc_info=True)
        logger.info('Provided value ({}) is not an IP address; '
                    'skipping'.format(ip_to_validate))
        return

    if check_local_interfaces:
        all_addresses = set()
        found = False
        for interface in netifaces.interfaces():
            int_addresses = netifaces.ifaddresses(interface)
            if not int_addresses:
                logger.debug(
                    'Could not find any addresses for interface {0}'.format(
                        interface))
                continue
            inet_addresses = int_addresses.get(netifaces.AF_INET)
            if not inet_addresses:
                logger.debug('No AF_INET addresses found for interface {0}'
                             .format(interface))
                continue
            for inet_addr in inet_addresses:
                addr = inet_addr.get('addr')
                if not addr:
                    logger.debug('No addr found for {0}'.format(inet_addr))
                    continue
                if addr == ip_to_validate:
                    found = True
                    break
                all_addresses.add(addr)
            if found:
                break

        if not found:
            _errors.append(
                "The provided private IP ({ip}) is not associated with any "
                "INET-type address available on this machine (available "
                "INET-type addresses: {addresses}). This will cause "
                "installation to fail. If you are certain that this IP address"
                " should be used, please set the '{param}' parameter to "
                "'false'".format(
                    ip=ip_to_validate,
                    addresses=', '.join(all_addresses),
                    param=SKIP_VALIDATIONS))


def _validate_python_version():
    logger.info('Validating Python version...')
    major_version, minor_version = sys.version_info[0], sys.version_info[1]
    python_version = '{0}.{1}'.format(major_version, minor_version)
    expected_version = config[VALIDATIONS]['expected_python_version']
    if python_version != expected_version:
        error = 'Local python version (`{0}`) does not match expected ' \
                'version (`{1}`)'.format(python_version, expected_version)
        _errors.append(error)


def _validate_sufficient_memory():
    logger.info('Validating memory requirement...')
    current_memory = _get_host_total_memory()
    required_memory = \
        config[VALIDATIONS]['minimum_required_total_physical_memory_in_mb']
    if current_memory < required_memory:
        _errors.append(
            'The provided host does not have enough memory to run '
            'Cloudify Manager (Current: {0}MB, Required: {1}MB).'.format(
                current_memory, required_memory)
        )


def _validate_sufficient_disk_space():
    logger.info('Validating disk space requirement...')
    available_disk_space_in_gb = _get_available_host_disk_space()
    required_disk_space = \
        config[VALIDATIONS]['minimum_required_available_disk_space_in_gb']

    if available_disk_space_in_gb < required_disk_space:
        _errors.append(
            'The provided host does not have enough disk space to run '
            'Cloudify Manager (Current: {0}GB, Required: {1}GB).'.format(
                available_disk_space_in_gb, required_disk_space)
        )


def _validate_openssl_version():
    logger.info('Validating OpenSSL version...')
    required_version = '1.0.2'

    try:
        output = run(['openssl', 'version']).aggr_stdout
    except OSError as e:
        _errors.append(
            'Cloudify Manager requires OpenSSL {0}, Error: {1}'.format(
                required_version, e
            )
        )
        return

    # The output should look like: "LibreSSL 2.2.7" or "OpenSSL 1.0.2k-fips"
    version = output.split()[1]
    if LooseVersion(version) < LooseVersion(required_version):
        _errors.append(
            'Cloudify Manager requires OpenSSL {0}, current version: {1}'
            ''.format(required_version, version)
        )


def _validate_inputs():
    Input = namedtuple('Input', 'key string flag')
    required_inputs = [
        Input(key=PRIVATE_IP, flag='--private-ip', string='Private IP'),
        Input(key=PUBLIC_IP, flag='--public-ip', string='Public IP')
    ]
    for inp in required_inputs:
        input_value = config[MANAGER].get(inp.key)
        if not input_value:
            raise ValidationError(
                '{string} not set in the config.\n'
                'Possible solutions are:\n'
                '1. Set the `{key}` key in {config_path}\n'
                '2. Use the `{flag}` flag when running '
                '`cfy_manager install/configure`'.format(
                    string=inp.string,
                    key=inp.key,
                    config_path=USER_CONFIG_PATH,
                    flag=inp.flag
                )
            )


def _validate_user_has_sudo_permissions():
    current_user = getuser()
    logger.info('Validating user `{0}` has sudo permissions...'.format(
        current_user
    ))
    result = run(['sudo', '-n', 'true'])
    if result.returncode != 0:
        _errors.append(
            "Failed executing 'sudo'. Please ensure that the "
            "current user ({0}) is allowed to execute 'sudo' commands "
            "and impersonate other users using "
            "'sudo -u'. (Error: {1})".format(current_user, result.aggr_stderr)
        )


def _validate_dependencies(components):
    logger.info('Validating dependencies...')

    components_str = ', '.join([component.__class__.__name__
                               for component in components])
    logger.debug('The following components are validated: '
                 '{0}'.format(components_str))

    for component in components:
        component.validate_dependencies()


def _check_ssl_file(filename, kind='Key', password=None):
    """Does the cert/key file exist and is it valid?"""
    if not os.path.isfile(filename):
        raise ValidationError(
            '{0} file {1} does not exist'
            .format(kind, filename))
    if kind == 'Key':
        check_command = ['openssl', 'rsa', '-in', filename, '-check', '-noout']
        if password:
            check_command += ['-passin', 'pass:{0}'.format(password)]
    elif kind == 'Cert':
        check_command = ['openssl', 'x509', '-in', filename, '-noout']
    else:
        raise ValueError('Unknown kind: {0}'.format(kind))
    proc = sudo(check_command, ignore_failures=True)
    if proc.returncode != 0:
        password_err = ''
        if password:
            password_err = '(or the provided password is incorrect)'
        raise ValidationError('{0} file {1} password is invalid {2}'
                              .format(kind, filename, password_err))


def check_certificates(component,
                       cert_path='cert_path', key_path='key_path',
                       ca_path='ca_path', key_password='key_password',
                       require_non_ca_certs=True):
    """Check that the provided cert, key, and CA actally match"""
    cert_filename = config[component].get(cert_path)
    key_filename = config[component].get(key_path)

    ca_filename = config[component].get(ca_path)
    password = config[component].get(key_password)

    if not cert_filename and not key_filename and require_non_ca_certs:
        failing = []
        if password:
            failing.append('key_password')
        if ca_filename:
            failing.append('ca_path')
        if failing:
            failing = ' or '.join(failing)
            raise ValidationError(
                'If {failing} was provided, both cert_path and key_path '
                'must be provided in {component}'.format(
                    failing=failing,
                    component=component,
                )
            )
    elif cert_filename and key_filename:
        _check_ssl_file(key_filename, kind='Key', password=password)
        _check_ssl_file(cert_filename, kind='Cert')
        key_modulus_command = ['openssl', 'rsa', '-noout', '-modulus',
                               '-in', key_filename]
        if password:
            key_filename += ['-passin', 'pass:{0}'.format(password)]
        cert_modulus_command = ['openssl', 'x509', '-noout', '-modulus',
                                '-in', cert_filename]
        key_modulus = sudo(key_modulus_command).aggr_stdout.strip()
        cert_modulus = sudo(cert_modulus_command).aggr_stdout.strip()
        if cert_modulus != key_modulus:
            raise ValidationError('Key {0} ({1}.{2}) does not match the '
                                  'cert {3} ({1}.{4})'
                                  .format(key_filename, component, key_path,
                                          cert_filename, cert_path))
    elif cert_filename or key_filename:
        raise ValidationError('Either both cert_path and key_path must be '
                              'provided, or neither.')

    if ca_filename:
        _check_ssl_file(ca_filename, kind='Cert')
        if cert_filename:
            ca_check_command = [
                'openssl', 'verify', '-CAfile', ca_filename, cert_filename
            ]
            try:
                sudo(ca_check_command)
            except subprocess.CalledProcessError:
                raise ValidationError(
                    'Provided certificate {cert} was not signed by provided '
                    'CA {ca}'.format(
                        cert=cert_filename,
                        ca=ca_filename,
                    )
                )

    return cert_filename, key_filename, ca_filename, password


def _check_internal_ca_cert():
    ssl_inputs = config[SSL_INPUTS]
    if ssl_inputs['ca_key_path'] and ssl_inputs['ca_cert_path']:
        _check_ssl_file(ssl_inputs['ca_key_path'],
                        password=ssl_inputs['ca_key_password'])
        _check_ssl_file(ssl_inputs['ca_cert_path'], kind='Cert')
    elif ssl_inputs['ca_key_path'] and not ssl_inputs['ca_cert_path']:
        raise ValidationError('Internal CA key provided, but the internal '
                              'CA cert was not')
    elif ssl_inputs['ca_cert_path'] and not ssl_inputs['ca_key_path']:
        if not ssl_inputs['internal_cert_path'] \
                or not ssl_inputs['internal_key_path']:
            raise ValidationError('If ca_cert_path was provided, but '
                                  'ca_key_path was not provided, both '
                                  'internal_cert_path and internal_key_path '
                                  'must be provided.')
    elif ssl_inputs['ca_key_password']:
        raise ValidationError('If ca_key_password was provided, both '
                              'ca_cert_path and ca_key_path must be '
                              'provided.')


def _validate_cert_inputs():
    _check_internal_ca_cert()
    for ssl_input in (
        'internal',
        'postgresql_server',
        'postgresql_client',
        'external',
        'external_ca',
    ):
        cert_path = '{0}_cert_path'.format(ssl_input)
        key_path = '{0}_key_path'.format(ssl_input)
        ca_path = '{0}_ca_path'.format(ssl_input)
        key_password = '{0}_key_password'.format(ssl_input)
        # These should all be moved to their respective components- see Rabbit
        check_certificates(
            SSL_INPUTS,
            cert_path=cert_path,
            key_path=key_path,
            ca_path=ca_path,
            key_password=key_password,
        )


def _services_coexistence_assertion(service_in_list_to_install,
                                    service_not_in_list_to_install):
    return service_in_list_to_install in config[SERVICES_TO_INSTALL] and \
           service_not_in_list_to_install not in config[SERVICES_TO_INSTALL]


def _validate_postgres_inputs():
    """
    Validating that an external DB will always listen to remote connections
    and, that a postgres password is set - needed for remote connections
    """
    if _services_coexistence_assertion(DATABASE_SERVICE, MANAGER_SERVICE):
        if config[POSTGRESQL_SERVER][ENABLE_REMOTE_CONNECTIONS] and \
            not config[POSTGRESQL_SERVER][POSTGRES_PASSWORD] \
            or \
            not config[POSTGRESQL_SERVER][ENABLE_REMOTE_CONNECTIONS] and \
                config[POSTGRESQL_SERVER][POSTGRES_PASSWORD]:
            raise ValidationError('When using an external database, both '
                                  'enable_remote_connections and '
                                  'postgres_password must be set')

    if _services_coexistence_assertion(MANAGER_SERVICE, DATABASE_SERVICE):
        postgres_host = config[POSTGRESQL_CLIENT]['host'].split(':')[0]
        if postgres_host in ('localhost', '127.0.0.1') and \
                not config[POSTGRESQL_CLIENT][POSTGRES_PASSWORD]:
            raise ValidationError('When using an external database, '
                                  'postgres_password must be set')


def _validate_postgres_ssl_certificates_provided():
    error_msg = 'If Postgresql requires SSL communication {0} a ' \
                'certificate and a key for Postgresql must be provided in ' \
                'config.yaml->ssl_inputs->{1}'
    if not (config[SSL_INPUTS]['postgresql_server_cert_path'] and
            config[SSL_INPUTS]['postgresql_server_key_path'] and
            config[SSL_INPUTS]['ca_cert_path']):
        if config[POSTGRESQL_SERVER][SSL_ENABLED]:
            raise ValidationError(error_msg.format(
                'a CA certificate,', 'postgresql_server'))
    elif not (config[SSL_INPUTS]['postgresql_client_cert_path'] and
              config[SSL_INPUTS]['postgresql_client_key_path']):
        if config[POSTGRESQL_CLIENT][SSL_ENABLED]:
            raise ValidationError(error_msg.format('', 'postgresql_client'))


def _validate_external_postgres_ssl_enabled():
    """
    Basically, making sure that if the manager and database are not on the same
    machine, SSL for DB communication must be enabled
    """
    if not config[POSTGRESQL_SERVER][SSL_ENABLED] and \
            _services_coexistence_assertion(DATABASE_SERVICE, MANAGER_SERVICE)\
            or \
            not config[POSTGRESQL_CLIENT][SSL_ENABLED] and \
            _services_coexistence_assertion(MANAGER_SERVICE, DATABASE_SERVICE):
        raise ValidationError('When using an external database, SSL must be '
                              'enabled')


def validate_config_access(write_required):
    # It's OK if file doesn't exist.
    if os.path.isfile(USER_CONFIG_PATH):
        if write_required:
            mode = os.R_OK | os.W_OK
            label = 'readable and writable'
        else:
            mode = os.R_OK
            label = 'readable'

        if not os.access(USER_CONFIG_PATH, mode):
            raise ValidationError(
                'Configuration file ({0}) must be {1} '
                'by the current user'.format(
                    USER_CONFIG_PATH, label))


def _validate_active_manager_access():
    is_port_open(80, config[CLUSTER][ACTIVE_MANAGER_IP])


def validate(components, skip_validations=False, only_install=False):
    if not only_install:
        # Inputs always need to be validated, otherwise the install won't work
        _validate_inputs()

    # These dependencies also need to always be validated
    _validate_dependencies(components)

    if config[VALIDATIONS][SKIP_VALIDATIONS] or skip_validations:
        logger.info('Skipping validations')
        return

    logger.notice('Validating local machine...')

    if not only_install:
        _validate_ip(config[MANAGER][PRIVATE_IP], check_local_interfaces=True)
        _validate_ip(ip_to_validate=config[MANAGER][PUBLIC_IP])
        if config[POSTGRESQL_CLIENT]['host'] not in ('localhost', '127.0.0.1'):
            _validate_ip(ip_to_validate=config[POSTGRESQL_CLIENT]['host'])
        if config[CLUSTER][ACTIVE_MANAGER_IP]:
            _validate_active_manager_access()
        _validate_python_version()
        _validate_sufficient_memory()
        _validate_postgres_inputs()
        _validate_external_postgres_ssl_enabled()
        _validate_postgres_ssl_certificates_provided()
        _validate_cert_inputs()

    _validate_supported_distros()
    _validate_openssl_version()
    _validate_user_has_sudo_permissions()
    _validate_sufficient_disk_space()

    if _errors:
        printable_error = 'Validation error(s):\n' \
                          '{0}'.format('\n'.join(_errors))
        raise ValidationError(printable_error)
    logger.notice('All validations passed successfully!')
