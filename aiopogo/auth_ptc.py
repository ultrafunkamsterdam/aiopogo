from urllib.parse import parse_qs, urlsplit
from asyncio import TimeoutError, CancelledError
from time import time
from functools import partial
from html import unescape

from aiohttp import TCPConnector, ClientSession, ClientError, DisconnectedError, HttpProcessingError

from . import json_loads
from .session import socks_connector
from .auth import Auth
from .exceptions import ActivationRequiredException, AuthConnectionException, AuthException, AuthTimeoutException, InvalidCredentialsException, ProxyException, SocksError, TimeoutException, UnexpectedAuthError

class AuthPtc(Auth):
    def __init__(self, username=None, password=None, proxy=None, timeout=None, locale=None):
        Auth.__init__(self)

        self.provider = 'ptc'
        self._session = None
        self._username = username
        self._password = password
        self.timeout = timeout or 10
        self.locale = locale or 'en_US'

        if proxy and proxy.startswith('socks'):
            self.conn = partial(socks_connector, proxy=proxy, loop=self.loop)
            self.proxy = None
        else:
            self.conn = partial(TCPConnector, loop=self.loop, verify_ssl=False, conn_timeout=5.0)
            self.proxy = proxy

        self.sess = partial(
            ClientSession,
            loop=self.loop,
            headers=(('User-Agent', 'niantic'), ('Host', 'sso.pokemon.com')),
            skip_auto_headers=('Accept', 'Accept-Encoding'))

    async def user_login(self, username=None, password=None):
        self._username = username or self._username
        self._password = password or self._password

        self.log.info('PTC User Login for: {}'.format(self._username))
        try:
            assert isinstance(self._username, str) and isinstance(self._password, str)

            now = time()
            async with self.sess(connector=self.conn()) as session:
                async with session.get('https://sso.pokemon.com/sso/oauth2.0/authorize', params={'client_id': 'mobile-app_pokemon-go', 'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error', 'locale': self.locale}, timeout=self.timeout, proxy=self.proxy) as resp:
                    resp.raise_for_status()
                    data = await resp.json(loads=json_loads, encoding='utf-8')

                try:
                    data['_eventId'] = 'submit'
                    data['username'] = self._username
                    data['password'] = self._password
                    data['locale'] = self.locale
                except TypeError as e:
                    raise AuthException('Invalid initial JSON response.') from e

                async with session.post('https://sso.pokemon.com/sso/login', params={'service': 'http://sso.pokemon.com/sso/oauth2.0/callbackAuthorize'}, headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=data, timeout=8.0, proxy=self.proxy, allow_redirects=False) as resp:
                    resp.raise_for_status()
                    try:
                        self._access_token = resp.cookies['CASTGC'].value
                    except (KeyError, IndexError):
                        try:
                            j = await resp.json(loads=json_loads)
                        except ValueError as e:
                            raise AuthException('Unable to decode second response.') from e
                        try:
                            if j.get('error_code') == 'users.login.activation_required':
                                raise ActivationRequiredException('Account email not verified.')
                            error = j['errors'][0]
                            if 'unexpected error' in error:
                                raise UnexpectedAuthError('Unexpected auth error')
                            raise AuthException(unescape(error))
                        except (AttributeError, KeyError, IndexError) as e:
                            raise AuthException('Unable to login or get error information.') from e

                if self._access_token:
                    self.authenticated = True
                    self._access_token_expiry = now + 7195.0
                    self.log.info('PTC User Login successful.')
        except HttpProcessingError as e:
            raise AuthConnectionException('Error {} during user_login: {}'.format(e.code, e.message))
        except (TimeoutError, TimeoutException) as e:
            raise AuthTimeoutException('user_login timeout.') from e
        except (ProxyException, SocksError) as e:
            raise ProxyException('Proxy connection error during user_login.') from e
        except ValueError as e:
            raise AuthException('Unable to parse user_login response.') from e
        except (ClientError, DisconnectedError) as e:
            err = e.__cause__ or e
            raise AuthConnectionException('{} during user_login.'.format(err.__class__.__name__)) from e
        except AssertionError as e:
            raise InvalidCredentialsException("Username/password not correctly specified") from e
        except (AuthException, CancelledError):
            raise
        except Exception as e:
            raise AuthException('{} during user_login.'.format(e.__class__.__name__)) from e

    async def get_access_token(self, force_refresh=False):
        if not force_refresh and self.check_access_token():
            self.log.debug('Using cached PTC Access Token')
            return self._access_token
        else:
            self._access_token = None
            self.authenticated = False
            await self.user_login()
            return self._access_token
