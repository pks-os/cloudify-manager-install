from os.path import join, dirname as up

CLOUDIFY_USER = 'cfyuser'
CLOUDIFY_GROUP = 'cfyuser'
CLOUDIFY_HOME_DIR = '/etc/cloudify'
SUDOERS_INCLUDE_DIR = '/etc/sudoers.d'
CLOUDIFY_SUDOERS_FILE = join(SUDOERS_INCLUDE_DIR, CLOUDIFY_USER)

BASE_RESOURCES_PATH = '/opt/cloudify'
CLOUDIFY_SOURCES_PATH = join(BASE_RESOURCES_PATH, 'sources')
MANAGER_RESOURCES_HOME = '/opt/manager/resources'
AGENT_ARCHIVES_PATH = '{0}/packages/agents'.format(MANAGER_RESOURCES_HOME)

BASE_LOG_DIR = '/var/log/cloudify'

INTERNAL_REST_PORT = 53333

BASE_DIR = up(__file__)
COMPONENTS_DIR = join(BASE_DIR, 'components')

SSL_CERTS_TARGET_DIR = '/etc/cloudify/ssl'

INTERNAL_CERT_FILENAME = 'cloudify_internal_cert.pem'
INTERNAL_KEY_FILENAME = 'cloudify_internal_key.pem'
INTERNAL_CA_CERT_FILENAME = 'cloudify_internal_ca_cert.pem'
INTERNAL_CA_KEY_FILENAME = 'cloudify_internal_ca_key.pem'
INTERNAL_PKCS12_FILENAME = 'cloudify_internal.p12'
EXTERNAL_CERT_FILENAME = 'cloudify_external_cert.pem'
EXTERNAL_KEY_FILENAME = 'cloudify_external_key.pem'

INTERNAL_CERT_PATH = join(SSL_CERTS_TARGET_DIR, INTERNAL_CERT_FILENAME)
INTERNAL_KEY_PATH = join(SSL_CERTS_TARGET_DIR, INTERNAL_KEY_FILENAME)
INTERNAL_CA_CERT_PATH = join(SSL_CERTS_TARGET_DIR, INTERNAL_CA_CERT_FILENAME)
INTERNAL_CA_KEY_PATH = join(SSL_CERTS_TARGET_DIR, INTERNAL_CA_KEY_FILENAME)
EXTERNAL_CERT_PATH = join(SSL_CERTS_TARGET_DIR, EXTERNAL_CERT_FILENAME)
EXTERNAL_KEY_PATH = join(SSL_CERTS_TARGET_DIR, EXTERNAL_KEY_FILENAME)
CERT_METADATA_FILE_PATH = join(SSL_CERTS_TARGET_DIR, 'certificate_metadata')
