import json
import logging
from logging.handlers import WatchedFileHandler

from os import path
import pkg_resources
from uuid import uuid4
from requests import post

from manager_rest import premium_enabled


MANAGER_ID_PATH = '/etc/cloudify/.id'
CLOUDIFY_ENDPOINT_UPTIME_URL = 'https://api.cloudify.co/cloudifyUptime'
LOGFILE = '/var/log/cloudify/usage_collector/usage_collector.log'
logger = logging.getLogger('usage_collector')
logger.setLevel(logging.INFO)
file_handler = WatchedFileHandler(filename=LOGFILE)
formatter = logging.Formatter(fmt='%(asctime)s [%(levelname)s] '
                                  '[%(name)s] %(message)s',
                              datefmt='%d/%m/%Y %H:%M:%S')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def _collect_metadata(data):
    pkg_distribution = pkg_resources.get_distribution('cloudify-rest-service')
    manager_version = pkg_distribution.version
    with open(MANAGER_ID_PATH) as id_file:
        manager_id = id_file.read().strip()
    data['metadata'] = {
        'manager_id': manager_id,
        'premium_edition': premium_enabled,
        'version': manager_version
    }


def _send_data(data):
    # for some reason, multi hierarchy dict doesn't pass well to the end point
    logger.info('The sent data: {0}'.format(data))
    data = {'data': json.dumps(data)}
    post(CLOUDIFY_ENDPOINT_UPTIME_URL, data=data)


def _create_manager_id_file():
    if path.exists(MANAGER_ID_PATH):
        with open(MANAGER_ID_PATH) as f:
            existing_manager_id = f.read().strip()
            if existing_manager_id:
                return
    with open(MANAGER_ID_PATH, 'w') as f:
        f.write(uuid4().hex)


def main():
    logger.info('Uptime script started running')
    _create_manager_id_file()
    data = {}
    _collect_metadata(data)
    _send_data(data)
    logger.info('Uptime script finished running')


if __name__ == '__main__':
    main()
