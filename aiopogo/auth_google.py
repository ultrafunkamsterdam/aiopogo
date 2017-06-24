from concurrent.futures import ThreadPoolExecutor
from functools import partial
from time import time

try:
    from gpsoauth import perform_master_login, perform_oauth
except ImportError:
    def perform_master_login(*args, **kwargs):
        raise ImportError('Must install gpsoauth to use Google accounts')
    perform_oauth = perform_master_login

from .auth import Auth
from .exceptions import AuthException, InvalidCredentialsException


class AuthGoogle(Auth):
    GOOGLE_LOGIN_ANDROID_ID = '9774d56d682e549c'
    GOOGLE_LOGIN_SERVICE = 'audience:server:client_id:848232511240-7so421jotr2609rmqakceuu1luuq0ptb.apps.googleusercontent.com'
    GOOGLE_LOGIN_APP = 'com.nianticlabs.pokemongo'
    GOOGLE_LOGIN_CLIENT_SIG = '321187995bc7cdc2b5fc91b11a96e2baa8602c62'

    def __init__(self, proxy=None, refresh_token=None):
        Auth.__init__(self)

        self.provider = 'google'
        self._refresh_token = refresh_token
        self._proxy = proxy

    async def user_login(self, username, password):
        self.log.info('Google User Login for: %s', username)

        try:
            assert (isinstance(username, str)
                    and isinstance(password, str))
        except AssertionError:
            raise InvalidCredentialsException(
                "Username/password not correctly specified")

        login = partial(
            perform_master_login,
            username,
            password,
            self.GOOGLE_LOGIN_ANDROID_ID,
            proxy=self._proxy)

        with ThreadPoolExecutor(max_workers=1) as executor:
            user_login = await self.loop.run_in_executor(executor, login)

        try:
            self._refresh_token = user_login['Token']
        except KeyError:
            raise AuthException("Invalid Google Username/password")

        await self.get_access_token()

    async def get_access_token(self, force_refresh=False):
        if not force_refresh and self.check_access_token():
            self.log.debug('Using cached Google access token')
            return self._access_token

        self._access_token = None
        self.authenticated = False
        self.log.info('Requesting Google access token...')

        oauth = partial(perform_oauth, None, self._refresh_token,
                        self.GOOGLE_LOGIN_ANDROID_ID, self.GOOGLE_LOGIN_SERVICE,
                        self.GOOGLE_LOGIN_APP, self.GOOGLE_LOGIN_CLIENT_SIG,
                        proxy=self._proxy)
        with ThreadPoolExecutor(max_workers=1) as executor:
            token_data = await self.loop.run_in_executor(executor, oauth)

        try:
            self._access_token = token_data['Auth']
        except KeyError:
            self._access_token = None
            self.authenticated = False
            raise AuthException("Could not receive a Google Access Token")

        try:
            self._access_token_expiry = float(token_data['Expiry'])
        except KeyError:
            self._access_token_expiry = time() + 7200.0
        self.authenticated = True
        self.log.info('Google Access Token successfully received.')
        self.log.debug('Google Access Token: %s...',
                       self._access_token[:25])
        return self._access_token
