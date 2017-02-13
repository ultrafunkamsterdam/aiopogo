"""
pgoapi - Pokemon Go API
Copyright (c) 2016 tjado <https://github.com/tejado>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.

Author: tjado <https://github.com/tejado>
"""

from __future__ import absolute_import

import re
import logging
import time

logging.getLogger('aiohttp.client').setLevel(40)

from . import __title__, __version__
from pogo_async.rpc_api import RpcApi, RpcState
from pogo_async.auth_ptc import AuthPtc
from pogo_async.auth_google import AuthGoogle
from pogo_async.utilities import parse_api_endpoint, get_time
from pogo_async.exceptions import AuthException, AuthTokenExpiredException, BadRequestException, BannedAccountException, InvalidCredentialsException, NoPlayerPositionSetException, NotLoggedInException, ServerApiEndpointRedirectException, ServerBusyOrOfflineException, UnexpectedResponseException

from . import protos
from pogoprotos.networking.requests.request_type_pb2 import RequestType

logger = logging.getLogger(__name__)


class PGoApi:
    def __init__(self, provider=None, position_lat=None, position_lng=None, position_alt=None, proxy_config=None, device_info=None):
        self.set_logger()
        self.log.info('%s v%s', __title__, __version__)

        self._auth_provider = None
        self._state = RpcState()

        self.set_api_endpoint("pgorelease.nianticlabs.com/plfe")

        self._position_lat = position_lat
        self._position_lng = position_lng
        self._position_alt = position_alt

        self._hash_server_token = None
        self.proxy = proxy_config
        self.device_info = device_info

    def set_logger(self, logger=None):
        self.log = logger or logging.getLogger(__name__)

    async def set_authentication(self, provider=None, oauth2_refresh_token=None, username=None, password=None, proxy_config=None, user_agent=None, timeout=None):
        if provider == 'ptc':
            self._auth_provider = AuthPtc(proxy=proxy_config or self.proxy, user_agent=user_agent, timeout=timeout)
        elif provider == 'google':
            self._auth_provider = AuthGoogle(proxy=proxy_config)
        elif provider is None:
            self._auth_provider = None
        else:
            raise InvalidCredentialsException("Invalid authentication provider - only ptc/google available.")

        if oauth2_refresh_token is not None:
            self._auth_provider.set_refresh_token(oauth2_refresh_token)
        elif username and password:
            if not await self._auth_provider.user_login(username, password):
                raise AuthException("User login failed!")
        else:
            raise InvalidCredentialsException("Invalid Credential Input - Please provide username/password or an oauth2 refresh token")

    def get_position(self):
        return (self._position_lat, self._position_lng, self._position_alt)

    def set_position(self, lat, lng, alt=None):
        self.log.debug('Set Position - Lat: %s Long: %s Alt: %s', lat, lng, alt)

        self._position_lat = lat
        self._position_lng = lng
        self._position_alt = alt

    def set_proxy(self, proxy_config):
        self.proxy = proxy_config

    def get_api_endpoint(self):
        return self._api_endpoint

    def set_api_endpoint(self, api_url):
        if api_url.startswith("https"):
            self._api_endpoint = api_url
        else:
            self._api_endpoint = parse_api_endpoint(api_url)

    def get_auth_provider(self):
        return self._auth_provider

    def get_state(self):
        return self._state

    def create_request(self):
        request = PGoApiRequest(self, self._position_lat, self._position_lng,
                                self._position_alt, self.device_info, self.proxy,
                                self._hash_server_token)
        return request

    def activate_hash_server(self, hash_server_token):
        self._hash_server_token = hash_server_token

    def get_hash_server_token(self):
        return self._hash_server_token

    async def __getattr__(self, func):
        async def function(**kwargs):
            request = self.create_request()
            getattr(request, func)(_call_direct=True, **kwargs )
            return await request.call()

        if func.upper() in RequestType.keys():
            return await function
        else:
            raise AttributeError('{} not known.'.format(func))


class PGoApiRequest:
    def __init__(self, parent, position_lat, position_lng, position_alt,
                 device_info=None, proxy_config=None, hash_token=None):
        self.log = logging.getLogger(__name__)

        self.__parent__ = parent

        # Inherit necessary parameters from parent
        self._api_endpoint = self.__parent__.get_api_endpoint()
        self._auth_provider = self.__parent__.get_auth_provider()
        self._state = self.__parent__.get_state()

        self._position = (position_lat, position_lng, position_alt)

        self._req_method_list = []
        self.device_info = device_info
        self.proxy = proxy_config
        self.hash_token = hash_token

    async def call(self):
        if self._position[0] is None or self._position[1] is None:
            raise NoPlayerPositionSetException('No position set.')

        try:
            if not self._auth_provider.is_login():
                await self._auth_provider.get_access_token()
        except AttributeError:
            raise NotLoggedInException('Not logged in.')

        request = RpcApi(self._auth_provider, self.device_info, self._state, proxy=self.proxy)

        if self.hash_token:
            request.set_api_version(0.55)
            request.activate_hash_server(self.hash_token)
        else:
            request.activate_hash_library()

        response = None
        execute = True
        while execute:
            execute = False

            try:
                response = await request.request(self._api_endpoint, self._req_method_list, self._position)
            except AuthTokenExpiredException:
                """
                This exception only occures if the OAUTH service provider (google/ptc) didn't send any expiration date
                so that we are assuming, that the access_token is always valid until the API server states differently.
                """
                self.log.info('Access Token rejected! Requesting new one...')
                await self._auth_provider.get_access_token(force_refresh=True)

                request.request_proto = None  # reset request and rebuild
                execute = True  # reexecute the call
            except ServerApiEndpointRedirectException as e:
                self.log.debug('API Endpoint redirect... re-execution of call')
                new_api_endpoint = e.get_redirected_endpoint()

                self._api_endpoint = parse_api_endpoint(new_api_endpoint)
                self.__parent__.set_api_endpoint(self._api_endpoint)

                execute = True  # reexecute the call

        # cleanup after call execution
        self._req_method_list = []

        return response

    def list_curr_methods(self):
        for i in self._req_method_list:
            print("{} ({})".format(RequestType.Name(i), i))

    def __getattr__(self, func):
        def function(**kwargs):

            if '_call_direct' in kwargs:
                del kwargs['_call_direct']
                self.log.debug('Creating a new direct request...')
            elif not self._req_method_list:
                self.log.debug('Creating a new request...')

            name = func.upper()
            if kwargs:
                self._req_method_list.append({RequestType.Value(name): kwargs})
                self.log.debug("Arguments of '%s': \n\r%s", name, kwargs)
            else:
                self._req_method_list.append(RequestType.Value(name))
                self.log.debug("Adding '%s' to RPC request", name)

            return self

        if func.upper() in RequestType.keys():
            return function
        else:
            raise AttributeError('{} not known.'.format(func))
