from urllib.parse import parse_qs, urlsplit
from asyncio import get_event_loop, TimeoutError
from time import time
from functools import partial

from aiohttp import TCPConnector, ClientSession, ClientError, ClientHttpProxyError, ClientProxyConnectionError, ClientResponseError, ServerTimeoutError

from . import json_loads
from .session import socks_connector
from .auth import Auth
from .exceptions import ActivationRequiredException, AuthConnectionException, AuthException, AuthTimeoutException, InvalidCredentialsException, ProxyException, SocksError, TimeoutException

class AuthPtc(Auth):
    loop = get_event_loop()

    def __init__(self, username=None, password=None, proxy=None, user_agent=None, timeout=None):
        Auth.__init__(self)
        self._auth_provider = 'ptc'

        self._username = username
        self._password = password

        if proxy and proxy.startswith('socks'):
            self.conn = partial(socks_connector, proxy=proxy, loop=self.loop)
            self.proxy = None
        else:
            self.conn = partial(TCPConnector, loop=self.loop, verify_ssl=False)
            self.proxy = proxy

        self.session = partial(
            ClientSession,
            loop=self.loop,
            headers=(('User-Agent', user_agent or 'pokemongo/0 CFNetwork/758.5.3 Darwin/15.6.0'),),
            raise_for_status=True,
            conn_timeout=5.0,
            read_timeout=timeout or 10.0)

    async def user_login(self, username=None, password=None, retry=True):
        self._username = username or self._username
        self._password = password or self._password
        self._login = False
        if not isinstance(self._username, str) or not isinstance(self._password, str):
            raise InvalidCredentialsException("Username/password not correctly specified")

        self.log.info('PTC User Login for: {}'.format(self._username))
        self._access_token = None
        try:
            now = time()
            login_url = 'https://sso.pokemon.com/sso/oauth2.0/authorize?client_id=mobile-app_pokemon-go&redirect_uri=https%3A%2F%2Fwww.nianticlabs.com%2Fpokemongo%2Ferror&locale=en'
            async with self.session(connector=self.conn()) as session:
                async with session.get(login_url, proxy=self.proxy) as resp:
                    data = await resp.json(encoding='utf-8', loads=json_loads, content_type=None)

                assert 'lt' in data
                data['_eventId'] = 'submit'
                data['username'] = self._username
                data['password'] = self._password

                login_url = 'https://sso.pokemon.com/sso/login?service=https%3A%2F%2Fsso.pokemon.com%2Fsso%2Foauth2.0%2FcallbackAuthorize&locale=en'
                async with session.post(login_url, data=data, proxy=self.proxy, allow_redirects=False) as resp:
                    try:
                        qs = parse_qs(urlsplit(resp.headers['Location'])[3])
                        self._refresh_token = qs['ticket'][0]
                        self._access_token = resp.cookies['CASTGC'].value
                    except (KeyError, AttributeError, TypeError, IndexError):
                        try:
                            j = await resp.json(encoding='utf-8', loads=json_loads, content_type=None)
                        except ValueError as e:
                            raise AuthException('Unable to decode second response.') from e
                        try:
                            if j.get('error_code') == 'users.login.activation_required':
                                raise ActivationRequiredException('Account email not verified.')
                            error = j['errors'][0]
                            raise AuthException(error)
                        except (KeyError, AttributeError, TypeError, IndexError) as e:
                            raise AuthException('Unable to login or get error information.') from e
        except (ClientHttpProxyError, ClientProxyConnectionError, SocksError) as e:
            raise ProxyException('Proxy connection error during user_login.') from e
        except ClientResponseError as e:
            raise AuthConnectionException('Error {} during user_login: {}'.format(e.code, e.message))
        except (TimeoutError, ServerTimeoutError) as e:
            raise AuthTimeoutException('user_login timeout.') from e
        except ClientError as e:
            raise AuthConnectionException('{} during user_login.'.format(e.__class__.__name__)) from e
        except (AssertionError, TypeError, ValueError) as e:
            raise AuthException('Invalid initial JSON response.') from e

        if self._access_token:
            self._login = True
            self._access_token_expiry = now + 7200.0
            self.log.info('PTC User Login successful.')
        elif self._refresh_token and retry:
            return await self.get_access_token()
        return self._access_token

    async def get_access_token(self, force_refresh=False):
        if not force_refresh and self.check_access_token():
            self.log.debug('Using cached PTC Access Token')
            return self._access_token
        elif self._refresh_token is None:
            return await self.user_login()
        else:
            self._login = False
            self._access_token = None
            self.log.info('Request PTC Access Token...')

            data = {
                'code': self._refresh_token,
                'grant_type': 'refresh_token',
                'client_secret': 'w8ScCUXJQc6kXKw8FiOhd8Fixzht18Dq3PEVkUCP5ZPxtgyWsbTvWHFLm2wNY0JR',
                'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error',
                'client_id': 'mobile-app_pokemon-go'
            }

            try:
                async with self.session(connector=self.conn()) as session:
                    async with session.post('https://sso.pokemon.com/sso/oauth2.0/accessToken', data=data, proxy=self.proxy) as resp:
                        self._refresh_token = None
                        qs = await resp.text()
            except (ClientHttpProxyError, ClientProxyConnectionError, SocksError) as e:
                raise ProxyException('Proxy connection error while fetching access token.') from e
            except ClientResponseError as e:
                raise AuthConnectionException('Error {} while fetching access token: {}'.format(e.code, e.message))
            except (TimeoutError, ServerTimeoutError) as e:
                raise AuthTimeoutException('Access token request timed out.') from e
            except ClientError as e:
                raise AuthConnectionException('{} while fetching access token.'.format(e.__class__.__name__)) from e

            token_data = parse_qs(qs)
            try:
                self._access_token = token_data['access_token'][0]
                assert self._access_token is not None
            except (KeyError, IndexError, AssertionError):
                self.log.info('Authenticating with refresh token failed, using credentials instead.')
                return await self.user_login(retry=False)

            # set expiration to an hour less than value received because Pokemon OAuth
            # login servers return an access token with an explicit expiry time of
            # three hours, however, the token stops being valid after two hours.
            # See issue #86
            try:
                self._access_token_expiry = token_data['expires'][0] - 3600 + time()
            except (KeyError, IndexError, TypeError):
                self._access_token_expiry = 0

            self._login = True

            self.log.info('PTC Access Token successfully retrieved.')
            return self._access_token
