from __future__ import absolute_import

import ctypes
import base64
import json

from aiohttp import ClientError, DisconnectedError
from asyncio import TimeoutError
from concurrent.futures import TimeoutError as TimeoutError2

from pogo_async.hash_engine import HashEngine
from pogo_async.exceptions import BadHashRequestException, HashingOfflineException, HashingQuotaExceededException, HashingTimeoutException, MalformedHashResponseException, TempHashingBanException, UnexpectedHashResponseException
from pogo_async.session import Session

class HashServer(HashEngine):
    endpoint = "https://pokehash.buddyauth.com/api/v121_2/hash"
    status = {}
    timeout = 15

    def __init__(self, auth_token):
        self.headers = {'content-type': 'application/json', 'Accept' : 'application/json', 'User-Agent': 'Python pogo_async', 'X-AuthToken' : auth_token}
        self._session = Session.get()

    async def hash(self, timestamp, latitude, longitude, altitude, authticket, sessiondata, requestslist):
        self.location_hash = None
        self.location_auth_hash = None
        self.request_hashes = []

        payload = {}
        payload["Timestamp"] = timestamp
        payload["Latitude"] = latitude
        payload["Longitude"] = longitude
        payload["Altitude"] = altitude
        payload["AuthTicket"] = base64.b64encode(authticket).decode('ascii')
        payload["SessionData"] = base64.b64encode(sessiondata).decode('ascii')
        payload["Requests"] = []
        for request in requestslist:
            payload["Requests"].append(base64.b64encode(request.SerializeToString()).decode('ascii'))

        payload = json.dumps(payload)

        # request hashes from hashing server

        try:
            async with self._session.post(self.endpoint, data=payload, headers=self.headers, timeout=self.timeout) as resp:
                if resp.status == 400:
                    text = await resp.text()
                    raise BadHashRequestException("400: Bad request, error: {}".format(text))
                elif resp.status == 403:
                    raise TempHashingBanException('Your IP was temporarily banned for sending too many requests with invalid keys')
                elif resp.status == 429:
                    raise HashingQuotaExceededException("429: Request limited.")
                elif resp.status in (502, 503, 504):
                    raise HashingOfflineException('{} Server Error'.format(resp.status))
                elif resp.status != 200:
                    text = await resp.text()
                    error = 'Unexpected HTTP server response - needs 200 got {c}. {t}'.format(
                        c=resp.status, t=text)
                    raise UnexpectedHashResponseException(error)

                headers = resp.headers
                try:
                    self.status['period'] = int(headers.get('X-RatePeriodEnd'))
                    self.status['remaining'] = int(headers.get('X-RateRequestsRemaining'))
                    self.status['maximum'] = int(headers.get('X-MaxRequestCount'))
                except TypeError:
                    pass

                try:
                    response_parsed = await resp.json()
                except (json.JSONDecodeError, ValueError) as e:
                    raise MalformedHashResponseException('Unable to parse JSON from hash server.') from e
        except (TimeoutError, TimeoutError2) as e:
            raise HashingTimeoutException('Hashing request timed out.') from e
        except (ClientError, DisconnectedError) as e:
            raise HashingOfflineException('Caught client or disconnected error.') from e

        try:
            self.location_auth_hash = ctypes.c_int32(response_parsed['locationAuthHash']).value
            self.location_hash = ctypes.c_int32(response_parsed['locationHash']).value

            for request_hash in response_parsed['requestHashes']:
                self.request_hashes.append(ctypes.c_int64(request_hash).value)
        except Exception as e:
            raise MalformedHashResponseException('Unable to load values') from e
