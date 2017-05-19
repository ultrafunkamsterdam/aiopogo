from logging import getLogger
from time import time
from asyncio import get_event_loop

from .utilities import get_time_ms


class Auth:
    loop = get_event_loop()

    def __init__(self):
        self.log = getLogger(__name__)

        self.provider = None

        self.authenticated = False

        # oauth2 uses refresh tokens (which basically never expire)
        # to get an access_token which is only valid for a certain time)
        self._refresh_token = None
        self._access_token = None
        self._access_token_expiry = 0

        # Pokemon Go uses internal tickets, like an internal
        # session to keep a user logged in over a certain time (30 minutes)
        self._ticket_expire = 0
        self._ticket_start = None
        self._ticket_end = None

    def has_ticket(self):
        return self._ticket_expire and self._ticket_start and self._ticket_end

    def set_ticket(self, auth_ticket):
        timestamp = auth_ticket.expire_timestamp_ms
        if timestamp > self._ticket_expire:
            self._ticket_expire = timestamp
            self._ticket_start = auth_ticket.start
            self._ticket_end = auth_ticket.end

    def is_new_ticket(self, new_ticket_time_ms):
        return new_ticket_time_ms > self._ticket_expire

    def check_ticket(self):
        if get_time_ms() < (self._ticket_expire - 10000):
            return True
        self.log.debug(
            'Removed expired Session Ticket (%s)', self._ticket_expire)
        self._ticket_expire, self._ticket_start, self._ticket_end = 0, None, None
        return False

    def get_ticket(self):
        return self._ticket_expire, self._ticket_start, self._ticket_end

    def user_login(self, username, password):
        raise NotImplementedError

    def get_access_token(self, force_refresh=False):
        raise NotImplementedError

    def check_access_token(self):
        return self._access_token and self._access_token_expiry > time()
