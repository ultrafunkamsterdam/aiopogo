from urllib.parse import parse_qs, urlsplit
from asyncio import get_event_loop, TimeoutError, CancelledError

from aiohttp import TCPConnector, ClientSession, ClientError, DisconnectedError, HttpProcessingError
try:
    from aiosocks.errors import SocksError
except ImportError:
    class SocksError(Exception): pass

from .session import socks_connector, CONN_TIMEOUT
from .auth import Auth
from .utilities import get_time
from .exceptions import ActivationRequiredException, AuthConnectionException, AuthException, AuthTimeoutException, InvalidCredentialsException, ProxyException, TimeoutException

try:
    import ujson as json

    jexc = ValueError
except ImportError:
    import json

    jexc = jexc = (json.JSONDecodeError, ValueError)

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

    def activate_session(self):
        if self._session and not self._session.closed:
            return
        if self.socks_proxy:
            conn = socks_connector(self.socks_proxy, loop=self.loop)
        else:
            conn = TCPConnector(loop=self.loop, verify_ssl=False, conn_timeout=CONN_TIMEOUT)
        self._session = ClientSession(connector=conn,
                                      loop=self.loop,
                                      headers={'User-Agent': self.user_agent})

    def close_session(self):
        if self._session.closed:
            return
        self._session.close()

    async def user_login(self, username=None, password=None, retry=True):
        self._username = username or self._username
        self._password = password or self._password
        self._login = False
        if not isinstance(self._username, str) or not isinstance(self._password, str):
            raise InvalidCredentialsException("Username/password not correctly specified")

        self.log.info('PTC User Login for: {}'.format(self._username))
        self._access_token = None
        self.activate_session()
        try:
            now = get_time()
            async with self._session.get(self.PTC_LOGIN_URL, timeout=self.timeout, proxy=self.proxy) as resp:
                resp.raise_for_status()
                data = await resp.json(loads=json.loads)

            try:
                data['_eventId'] = 'submit'
                data['username'] = self._username
                data['password'] = self._password
            except TypeError as e:
                raise AuthException('Invalid initial JSON response.') from e

            async with self._session.post(self.PTC_LOGIN_URL, data=data, timeout=self.timeout, proxy=self.proxy, allow_redirects=False) as resp:
                resp.raise_for_status()
                try:
                    qs = parse_qs(urlsplit(resp.headers['Location'])[3])
                    self._refresh_token = qs['ticket'][0]
                    self._access_token = resp.cookies['CASTGC'].value
                except KeyError:
                    try:
                        j = await resp.json(loads=json.loads)
                    except jexc as e:
                        raise AuthException('Unable to decode second response.') from e
                    try:
                        if j.get('error_code') == 'users.login.activation_required':
                            raise ActivationRequiredException('Account email not verified.')
                        error = j['errors'][0]
                        raise AuthException(error)
                    except (AttributeError, KeyError, IndexError) as e:
                        raise AuthException('Unable to login or get error information.') from e

            if self._access_token:
                self._login = True
                self._access_token_expiry = now + 7200
                self.log.info('PTC User Login successful.')
            elif self._refresh_token and retry:
                return await self.get_access_token()
            return self._access_token
        except HttpProcessingError as e:
            raise AuthConnectionException('Error {} during user_login: {}'.format(e.code, e.message))
        except (TimeoutError, TimeoutException) as e:
            raise AuthTimeoutException('user_login timeout.') from e
        except (ProxyException, SocksError) as e:
            raise ProxyException('Proxy connection error during user_login.') from e
        except jexc as e:
            raise AuthException('Unable to parse user_login response.') from e
        except (ClientError, DisconnectedError) as e:
            err = e.__cause__ or e
            raise AuthConnectionException('{} during user_login.'.format(err.__class__.__name__)) from e
        except (AuthException, CancelledError):
            raise
        except Exception as e:
            raise AuthException('{} during user_login.'.format(e.__class__.__name__)) from e
        finally:
            self.close_session()

    def set_refresh_token(self, refresh_token):
        self.log.info('PTC Refresh Token provided by user')
        self._refresh_token = refresh_token

    async def get_access_token(self, force_refresh=False):
        if force_refresh is False and self.check_access_token():
            self.log.debug('Using cached PTC Access Token')
            return self._access_token
        elif self._refresh_token is None:
            return await self.user_login()
        else:
            self.activate_session()
            try:
                self._login = False
                self._access_token = None
                if force_refresh:
                    self.log.info('Forced request of PTC Access Token!')
                else:
                    self.log.info('Request PTC Access Token...')

                data = {
                    'client_id': 'mobile-app_pokemon-go',
                    'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error',
                    'client_secret': self.PTC_LOGIN_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'code': self._refresh_token
                }

                async with self._session.post(self.PTC_LOGIN_OAUTH, data=data, timeout=self.timeout, proxy=self.proxy) as resp:
                    self._refresh_token = None
                    resp.raise_for_status()
                    qs = await resp.text()
                token_data = parse_qs(qs)
                try:
                    self._access_token = token_data['access_token'][0]
                except (KeyError, IndexError):
                    return await self.user_login(retry=False)

                if self._access_token is not None:
                    # set expiration to an hour less than value received because Pokemon OAuth
                    # login servers return an access token with an explicit expiry time of
                    # three hours, however, the token stops being valid after two hours.
                    # See issue #86
                    try:
                        self._access_token_expiry = token_data['expires'][0] - 3600 + get_time()
                    except (KeyError, IndexError, TypeError):
                        self._access_token_expiry = 0

                    self._login = True

                    self.log.info('PTC Access Token successfully retrieved.')
                    return self._access_token
                else:
                    self.log.info('Authenticating with refresh token failed, using credentials instead.')
                    return await self.user_login(retry=False)
            except HttpProcessingError as e:
                raise AuthConnectionException('Error {} while fetching access token: {}'.format(e.code, e.message))
            except (TimeoutError, TimeoutException) as e:
                raise AuthTimeoutException('Access token request timed out.') from e
            except (ProxyException, SocksError) as e:
                raise ProxyException('Proxy connection error while fetching access token.') from e
            except (ClientError, DisconnectedError) as e:
                raise AuthConnectionException('{} while fetching access token.'.format(e.__class__.__name__)) from e
            except (AuthException, CancelledError):
                raise
            except Exception as e:
                raise AuthException('{} while fetching access token.'.format(e.__class__.__name__)) from e
            finally:
                self.close_session()
