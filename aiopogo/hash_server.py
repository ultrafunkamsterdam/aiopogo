from ctypes import c_int32, c_int64
from base64 import b64encode
from asyncio import get_event_loop, TimeoutError, CancelledError, sleep
from itertools import cycle
from time import time
from logging import getLogger

from aiohttp import ClientSession, ClientError, ClientResponseError, ServerConnectionError, ServerTimeoutError

from . import json_dumps, json_loads
from .connector import TimedConnector
from .exceptions import BadHashRequestException, ExpiredHashKeyException, HashingOfflineException, HashingTimeoutException, MalformedHashResponseException, NoHashKeyException, TempHashingBanException, UnexpectedHashResponseException
from .utilities import f2i


class HashServer:
    _session = None
    multi = False
    loop = get_event_loop()
    status = {}
    log = getLogger('hashing')

    def __init__(self):
        try:
            self.instance_token = self.auth_token
        except AttributeError:
            NoHashKeyException(
                'You must provide a hash key before making a request.')

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
                    break
        except KeyError:
            pass
        headers = {'X-AuthToken': self.instance_token}

        payload = {
            'Timestamp': timestamp,
            'Latitude64': f2i(latitude),
            'Longitude64': f2i(longitude),
            'Accuracy64': f2i(accuracy),
            'AuthTicket': b64encode(authticket),
            'SessionData': b64encode(sessiondata),
            'Requests': [b64encode(x.SerializeToString()) for x in requests]
        }

        # request hashes from hashing server
        for attempt in range(3):
            try:
                async with self._session.post("http://pokehash.buddyauth.com/api/v137_1/hash", headers=headers, json=payload) as resp:
                    if resp.status == 400:
                        status['failures'] += 1

                        if status['failures'] < 10:
                            if attempt < 2:
                                await sleep(1.0)
                                continue
                            raise BadHashRequestException('400 was returned from the hashing server.')

                        if self.multi:
                            self.log.warning(
                                '{:.10}... expired, removing from rotation.'.format(
                                    self.instance_token))
                            self.remove_token(self.instance_token)
                            self.instance_token = self.auth_token
                            if attempt < 2:
                                headers = {'X-AuthToken': self.instance_token}
                                continue
                            return await self.hash(timestamp, latitude, longitude, accuracy, authticket, sessiondata, requests)
                        raise ExpiredHashKeyException("{:.10}... appears to have expired.".format(self.instance_token))

                    resp.raise_for_status()
                    status['failures'] = 0

                    response = await resp.json(encoding='ascii', loads=json_loads)
                    headers = resp.headers
                    break
            except ClientResponseError as e:
                if e.code == 403:
                    raise TempHashingBanException('Your IP was temporarily banned for sending too many requests with invalid keys')
                elif e.code == 429:
                    status['remaining'] = 0
                    self.instance_token = self.auth_token
                    return await self.hash(timestamp, latitude, longitude, accuracy, authticket, sessiondata, requests)
                elif e.code >= 500 or e.code == 404:
                    raise HashingOfflineException(
                        'Hashing server error {}: {}'.format(
                            e.code, e.message))
                else:
                    raise UnexpectedHashResponseException('Unexpected hash code {}: {}'.format(e.code, e.message))
            except ValueError as e:
                raise MalformedHashResponseException('Unable to parse JSON from hash server.') from e
            except (TimeoutError, ServerConnectionError, ServerTimeoutError) as e:
                if attempt < 2:
                    self.log.info('Hashing request timed out.')
                    await sleep(1.5)
                else:
                    raise HashingTimeoutException('Hashing request timed out.') from e
            except ClientError as e:
                error = '{} during hashing. {}'.format(e.__class__.__name__, e)
                if attempt < 2:
                    self.log.info(error)
                else:
                    raise HashingOfflineException(error) from e

        try:
            status['remaining'] = int(headers['X-RateRequestsRemaining'])
            status['period'] = int(headers['X-RatePeriodEnd'])
            status['maximum'] = int(headers['X-MaxRequestCount'])
            status['expiration'] = int(headers['X-AuthTokenExpiration'])
            HashServer.status = status
        except (KeyError, TypeError, ValueError):
            pass

        try:
            return (c_int32(response['locationHash']).value,
                    c_int32(response['locationAuthHash']).value,
                    [c_int64(x).value for x in response['requestHashes']])
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
        conn = TimedConnector(loop=cls.loop,
                              limit=conn_limit,
                              verify_ssl=False)
        headers = (('Content-Type', 'application/json'),
                   ('Accept', 'application/json'),
                   ('User-Agent', 'Python aiopogo'))
        cls._session = ClientSession(connector=conn,
                                     loop=cls.loop,
                                     headers=headers,
                                     raise_for_status=False,
                                     conn_timeout=4.5,
                                     json_serialize=json_dumps)

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
            cls.key_statuses = {t: {'failures': 0} for t in token}
            cls.key_status = cls._multi_status
        else:
            cls.auth_token = token
            cls.key_status = {'failures': 0}
