from ctypes import c_int32, c_int64
from base64 import b64encode
from asyncio import get_event_loop, TimeoutError, CancelledError

from aiohttp import ClientSession, ClientError, DisconnectedError, HttpProcessingError

from .exceptions import ExpiredHashKeyException, HashingOfflineException, HashingQuotaExceededException, HashingTimeoutException, MalformedHashResponseException, TempHashingBanException, TimeoutException, UnexpectedHashResponseException
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
    status = {}
    _session = None
    loop = get_event_loop()

    def __init__(self, auth_token):
        self.auth_token = auth_token
        self.activate_session()

    async def hash(self, timestamp, latitude, longitude, accuracy, authticket, sessiondata, requests):
        self.location_hash = None
        self.location_auth_hash = None
        headers = {'X-AuthToken': self.auth_token}

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
                    response = await resp.json(encoding='ascii', loads=json.loads)
                except jexc as e:
                    raise MalformedHashResponseException('Unable to parse JSON from hash server.') from e
        except (TimeoutException, TimeoutError) as e:
            raise HashingTimeoutException('Hashing request timed out.') from e
        except (ClientError, DisconnectedError) as e:
            err = e.__cause__ or e
            raise HashingOfflineException('{} during hashing. {}'.format(err.__class__.__name__, e)) from e

        try:
            self.location_auth_hash = c_int32(response['locationAuthHash']).value
            self.location_hash = c_int32(response['locationHash']).value

            self.request_hashes = tuple(c_int64(x).value for x in response['requestHashes'])
        except CancelledError:
            raise
        except Exception as e:
            raise MalformedHashResponseException('Unable to load values from hash response.') from e

    @classmethod
    def activate_session(cls):
        if cls._session and not cls._session.closed:
            return
        headers = {'content-type': 'application/json',
                   'Accept': 'application/json',
                   'User-Agent': 'Python aiopogo'}
        conn = TimedConnector(loop=cls.loop,
                              limit=300,
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
