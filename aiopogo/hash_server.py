from ctypes import c_int32, c_int64
from base64 import b64encode
from asyncio import get_event_loop, TimeoutError, CancelledError, sleep
from itertools import cycle
from time import time
from logging import getLogger

from aiohttp import ClientSession, ClientError, DisconnectedError, HttpProcessingError

from .exceptions import ExpiredHashKeyException, HashingOfflineException, HashingQuotaExceededException, HashingTimeoutException, MalformedHashResponseException, NoHashKeyException, TempHashingBanException, TimeoutException, UnexpectedHashResponseException
from .connector import TimedConnector

try:
    import ujson as json

    jargs = {'double_precision': 17, 'escape_forward_slashes': False}
    jexc = ValueError
except ImportError:
    import json
    from .utilities import JSONByteEncoder

    jargs = {'cls': JSONByteEncoder}
    jexc = (json.JSONDecodeError, ValueError)


class HashServer:
    endpoint = "https://pokehash.buddyauth.com/api/v127_4/hash"
    _session = None
    multi = False
    loop = get_event_loop()
    status = {}
    log = getLogger('hashing')

    def __init__(self):
        try:
            self.instance_token = self.auth_token
        except AttributeError:
            NoHashKeyException('You must provide a hash key before making a request.')

    async def hash(self, timestamp, latitude, longitude, accuracy, authticket, sessiondata, requests):
        status = self.key_status
        iteration = 0
        try:
            while status['remaining'] < 3 and time() < status['refresh']:
                if self.multi and iteration < self.multi:
                    self.instance_token = self.auth_token
                    status = self.key_status
                    iteration += 1
                else:
                    self.log.info('Out of hashes, waiting for new period.')
                    await sleep(status['refresh'] - time() + 1, loop=self.loop)
        except KeyError:
            pass
        headers = {'X-AuthToken': self.instance_token}

        payload = {
            'Timestamp': timestamp,
            'Latitude': latitude,
            'Longitude': longitude,
            'Altitude': accuracy,
            'AuthTicket': b64encode(authticket),
            'SessionData': b64encode(sessiondata),
            'Requests': tuple(b64encode(x.SerializeToString()) for x in requests)
        }
        payload = json.dumps(payload, **jargs)

        # request hashes from hashing server
        try:
            async with self._session.post(self.endpoint, headers=headers, data=payload) as resp:
                try:
                    resp.raise_for_status()
                except HttpProcessingError as e:
                    if e.code == 400:
                        if self.multi:
                            self.log.warning('{} expired, removing from rotation.'.format(self.instance_token))
                            self.remove_token(self.instance_token)
                            self.instance_token = self.auth_token
                            return self.hash(timestamp, latitude, longitude, accuracy, authticket, sessiondata, requests)
                        text = await resp.text()
                        raise ExpiredHashKeyException("Hash key appears to have expired. {}".format(text))
                    elif e.code == 403:
                        raise TempHashingBanException('Your IP was temporarily banned for sending too many requests with invalid keys')
                    elif e.code == 429:
                        status['remaining'] = 0
                        raise HashingQuotaExceededException("429: hashing quota exceeded.")
                    elif e.code >= 500:
                        raise HashingOfflineException('Hashing server error {}: {}'.format(e.code, e.message))
                    else:
                        raise UnexpectedHashResponseException('Unexpected hash code {}: {}'.format(e.code, e.message))

                headers = resp.headers

                try:
                    response = await resp.json(encoding='ascii', loads=json.loads)
                except jexc as e:
                    raise MalformedHashResponseException('Unable to parse JSON from hash server.') from e
        except (TimeoutException, TimeoutError) as e:
            raise HashingTimeoutException('Hashing request timed out.') from e
        except (ClientError, DisconnectedError) as e:
            err = e.__cause__ or e
            raise HashingOfflineException('{} during hashing. {}'.format(err.__class__.__name__, e)) from e

        try:
            status['remaining'] = int(headers['X-RateRequestsRemaining'])
            status['period'] = int(headers['X-RatePeriodEnd'])
            status['maximum'] = int(headers['X-MaxRequestCount'])
            status['expiration'] = int(headers['X-AuthTokenExpiration'])
            HashServer.status = status
        except (KeyError, TypeError, ValueError):
            pass

        try:
            self.location_auth_hash = c_int32(response['locationAuthHash']).value
            self.location_hash = c_int32(response['locationHash']).value
            self.request_hashes = tuple(c_int64(x).value for x in response['requestHashes'])
        except CancelledError:
            raise
        except Exception as e:
            raise MalformedHashResponseException('Unable to load values from hash response.') from e

    @property
    def _multi_token(self):
        return next(self._tokens)

    @property
    def _multi_status(self):
        return self.key_statuses[self.instance_token]

    @classmethod
    def activate_session(cls, conn_limit=300):
        if cls._session and not cls._session.closed:
            return
        headers = {'content-type': 'application/json',
                   'Accept': 'application/json',
                   'User-Agent': 'Python aiopogo'}
        conn = TimedConnector(loop=cls.loop,
                              limit=conn_limit,
                              verify_ssl=False,
                              conn_timeout=6)
        cls._session = ClientSession(connector=conn,
                                     loop=cls.loop,
                                     headers=headers)

    @classmethod
    def close_session(cls):
        if not cls._session or cls._session.closed:
            return
        cls._session.close()

    @classmethod
    def remove_token(cls, token):
        tokens = set(cls.key_statuses)
        tokens.discard(token)
        del cls.key_statuses[token]
        if len(tokens) > 1:
            cls.multi = len(tokens)
            cls._tokens = cycle(tokens)
        else:
            cls.multi = False
            cls.auth_token = tokens.pop()
            cls.key_status = cls.key_statuses[cls.auth_token]

    @classmethod
    def set_token(cls, token):
        if isinstance(token, (tuple, list, set, frozenset)) and len(token) > 1:
            cls._tokens = cycle(token)
            cls.auth_token = cls._multi_token
            cls.multi = len(token)
            cls.key_statuses = {t: {} for t in token}
            cls.key_status = cls._multi_status
        else:
            cls.auth_token = token
            cls.key_status = {}
