from logging import getLogger

from . import __title__, __version__
from .rpc_api import RpcApi, RpcState
from .auth_ptc import AuthPtc
from .auth_google import AuthGoogle
from .utilities import parse_api_endpoint
from .hash_server import HashServer
from .exceptions import AuthException, AuthTokenExpiredException, InvalidCredentialsException, NoPlayerPositionSetException, NotLoggedInException, ServerApiEndpointRedirectException
from .protos.pogoprotos.networking.requests.request_type_pb2 import RequestType



class PGoApi:
    log = getLogger(__name__)
    log.info('%s v%s', __title__, __version__)

    def __init__(self, provider=None, lat=None, lon=None, alt=None, proxy=None, device_info=None):
        self.auth_provider = None
        self.state = RpcState()

        self._api_endpoint = 'https://pgorelease.nianticlabs.com/plfe/rpc'

        self.latitude = lat
        self.longitude = lon
        self.altitude = alt

        self.proxy = proxy
        self.device_info = device_info

    async def set_authentication(self, provider=None, oauth2_refresh_token=None, username=None, password=None, proxy=None, user_agent=None, timeout=None):
        if provider == 'ptc':
            self.auth_provider = AuthPtc(proxy=proxy or self.proxy, user_agent=user_agent, timeout=timeout)
        elif provider == 'google':
            self.auth_provider = AuthGoogle(proxy=proxy)
        elif provider is None:
            self.auth_provider = None
        else:
            raise InvalidCredentialsException("Invalid authentication provider - only ptc/google available.")

        if oauth2_refresh_token is not None:
            self.auth_provider.set_refresh_token(oauth2_refresh_token)
        elif username and password:
            if not await self.auth_provider.user_login(username, password):
                raise AuthException("User login failed!")
        else:
            raise InvalidCredentialsException("Invalid Credential Input - Please provide username/password or an oauth2 refresh token")

    def set_position(self, lat, lon, alt=None):
        self.log.debug('Set Position - Lat: %s Lon: %s Alt: %s', lat, lon, alt)

        self.latitude = lat
        self.longitude = lon
        self.altitude = alt

    def create_request(self):
        return PGoApiRequest(self)

    def activate_hash_server(self, hash_token, conn_limit=300):
        HashServer.set_token(hash_token)
        HashServer.activate_session(conn_limit=conn_limit)

    @property
    def position(self):
        return self.latitude, self.longitude, self.altitude

    @property
    def api_endpoint(self):
        return self._api_endpoint

    @api_endpoint.setter
    def api_endpoint(self, api_url):
        if api_url.startswith("https"):
            self._api_endpoint = api_url
        else:
            self._api_endpoint = parse_api_endpoint(api_url)

    @property
    def start_time(self):
        return self.state.start_time

    def __getattr__(self, func):
        async def function(**kwargs):
            request = self.create_request()
            getattr(request, func)(**kwargs)
            return await request.call()

        if func.upper() in RequestType.keys():
            return function
        else:
            raise AttributeError('{} not known.'.format(func))


class PGoApiRequest:
    log = getLogger(__name__)

    def __init__(self, parent):
        self.__parent__ = parent
        self._req_method_list = []

    async def call(self):
        parent = self.__parent__
        auth_provider = parent.auth_provider
        position = parent.position
        try:
            assert position[0] is not None and position[1] is not None
        except AssertionError:
            raise NoPlayerPositionSetException('No position set.')
        try:
            assert auth_provider.is_login()
        except (AssertionError, AttributeError):
            raise NotLoggedInException('Not logged in.')


        request = RpcApi(auth_provider, parent.state)
        while True:
            try:
                response = await request.request(parent.api_endpoint, self._req_method_list, position, parent.device_info, parent.proxy)
                break
            except AuthTokenExpiredException:
                """
                This exception only occures if the OAUTH service provider (google/ptc) didn't send any expiration date
                so that we are assuming, that the access_token is always valid until the API server states differently.
                """
                self.log.info('Access Token rejected! Requesting new one...')
                await auth_provider.get_access_token(force_refresh=True)
            except ServerApiEndpointRedirectException as e:
                self.log.debug('API Endpoint redirect... re-execution of call')
                parent.api_endpoint = e.get_redirected_endpoint()

        # cleanup after call execution
        self._req_method_list = []

        return response

    def list_curr_methods(self):
        for i in self._req_method_list:
            print("{} ({})".format(RequestType.Name(i), i))

    def __getattr__(self, func):
        def function(**kwargs):
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
