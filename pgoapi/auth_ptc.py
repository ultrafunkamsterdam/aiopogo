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

import re
import json
import logging

from urllib.parse import parse_qs
from six import string_types
from aiohttp import TCPConnector, ClientSession, ClientResponseError
from asyncio import get_event_loop

from pgoapi.auth import Auth
from pgoapi.utilities import get_time
from pgoapi.exceptions import AuthException, InvalidCredentialsException


class AuthPtc(Auth):

    PTC_LOGIN_URL = 'https://sso.pokemon.com/sso/login?service=https%3A%2F%2Fsso.pokemon.com%2Fsso%2Foauth2.0%2FcallbackAuthorize'
    PTC_LOGIN_OAUTH = 'https://sso.pokemon.com/sso/oauth2.0/accessToken'
    PTC_LOGIN_CLIENT_SECRET = 'w8ScCUXJQc6kXKw8FiOhd8Fixzht18Dq3PEVkUCP5ZPxtgyWsbTvWHFLm2wNY0JR'
    loop = get_event_loop()
    _connector = TCPConnector(limit=100, loop=loop)
    _session = ClientSession(connector=_connector,
                             loop=loop,
                             headers={'User-Agent': 'pokemongo/0 CFNetwork/758.5.3 Darwin/15.6.0'})

    def __init__(self):
        super().__init__()

        self._auth_provider = 'ptc'
        self.proxy = None

    def set_proxy(self, proxy_config):
        self.proxy = proxy_config

    async def user_login(self, username, password):
        self.log.info('PTC User Login for: {}'.format(username))

        if not isinstance(username, string_types) or not isinstance(password, string_types):
            raise InvalidCredentialsException("Username/password not correctly specified")

        try:
            async with self._session.get(self.PTC_LOGIN_URL, timeout=30, proxy=self.proxy) as resp:
                jdata = await resp.json()
        except ClientResponseError as e:
            raise AuthException('Caught ConnectionError.') from e
        except json.JSONDecodeError as e:
            raise AuthException('Unable to parse response') from e

        try:
            data = {
                'lt': jdata['lt'],
                'execution': jdata['execution'],
                '_eventId': 'submit',
                'username': username,
                'password': password,
            }
        except (ValueError, KeyError) as e:
            raise AuthException('PTC User Login Error - Field missing in response.') from e

        ticket = None
        try:
            async with self._session.post(self.PTC_LOGIN_URL, data=data, proxy=self.proxy) as r1:
                ticket = re.sub('.*ticket=', '', r1.history[0].headers['Location'])
        except Exception as e:
            raise AuthException('Could not retrieve token!') from e

        self._refresh_token = ticket
        self.log.info('PTC User Login successful.')

        await self.get_access_token()
        return self._login

    def set_refresh_token(self, refresh_token):
        self.log.info('PTC Refresh Token provided by user')
        self._refresh_token = refresh_token

    async def get_access_token(self, force_refresh=False):
        token_validity = self.check_access_token()

        if token_validity is True and force_refresh is False:
            self.log.debug('Using cached PTC Access Token')
            return self._access_token
        else:
            if force_refresh:
                self.log.info('Forced request of PTC Access Token!')
            else:
                self.log.info('Request PTC Access Token...')

            data1 = {
                'client_id': 'mobile-app_pokemon-go',
                'redirect_uri': 'https://www.nianticlabs.com/pokemongo/error',
                'client_secret': self.PTC_LOGIN_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'code': self._refresh_token,
            }

            try:
                async with self._session.post(self.PTC_LOGIN_OAUTH, data=data1, proxy=self.proxy) as r2:
                    qs = await r2.text()
                token_data = parse_qs(qs)
            except Exception as e:
                raise AuthException('Could not retrieve qs!') from e

            access_token = token_data.get('access_token', None)
            if access_token is not None:
                self._access_token = access_token[0]

                now_s = get_time()
                # set expiration to an hour less than value received because Pokemon OAuth
                # login servers return an access token with an explicit expiry time of
                # three hours, however, the token stops being valid after two hours.
                # See issue #86
                expires = int(token_data.get('expires', [0])[0]) - 3600
                if expires > 0:
                    self._access_token_expiry = expires + now_s
                else:
                    self._access_token_expiry = 0

                self._login = True

                self.log.info('PTC Access Token successfully retrieved.')
                self.log.debug('PTC Access Token: {}...'.format(self._access_token[:25]))
            else:
                self._access_token = None
                self._login = False
                raise AuthException("Could not retrieve a PTC Access Token")
