from .exceptions import PleaseInstallProtobufVersion3

import logging

__title__ = 'aiopogo'
__version__ = '1.5.0'
__author__ = 'David Christenson'
__license__ = 'MIT License'
__copyright__ = 'Copyright (c) 2017 David Christenson <https://github.com/Noctem>'

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

def activate_hash_server(hash_token, conn_limit=300):
    HashServer.set_token(hash_token)
    HashServer.activate_session(conn_limit)
