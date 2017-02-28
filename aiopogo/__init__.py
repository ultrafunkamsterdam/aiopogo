from .exceptions import PleaseInstallProtobufVersion3

import logging

__title__ = 'aiopogo'
__version__ = '1.2'
__author__ = 'Noctem'
__license__ = 'MIT License'
__copyright__ = 'Copyright (c) 2017 Noctem <https://github.com/Noctem>'

protobuf_exist = False
protobuf_version = 0
try:
    from google import protobuf
    protobuf_version = protobuf.__version__
    protobuf_exist = True
except ImportError:
    raise PleaseInstallProtobufVersion3('Protobuf not found, install it.')

if int(protobuf_version[:1]) < 3:
    raise PleaseInstallProtobufVersion3('Protobuf 3 needed, you have {}'.format(protobuf_version))

from .pgoapi import PGoApi
from .rpc_api import RpcApi, RPC_SESSIONS
from .auth import Auth
from .hash_server import HashServer

def close_sessions():
    RPC_SESSIONS.close()
    HashServer.close_session()

logging.getLogger("pgoapi").addHandler(logging.NullHandler())
logging.getLogger("rpc_api").addHandler(logging.NullHandler())
logging.getLogger("utilities").addHandler(logging.NullHandler())
logging.getLogger("auth").addHandler(logging.NullHandler())
logging.getLogger("auth_ptc").addHandler(logging.NullHandler())
logging.getLogger("auth_google").addHandler(logging.NullHandler())
