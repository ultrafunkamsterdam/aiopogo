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
from future.standard_library import install_aliases
install_aliases()

from urllib.parse import parse_qs, urlsplit
from six import string_types
from aiohttp import TCPConnector, ClientSession, ClientError, DisconnectedError, ProxyConnectionError
from json import JSONDecodeError
from asyncio import get_event_loop, TimeoutError
from concurrent.futures import TimeoutError as TimeoutError2
try:
    from aiosocks import SocksError
except ImportError:
    class SocksError(ProxyConnectionError): pass

from pogo_async.session import proxy_connector
from pogo_async.auth import Auth
from pogo_async.utilities import get_time
from pogo_async.exceptions import AuthException, AuthTimeoutException, InvalidCredentialsException


class AuthPtc(Auth):

    PTC_LOGIN_URL = 'https://sso.pokemon.com/sso/login?service=https%3A%2F%2Fsso.pokemon.com%2Fsso%2Foauth2.0%2FcallbackAuthorize'
    PTC_LOGIN_OAUTH = 'https://sso.pokemon.com/sso/oauth2.0/accessToken'
    PTC_LOGIN_CLIENT_SECRET = 'w8ScCUXJQc6kXKw8FiOhd8Fixzht18Dq3PEVkUCP5ZPxtgyWsbTvWHFLm2wNY0JR'
    loop = get_event_loop()

    def __init__(self, username=None, password=None, proxy=None, user_agent=None, timeout=None):
        Auth.__init__(self)

        self._auth_provider = 'ptc'
        self._session = None
        self._username = username
        self._password = password
        self.user_agent = user_agent or 'pokemongo/0 CFNetwork/758.5.3 Darwin/15.6.0'
        self.timeout = timeout or 10

        if proxy and proxy.startswith('socks'):
            self.socks_proxy = proxy
            self.proxy = None
        else:
            self.socks_proxy = None
            self.proxy = proxy

    def session_start(self):
        if self._session and not self._session.closed:
            return
        if self.socks_proxy:
            conn = proxy_connector(self.socks_proxy, loop=self.loop)
        else:
            conn = TCPConnector(loop=self.loop, verify_ssl=False)
        self._session = ClientSession(connector=conn,
                                      loop=self.loop,
                                      headers={'User-Agent': self.user_agent})

    def session_close(self):
        if self._session.closed:
            return
        self._session.close()

    async def user_login(self, username=None, password=None, retry=True):
        self._username = username or self._username
        self._password = password or self._password
        if not isinstance(self._username, string_types) or not isinstance(self._password, string_types):
            raise InvalidCredentialsException("Username/password not correctly specified")

        self.log.info('PTC User Login for: {}'.format(self._username))
        self.session_start()
        try:
            now = get_time()
            try:
                async with self._session.get(self.PTC_LOGIN_URL, timeout=self.timeout, proxy=self.proxy) as resp:
                    data = await resp.json()
            except (TimeoutError, TimeoutError2) as e:
                raise AuthTimeoutException('Auth GET timed out.') from e
            except (ProxyConnectionError, SocksError) as e:
                raise ProxyConnectionError from e
            except JSONDecodeError as e:
                raise AuthException('Unable to parse response') from e
            except (ClientError, DisconnectedError) as e:
                raise AuthException('Caught a client or disconnected error.') from e
            except Exception as e:
                raise AuthException('First request failed.') from e

            try:
                data.update({
                    '_eventId': 'submit',
                    'username': self._username,
                    'password': self._password,
                })
            except (ValueError, AttributeError) as e:
                raise AuthException('Invalid JSON response.') from e

            try:
                async with self._session.post(self.PTC_LOGIN_URL, data=data, timeout=self.timeout, proxy=self.proxy, allow_redirects=False) as resp:
                    qs = parse_qs(urlsplit(resp.headers['Location'])[3])
                    self._refresh_token = qs.get('ticket')[0]
                    self._access_token = resp.cookies['CASTGC'].value
            except (TimeoutError, TimeoutError2) as e:
                raise AuthTimeoutException('Auth POST timed out.') from e
            except (ProxyConnectionError, SocksError) as e:
                raise ProxyConnectionError from e
            except (ClientError, DisconnectedError) as e:
                raise AuthException('Caught a client or disconnected error.') from e
            except Exception as e:
                raise AuthException('Could not retrieve token.') from e

            if self._access_token:
                self._login = True
                self._access_token_expiry = int(now) + 7200
                self.log.info('PTC User Login successful.')
            elif self._refresh_token and retry:
                await self.get_access_token()
            else:
                self._login = False
                raise AuthException("Could not retrieve a PTC Access Token")
            return self._login
        finally:
            self.session_close()

    def set_refresh_token(self, refresh_token):
        self.log.info('PTC Refresh Token provided by user')
        self._refresh_token = refresh_token

    async def get_access_token(self, force_refresh=False):
        if force_refresh is False and self.check_access_token():
            self.log.debug('Using cached PTC Access Token')
            return self._access_token
        else:
            self.session_start()
            try:
                self._login = False
                if force_refresh:
                    self.log.info('Forced request of PTC Access Token!')
                else:
                    self.log.info('Request PTC Access Token...')

                data = {
                    'client_id': 'mobile-app_pokemon-go',
                    'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error',
                    'client_secret': self.PTC_LOGIN_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'code': self._refresh_token,
                }

                try:
                    async with self._session.post(self.PTC_LOGIN_OAUTH, data=data, timeout=self.timeout, proxy=self.proxy) as resp:
                        qs = await resp.text()
                    access_token = parse_qs(qs).get('access_token')
                except (TimeoutError, TimeoutError2) as e:
                    raise AuthTimeoutException('Auth POST timed out.') from e
                except (ProxyConnectionError, SocksError) as e:
                    raise ProxyConnectionError from e
                except (ClientError, DisconnectedError) as e:
                    raise AuthException('Caught a client or disconnected error.') from e
                except Exception as e:
                    raise AuthException('Could not retrieve token.') from e

                if access_token is not None:
                    self._access_token = access_token[0]

                    # set expiration to an hour less than value received because Pokemon OAuth
                    # login servers return an access token with an explicit expiry time of
                    # three hours, however, the token stops being valid after two hours.
                    # See issue #86
                    expires = int(token_data.get('expires', [0])[0]) - 3600
                    if expires > 0:
                        self._access_token_expiry = expires + get_time()
                    else:
                        self._access_token_expiry = 0

                    self._login = True

                    self.log.info('PTC Access Token successfully retrieved.')
                else:
                    self._access_token = None
                    self._login = False
                    if force_refresh and self._password:
                        self.log.info('Reauthenticating with refresh token failed, using credentials instead.')
                        return await self.user_login(retry=False)
                    raise AuthException("Could not retrieve a PTC Access Token")
            finally:
                self.session_close()
