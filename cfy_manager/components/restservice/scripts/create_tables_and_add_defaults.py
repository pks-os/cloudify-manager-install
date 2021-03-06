#!/usr/bin/env python
#########
# Copyright (c) 2016 GigaSpaces Technologies Ltd. All rights reserved
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

import atexit
import json
import argparse
import logging
import tempfile
import os
from datetime import datetime

from flask_migrate import upgrade

from manager_rest import config, version
from manager_rest.storage import db, models, get_storage_manager  # NOQA
from manager_rest.amqp_manager import AMQPManager
from manager_rest.flask_utils import setup_flask_app
from manager_rest.storage.storage_utils import \
    create_default_user_tenant_and_roles

logger = \
    logging.getLogger('[{0}]'.format('create_tables_and_add_defaults'.upper()))


def _init_db_tables(db_migrate_dir):
    print 'Setting up a Flask app'
    setup_flask_app(
        manager_ip=config.instance.postgresql_host,
        hash_salt=config.instance.security_hash_salt,
        secret_key=config.instance.security_secret_key
    )

    # Clean up the DB, in case it's not a clean install
    db.drop_all()
    db.engine.execute('DROP TABLE IF EXISTS alembic_version;')

    print 'Creating tables in the DB'
    upgrade(directory=db_migrate_dir)


def _add_default_user_and_tenant(amqp_manager, script_config):
    print 'Creating bootstrap admin, default tenant and security roles'
    create_default_user_tenant_and_roles(
        admin_username=script_config['admin_username'],
        admin_password=script_config['admin_password'],
        amqp_manager=amqp_manager,
        authorization_file_path=script_config['authorization_file_path']
    )


def _get_amqp_manager(script_config):
    with tempfile.NamedTemporaryFile(delete=False, mode='wb') as f:
        f.write(script_config['rabbitmq_ca_cert'])
    broker = script_config['rabbitmq_brokers'][0]
    atexit.register(os.unlink, f.name)
    return AMQPManager(
        host=broker['management_host'],
        username=broker['username'],
        password=broker['password'],
        verify=f.name
    )


def _insert_config(config):
    sm = get_storage_manager()
    for scope, entries in config:
        for name, value in entries.items():
            inst = sm.get(models.Config, None,
                          filters={'name': name, 'scope': scope})
            inst.value = value
            sm.update(inst)


def _insert_rabbitmq_broker(brokers, ca_id):
    sm = get_storage_manager()
    for broker in brokers:
        inst = models.RabbitMQBroker(
            _ca_cert_id=ca_id,
            **broker
        )
        sm.put(inst)


def _insert_manager(config, ca_id):
    sm = get_storage_manager()
    version_data = version.get_version_data()
    inst = models.Manager(
        public_ip=config['public_ip'],
        hostname=config['hostname'],
        private_ip=config['private_ip'],
        networks=config['networks'],
        edition=version_data['edition'],
        version=version_data['version'],
        distribution=version_data['distribution'],
        distro_release=version_data['distro_release'],
        _ca_cert_id=ca_id
    )
    sm.put(inst)


def _insert_cert(cert, name):
    sm = get_storage_manager()
    inst = models.Certificate(
        name=name,
        value=cert,
        updated_at=datetime.now(),
        _updater_id=0,
    )
    sm.put(inst)
    return inst.id


def _add_provider_context(context):
    sm = get_storage_manager()
    provider_context = models.ProviderContext(
        id='CONTEXT',
        name='provider',
        context=context
    )
    sm.put(provider_context)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Create SQL DB tables and populate them with defaults'
    )
    parser.add_argument(
        'config_path',
        help='Path to a config file containing info needed by this script'
    )

    args = parser.parse_args()
    config.instance.load_configuration(from_db=False)

    with open(args.config_path, 'r') as f:
        script_config = json.load(f)
    _init_db_tables(script_config['db_migrate_dir'])
    amqp_manager = _get_amqp_manager(script_config)
    _add_default_user_and_tenant(amqp_manager, script_config)
    _insert_config(script_config['config'])
    rabbitmq_ca_id = _insert_cert(script_config['rabbitmq_ca_cert'],
                                  'rabbitmq-ca')
    rest_ca_id = _insert_cert(script_config['ca_cert'],
                              '{0}-ca'.format(script_config['hostname']))
    _insert_manager(script_config, rest_ca_id)
    _insert_rabbitmq_broker(script_config['rabbitmq_brokers'], rabbitmq_ca_id)
    _add_provider_context(script_config['provider_context'])
    print 'Finished creating bootstrap admin, default tenant and provider ctx'
