from __future__ import absolute_import

import json

from ctypes import c_int32, c_int64
from base64 import b64encode
from aiohttp import ClientError, DisconnectedError, HttpProcessingError

from asyncio import TimeoutError

from .hash_engine import HashEngine
from .exceptions import ExpiredHashKeyException, HashingOfflineException, HashingQuotaExceededException, HashingTimeoutException, MalformedHashResponseException, TempHashingBanException, TimeoutException, UnexpectedHashResponseException
from .session import Session
from .utilities import JSONByteEncoder


class HashServer(HashEngine):
    endpoint = "https://pokehash.buddyauth.com/api/v125/hash"
    status = {}
    timeout = 10

    def __init__(self, auth_token):
        self.headers = {'content-type': 'application/json', 'Accept' : 'application/json', 'User-Agent': 'Python pogo_async', 'X-AuthToken' : auth_token}
        self._session = Session.get()

    async def hash(self, timestamp, latitude, longitude, altitude, authticket, sessiondata, requests):
        self.location_hash = None
        self.location_auth_hash = None
        self.request_hashes = []

        payload = {
            'Timestamp': timestamp,
            'Latitude': latitude,
            'Longitude': longitude,
            'Altitude': altitude,
            'AuthTicket': b64encode(authticket),
            'SessionData': b64encode(sessiondata),
            'Requests': tuple(b64encode(x.SerializeToString()) for x in requests)
        }
        payload = json.dumps(payload, cls=JSONByteEncoder)

        # request hashes from hashing server
        try:
            async with self._session.post(self.endpoint, data=payload, headers=self.headers, timeout=self.timeout) as resp:
                try:
                    resp.raise_for_status()
                except HttpProcessingError as e:
                    if e.code == 400:
                        text = await resp.text()
                        raise ExpiredHashKeyException("Hash key appears to have expired. {}".format(text))
                    elif e.code == 403:
                        raise TempHashingBanException('Your IP was temporarily banned for sending too many requests with invalid keys')
                    elif e.code == 429:
                        raise HashingQuotaExceededException("429: hashing quota exceeded.")
                    elif e.code >= 500:
                        raise HashingOfflineException('Hashing server error {}: {}'.format(e.code, e.message))
                    else:
                        raise UnexpectedHashResponseException('Unexpected hash code {}: {}'.format(e.code, e.message))

                headers = resp.headers
                try:
                    self.status['remaining'] = int(headers['X-RateRequestsRemaining'])
                    self.status['period'] = int(headers['X-RatePeriodEnd'])
                    self.status['maximum'] = int(headers['X-MaxRequestCount'])
                    self.status['expiration'] = int(headers['X-AuthTokenExpiration'])
                except (KeyError, TypeError, ValueError):
                    pass

                try:
                    response = await resp.json()
                except (json.JSONDecodeError, ValueError) as e:
                    raise MalformedHashResponseException('Unable to parse JSON from hash server.') from e
        except (TimeoutException, TimeoutError) as e:
            raise HashingTimeoutException('Hashing request timed out.') from e
        except (ClientError, DisconnectedError) as e:
            raise HashingOfflineException('{} during hashing. {}'.format(e.__class__.__name__, e)) from e

        try:
            self.location_auth_hash = c_int32(response['locationAuthHash']).value
            self.location_hash = c_int32(response['locationHash']).value

            for request_hash in response['requestHashes']:
                self.request_hashes.append(c_int64(request_hash).value)
        except Exception as e:
            raise MalformedHashResponseException('Unable to load values from hash response.') from e
