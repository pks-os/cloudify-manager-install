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
import re
from tempfile import mkstemp
from os.path import join, isdir, islink

from ..components_constants import (
    SOURCES,
    PRIVATE_IP,
    SCRIPTS,
    ENABLE_REMOTE_CONNECTIONS,
    POSTGRES_PASSWORD,
    SSL_ENABLED,
    SSL_INPUTS
)
from ..base_component import BaseComponent
from ..service_names import (
    POSTGRESQL_SERVER,
    MANAGER
)
from ... import constants
from ...config import config
from ...logger import get_logger
from ...utils import common, files
from ...utils.systemd import systemd
from ...utils.install import yum_install, yum_remove

POSTGRESQL_SCRIPTS_PATH = join(constants.COMPONENTS_DIR, POSTGRESQL_SERVER,
                               SCRIPTS)

SYSTEMD_SERVICE_NAME = 'postgresql-9.5'
POSTGRES_USER = POSTGRES_GROUP = 'postgres'
HOST = 'host'
LOG_DIR = join(constants.BASE_LOG_DIR, POSTGRESQL_SERVER)

PGSQL_LIB_DIR = '/var/lib/pgsql'
PGSQL_USR_DIR = '/usr/pgsql-9.5'
PG_HBA_CONF = '/var/lib/pgsql/9.5/data/pg_hba.conf'
PG_CONF_PATH = '/var/lib/pgsql/9.5/data/postgresql.conf'
PGPASS_PATH = join(constants.CLOUDIFY_HOME_DIR, '.pgpass')

PG_CA_CERT_PATH = os.path.join(os.path.dirname(PG_CONF_PATH), 'root.crt')
PG_SERVER_CERT_PATH = os.path.join(os.path.dirname(PG_CONF_PATH), 'server.crt')
PG_SERVER_KEY_PATH = os.path.join(os.path.dirname(PG_CONF_PATH), 'server.key')

PG_HBA_LISTEN_ALL_REGEX_PATTERN = r'host\s+all\s+all\s+0\.0\.0\.0\/0\s+md5'
PG_HBA_HOSTSSL_REGEX_PATTERN = \
    r'hostssl\s+all\s+all\s+0\.0\.0\.0\/0\s+md5\s+clientcert=1'

PG_PORT = 5432

logger = get_logger(POSTGRESQL_SERVER)


class PostgresqlServer(BaseComponent):
    def __init__(self, skip_installation):
        super(PostgresqlServer, self).__init__(skip_installation)

    def _install(self):
        sources = config[POSTGRESQL_SERVER][SOURCES]

        logger.debug('Installing PostgreSQL Server dependencies...')
        yum_install(sources['libxslt_rpm_url'])

        logger.debug('Installing PostgreSQL Server...')
        yum_install(sources['ps_libs_rpm_url'])
        yum_install(sources['ps_rpm_url'])
        yum_install(sources['ps_contrib_rpm_url'])
        yum_install(sources['ps_server_rpm_url'])
        yum_install(sources['ps_devel_rpm_url'])

    def _init_postgresql_server(self):
        logger.debug('Initializing PostreSQL Server DATA folder...')
        postgresql95_setup = join(PGSQL_USR_DIR, 'bin', 'postgresql95-setup')
        try:
            common.sudo(command=[postgresql95_setup, 'initdb'])
        except Exception:
            logger.debug('PostreSQL Server DATA folder already initialized...')
            pass

        logger.debug('Installing PostgreSQL Server service...')
        systemd.enable(SYSTEMD_SERVICE_NAME, append_prefix=False)
        systemd.restart(SYSTEMD_SERVICE_NAME, append_prefix=False)

        logger.debug('Setting PostgreSQL Server logs path...')
        ps_95_logs_path = join(PGSQL_LIB_DIR, '9.5', 'data', 'pg_log')
        common.mkdir(LOG_DIR)
        if not isdir(ps_95_logs_path) and not islink(join(LOG_DIR, 'pg_log')):
            files.ln(source=ps_95_logs_path, target=LOG_DIR, params='-s')

        logger.info('Starting PostgreSQL Server service...')
        systemd.restart(SYSTEMD_SERVICE_NAME, append_prefix=False)

    def _read_old_file_lines(self, file_path):
        temp_file_path = files.write_to_tempfile('')
        common.copy(file_path, temp_file_path)
        common.chmod('777', temp_file_path)
        with open(temp_file_path, 'r') as f:
            lines = f.readlines()
        return lines

    def _write_new_pgconfig_file(self, lines):
        """
        Recreate the pgconfig file based on listening address and SSL
        """
        fd, temp_pgconfig_path = mkstemp()
        os.close(fd)
        with open(temp_pgconfig_path, 'a') as f:
            for line in lines:
                if line.startswith('#listen_addresses = \'localhost\''):
                    line = line.replace('#listen_addresses = \'localhost\'',
                                        'listen_addresses = \'{0}\''
                                        .format(config[MANAGER][PRIVATE_IP]))
                if config[POSTGRESQL_SERVER][SSL_ENABLED]:
                    if line.startswith('#ssl = off'):
                        line = line.replace('#ssl = off', 'ssl = on')
                    if line.startswith('#ssl_ca_file = \'\''):
                        line = line.replace('#ssl_ca_file = \'\'',
                                            'ssl_ca_file = \'{0}\''.format(
                                                PG_CA_CERT_PATH
                                            ))
                f.write(line)
        return temp_pgconfig_path

    def _write_new_hba_file(self, lines, enable_remote_connections):
        fd, temp_hba_path = mkstemp()
        os.close(fd)
        with open(temp_hba_path, 'a') as f:
            for line in lines:
                if line.startswith(('host', 'local')):
                    line = line.replace('ident', 'md5')
                f.write(line)
            if not re.search(PG_HBA_LISTEN_ALL_REGEX_PATTERN,
                             '\n'.join(lines)) and enable_remote_connections:
                f.write('host all all 0.0.0.0/0 md5\n')
            if config[POSTGRESQL_SERVER][SSL_ENABLED] and not \
                    re.search(PG_HBA_HOSTSSL_REGEX_PATTERN, '\n'.join(lines)):
                # This will require the client to supply a certificate as well
                f.write('hostssl all all 0.0.0.0/0 md5 clientcert=1')
        return temp_hba_path

    def _configure_ssl(self):
        """
        Copy SSL certificates to postgres data directory.
        postgresql.conf and pg_hba.conf configurations are handled in
        the update_configuration step
        """
        if config[POSTGRESQL_SERVER][SSL_ENABLED]:
            common.copy(config[SSL_INPUTS]['postgresql_server_cert_path'],
                        PG_SERVER_CERT_PATH)
            common.copy(config[SSL_INPUTS]['postgresql_server_key_path'],
                        PG_SERVER_KEY_PATH)
            # This will be used to verify the client's certificate
            common.copy(config[SSL_INPUTS]['ca_cert_path'],
                        PG_CA_CERT_PATH)

            common.chown(POSTGRES_USER, POSTGRES_GROUP,
                         PG_SERVER_CERT_PATH)
            common.chown(POSTGRES_USER, POSTGRES_GROUP,
                         PG_SERVER_KEY_PATH)
            common.chown(POSTGRES_USER, POSTGRES_GROUP,
                         PG_CA_CERT_PATH)

            common.chmod('600', PG_SERVER_KEY_PATH)

    def _update_configuration(self, enable_remote_connections):
        logger.info('Updating PostgreSQL Server configuration...')
        logger.debug('Modifying {0}'.format(PG_HBA_CONF))
        common.copy(PG_HBA_CONF, '{0}.backup'.format(PG_HBA_CONF))
        lines = self._read_old_file_lines(PG_HBA_CONF)
        temp_hba_path = self._write_new_hba_file(lines,
                                                 enable_remote_connections)
        common.move(temp_hba_path, PG_HBA_CONF)
        common.chown(POSTGRES_USER, POSTGRES_USER, PG_HBA_CONF)
        if enable_remote_connections:
            lines = self._read_old_file_lines(PG_CONF_PATH)
            temp_pg_conf_path = self._write_new_pgconfig_file(lines)
            common.move(temp_pg_conf_path, PG_CONF_PATH)
            common.chown(POSTGRES_USER, POSTGRES_USER, PG_CONF_PATH)
            self._configure_ssl()

    def _update_postgres_password(self):
        logger.notice('Updating postgres password...')
        postgres_password = \
            config[POSTGRESQL_SERVER][POSTGRES_PASSWORD]

        update_password_script_path = join(POSTGRESQL_SCRIPTS_PATH,
                                           'update_postgres_password.sh')
        tmp_script_path = files.temp_copy(update_password_script_path)
        common.chmod('o+rx', tmp_script_path)
        common.sudo(
            'su - postgres -c "{cmd} {postgres_password}"'.format(
                cmd=tmp_script_path,
                postgres_password=postgres_password)
        )
        logger.info('Removing postgres password from config.yaml')
        config[POSTGRESQL_SERVER][POSTGRES_PASSWORD] = '<removed>'
        logger.notice('postgres password successfully updated')

    def _configure(self):
        files.copy_notice(POSTGRESQL_SERVER)
        self._init_postgresql_server()
        enable_remote_connections = \
            config[POSTGRESQL_SERVER][ENABLE_REMOTE_CONNECTIONS]
        self._update_configuration(enable_remote_connections)
        if config[POSTGRESQL_SERVER][POSTGRES_PASSWORD]:
            self._update_postgres_password()

        systemd.restart(SYSTEMD_SERVICE_NAME, append_prefix=False)
        systemd.verify_alive(SYSTEMD_SERVICE_NAME, append_prefix=False)

    def install(self):
        logger.notice('Installing PostgreSQL Server...')
        self._install()
        logger.notice('PostgreSQL Server successfully installed')

    def configure(self):
        logger.notice('Configuring PostgreSQL Server...')
        self._configure()
        logger.notice('PostgreSQL Server successfully configured')

    def remove(self):
        logger.notice('Removing PostgreSQL...')
        files.remove_notice(POSTGRESQL_SERVER)
        systemd.remove(SYSTEMD_SERVICE_NAME)
        files.remove_files([PGSQL_LIB_DIR, PGSQL_USR_DIR, LOG_DIR])
        yum_remove('postgresql95')
        yum_remove('postgresql95-libs')
        logger.notice('PostgreSQL successfully removed')

    def start(self):
        logger.notice('Starting PostgreSQL Server...')
        systemd.start(SYSTEMD_SERVICE_NAME, append_prefix=False)
        systemd.verify_alive(SYSTEMD_SERVICE_NAME, append_prefix=False)
        logger.notice('PostgreSQL Server successfully started')

    def stop(self):
        logger.notice('Stopping PostgreSQL Server...')
        systemd.stop(SYSTEMD_SERVICE_NAME, append_prefix=False)
        logger.notice('PostgreSQL Server successfully stopped')

    def validate_dependencies(self):
        super(PostgresqlServer, self).validate_dependencies()
