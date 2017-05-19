from asyncio import TimeoutError
from time import time
from html import unescape

from aiohttp import ClientRequest, ClientSession, ClientError, ClientHttpProxyError, ClientProxyConnectionError, ClientResponseError, ServerTimeoutError

from . import json_loads
from .session import SESSIONS, ProxyClientRequest
from .auth import Auth
from .exceptions import ActivationRequiredException, AuthConnectionException, AuthException, AuthTimeoutException, InvalidCredentialsException, ProxyException, SocksError, UnexpectedAuthError


class AuthPtc(Auth):
    def __init__(self, username=None, password=None, proxy=None,
                 proxy_auth=None, timeout=None, locale=None):
        Auth.__init__(self)
        self.provider = 'ptc'

        self._username = username
        self._password = password
        self.locale = locale or 'en_US'
        self.timeout = timeout or 10.0

        self.proxy = proxy
        self.socks = proxy and proxy.scheme in ('socks4', 'socks5')
        self.proxy_auth = proxy_auth

    async def user_login(self, username=None, password=None):
        self._username = username or self._username
        self._password = password or self._password

        try:
            assert (isinstance(self._username, str)
                    and isinstance(self._password, str))
        except AssertionError as e:
            raise InvalidCredentialsException(
                "Username/password not correctly specified") from e
        self.log.info('PTC User Login for: %s', self._username)

        try:
            now = time()
            async with ClientSession(
                    connector=SESSIONS.get_connector(self.socks),
                    loop=self.loop,
                    headers=(('User-Agent', 'niantic'),
                             ('Host', 'sso.pokemon.com')),
                    skip_auto_headers=('Accept', 'Accept-Encoding'),
                    request_class=ProxyClientRequest if self.socks else ClientRequest,
                    connector_owner=False,
                    raise_for_status=True,
                    conn_timeout=5.0,
                    read_timeout=self.timeout) as session:
                async with session.get('https://sso.pokemon.com/sso/oauth2.0/authorize', params={'client_id': 'mobile-app_pokemon-go', 'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error', 'locale': self.locale}, proxy=self.proxy, proxy_auth=self.proxy_auth) as resp:
                    data = await resp.json(loads=json_loads, encoding='utf-8', content_type=None)

                    assert 'lt' in data
                    data['_eventId'] = 'submit'
                    data['username'] = self._username
                    data['password'] = self._password
                    data['locale'] = self.locale

                async with session.post('https://sso.pokemon.com/sso/login', params={'service': 'http://sso.pokemon.com/sso/oauth2.0/callbackAuthorize'}, headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=data, timeout=8.0, proxy=self.proxy, proxy_auth=self.proxy_auth, allow_redirects=False) as resp:
                    try:
                        self._access_token = resp.cookies['CASTGC'].value
                    except (AttributeError, KeyError, TypeError):
                        try:
                            j = await resp.json(loads=json_loads, encoding='utf-8', content_type=None)
                        except ValueError as e:
                            raise AuthException('Unable to decode second response.') from e
                        try:
                            if j.get('error_code') == 'users.login.activation_required':
                                raise ActivationRequiredException('Account email not verified.')
                            error = j['errors'][0]
                            if 'unexpected error' in error:
                                raise UnexpectedAuthError('Unexpected auth error')
                            raise AuthException(unescape(error))
                        except (AttributeError, IndexError, KeyError, TypeError) as e:
                            raise AuthException('Unable to login or get error information.') from e
        except (ClientHttpProxyError, ClientProxyConnectionError, SocksError) as e:
            raise ProxyException('Proxy connection error during user_login.') from e
        except ClientResponseError as e:
            raise AuthConnectionException('Error {} during user_login: {}'.format(
                e.code, e.message))
        except (TimeoutError, ServerTimeoutError) as e:
            raise AuthTimeoutException('user_login timeout.') from e
        except ClientError as e:
            raise AuthConnectionException('{} during user_login.'.format(
                e.__class__.__name__)) from e
        except (AssertionError, TypeError, ValueError) as e:
            raise AuthException('Invalid initial JSON response.') from e

        if self._access_token:
            self.authenticated = True
            self._access_token_expiry = now + 7195.0
            self.log.info('PTC User Login successful.')

    async def get_access_token(self, force_refresh=False):
        if not force_refresh and self.check_access_token():
            self.log.debug('Using cached PTC Access Token')
            return self._access_token

        self._access_token = None
        self.authenticated = False
        await self.user_login()
        return self._access_token
