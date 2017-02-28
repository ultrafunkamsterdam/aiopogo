import logging

from .utilities import get_time, get_time_ms, get_format_time_diff

class Auth:

    def __init__(self):
        self.log = logging.getLogger(__name__)

        self._auth_provider = None

        self._login = False

        # oauth2 uses refresh tokens (which basically never expires)
        # to get an access_token which is only valid for a certain time)
        self._refresh_token = None
        self._access_token = None
        self._access_token_expiry = 0

        # Pokemon Go uses internal tickets, like an internal
        # session to keep a user logged in over a certain time (30 minutes)
        self._ticket_expire = None
        self._ticket_start = None
        self._ticket_end = None

    def get_name(self):
        return self._auth_provider

    def is_login(self):
        return self._login

    def get_token(self):
        return self._access_token

    def has_ticket(self):
        if self._ticket_expire and self._ticket_start and self._ticket_end:
            return True
        else:
            return False

    def set_ticket(self, params):
        self._ticket_expire, self._ticket_start, self._ticket_end = params

    def is_new_ticket(self, new_ticket_time_ms):
        if self._ticket_expire is None or new_ticket_time_ms > self._ticket_expire:
            return True
        else:
            return False

    def check_ticket(self):
        if self.has_ticket():
            now_ms = get_time_ms()
            if now_ms < (self._ticket_expire - 10000):
                return True
            else:
                self.log.debug('Removed expired Session Ticket (%s < %s)', now_ms, self._ticket_expire)
                self._ticket_expire, self._ticket_start, self._ticket_end = (None, None, None)
                return False
        else:
            return False

    def get_ticket(self):
        if self.check_ticket():
            return (self._ticket_expire, self._ticket_start, self._ticket_end)
        else:
            return False

    def user_login(self, username, password):
        raise NotImplementedError()

    def set_refresh_token(self, username, password):
        raise NotImplementedError()

    def get_access_token(self, force_refresh = False):
        raise NotImplementedError()

    def check_access_token(self):
        """
        Add few seconds to now so the token get refreshed 
        before it invalidates in the middle of the request
        """
        now_s = get_time() + 120

        if self._access_token is not None:
            if self._access_token_expiry == 0:
                self.log.debug('No Access Token Expiry found - assuming it is still valid!')
                return True
            elif self._access_token_expiry > now_s:
                h, m, s = get_format_time_diff(now_s, self._access_token_expiry, False)
                self.log.debug('Access Token still valid for further %02d:%02d:%02d hours (%s < %s)', h, m, s, now_s, self._access_token_expiry)
                return True
            else:
                self.log.info('Access Token expired!')
                return False
        else:
            self.log.debug('No Access Token available!')
            return False
