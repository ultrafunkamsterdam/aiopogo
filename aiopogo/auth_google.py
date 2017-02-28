import logging

from .auth import Auth
from .exceptions import AuthException, InvalidCredentialsException
from gpsoauth import perform_master_login, perform_oauth


class AuthGoogle(Auth):

    GOOGLE_LOGIN_ANDROID_ID = '9774d56d682e549c'
    GOOGLE_LOGIN_SERVICE= 'audience:server:client_id:848232511240-7so421jotr2609rmqakceuu1luuq0ptb.apps.googleusercontent.com'
    GOOGLE_LOGIN_APP = 'com.nianticlabs.pokemongo'
    GOOGLE_LOGIN_CLIENT_SIG = '321187995bc7cdc2b5fc91b11a96e2baa8602c62'

    def __init__(self, proxy=None):
        Auth.__init__(self)

        self._auth_provider = 'google'
        self._refresh_token = None
        self._proxy = proxy

    def set_proxy(self, proxy_config):
        self._proxy = proxy_config

    async def user_login(self, username, password):
        self.log.info('Google User Login for: {}'.format(username))

        if not isinstance(username, str) or not isinstance(password, str):
            raise InvalidCredentialsException("Username/password not correctly specified")

        user_login = perform_master_login(username, password, self.GOOGLE_LOGIN_ANDROID_ID, proxy=self._proxy)

        refresh_token = user_login.get('Token')

        if refresh_token is not None:
            self._refresh_token = refresh_token
            self.log.info('Google User Login successful.')
        else:
            self._refresh_token = None
            raise AuthException("Invalid Google Username/password")

        await self.get_access_token()
        return self._login

    def set_refresh_token(self, refresh_token):
        self.log.info('Google Refresh Token provided by user')
        self._refresh_token = refresh_token

    async def get_access_token(self, force_refresh=False):
        token_validity = self.check_access_token()

        if token_validity is True and force_refresh is False:
            self.log.debug('Using cached Google Access Token')
            return self._access_token
        else:
            if force_refresh:
                self.log.info('Forced request of Google Access Token!')
            else:
                self.log.info('Request Google Access Token...')

            token_data = perform_oauth(None, self._refresh_token, self.GOOGLE_LOGIN_ANDROID_ID, self.GOOGLE_LOGIN_SERVICE, self.GOOGLE_LOGIN_APP,
                self.GOOGLE_LOGIN_CLIENT_SIG, proxy=self._proxy)

            access_token = token_data.get('Auth')
            if access_token is not None:
                self._access_token = access_token
                self._access_token_expiry = int(token_data.get('Expiry', 0))
                self._login = True

                self.log.info('Google Access Token successfully received.')
                self.log.debug('Google Access Token: %s...', self._access_token[:25])
                return self._access_token
            else:
                self._access_token = None
                self._login = False
                raise AuthException("Could not receive a Google Access Token")
