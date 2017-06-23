from array import array
from asyncio import TimeoutError
from enum import Enum
from importlib import import_module
from logging import getLogger
from os import urandom

from aiohttp import ClientError, ClientHttpProxyError, ClientProxyConnectionError, ClientResponseError, ServerTimeoutError
from cyrandom import choose_weighted, randint, random, triangular, triangular_int, uniform
from google.protobuf.message import DecodeError
from pycrypt import pycrypt

from .exceptions import *
from .hash_server import HashServer
from .session import SESSIONS
from .utilities import to_camel_case, get_time_ms, IdGenerator

from .pogoprotos.networking.envelopes.request_envelope_pb2 import RequestEnvelope
from .pogoprotos.networking.envelopes.response_envelope_pb2 import ResponseEnvelope
from .pogoprotos.networking.envelopes.signal_log_pb2 import SignalLog
from .pogoprotos.networking.platform.requests.send_encrypted_signature_request_pb2 import SendEncryptedSignatureRequest
from .pogoprotos.networking.platform.requests.plat_eight_request_pb2 import PlatEightRequest
from .pogoprotos.networking.platform.responses.plat_eight_response_pb2 import PlatEightResponse
from .pogoprotos.networking.requests.request_type_pb2 import RequestType


class RpcApi:
    log = getLogger(__name__)

    def __init__(self, auth_provider, state):
        self._auth_provider = auth_provider
        self.state = state

    async def _make_rpc(self, endpoint, proto, proxy, proxy_auth, _sessions=SESSIONS):
        try:
            async with _sessions.get(proxy).post(endpoint, data=proto.SerializeToString(), proxy=proxy, proxy_auth=proxy_auth) as resp:
                return await resp.read()
        except (ClientHttpProxyError, ClientProxyConnectionError, SocksError) as e:
            raise ProxyException(
                'Proxy connection error during RPC request.') from e
        except ClientResponseError as e:
            if e.code == 400:
                raise BadRequestException(
                    "400: Bad RPC request. {}".format(
                        e.message))
            elif e.code == 403:
                raise NianticIPBannedException(
                    "Seems your IP Address is banned or something else went badly wrong.")
            elif e.code >= 500:
                raise NianticOfflineException(
                    '{} Niantic server error: {}'.format(
                        e.code, e.message))
            else:
                raise UnexpectedResponseException(
                    'Unexpected RPC response: {}, {}'.format(
                        e.code, e.message))
        except (TimeoutError, ServerTimeoutError) as e:
            raise NianticTimeoutException('RPC request timed out.') from e
        except ClientError as e:
            raise NianticOfflineException(
                '{} during RPC. {}'.format(
                    e.__class__.__name__, e)) from e

    @staticmethod
    def get_request_name(subrequests):
        try:
            first = subrequests[0]
            return to_camel_case(RequestType.Name(
                first[0] if isinstance(first, tuple) else first))
        except IndexError:
            return 'empty'
        except (ValueError, TypeError):
            return 'unknown'

    async def request(self, endpoint, subrequests, player_position, device_info=None, proxy=None, proxy_auth=None):
        request_proto = await self._build_main_request(subrequests, player_position, device_info)

        response = await self._make_rpc(endpoint, request_proto, proxy, proxy_auth)
        return self._parse_response(response, subrequests)

    async def _build_main_request(self, subrequests, player_position, device_info=None):
        self.log.debug('Generating main RPC request...')

        request = RequestEnvelope()
        request.status_code = 2

        request.request_id = self.state.request_id

        # 5: 43%, 10: 30%, 30: 5%, 50: 4%, 65: 10%, 200: 1%, float: 7%
        request.accuracy = choose_weighted(
            (5, 10, 30, 50, 65, 200, -1),
            (43, 73, 78, 82, 92, 93, 100))
        if request.accuracy == -1:
            request.accuracy = uniform(65, 200)

        request.latitude, request.longitude, altitude = player_position

        # generate sub requests before SignalLog generation
        request = self._build_sub_requests(request, subrequests)

        if self._auth_provider.check_ticket():
            self.log.debug(
                'Found Session Ticket - using this instead of oauth token')
            request.auth_ticket.expire_timestamp_ms, request.auth_ticket.start, request.auth_ticket.end = self._auth_provider.get_ticket()
            ticket_serialized = request.auth_ticket.SerializeToString()
        else:
            self.log.debug(
                'No Session Ticket found - using OAUTH Access Token')
            request.auth_info.provider = self._auth_provider.provider
            request.auth_info.token.contents = await self._auth_provider.get_access_token()

            # 59: 50%, others: 5% each
            request.auth_info.token.unknown2 = choose_weighted(
                (4, 19, 22, 26, 30, 44, 45, 50, 57, 58, 59),
                (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20))
            # Sig uses this when no auth_ticket available
            ticket_serialized = request.auth_info.SerializeToString()

        sig = SignalLog()

        sig.field22 = self.state.session_hash
        sig.epoch_timestamp_ms = get_time_ms()
        if not self.state.start_time:
            self.state.start_time = sig.epoch_timestamp_ms - randint(6000, 10000)
        sig.timestamp_ms_since_start = sig.epoch_timestamp_ms - self.state.start_time

        hash_engine = HashServer()
        hashing = HashServer.loop.create_task(
            hash_engine.hash(
                sig.epoch_timestamp_ms,
                request.latitude,
                request.longitude,
                request.accuracy,
                ticket_serialized,
                sig.field22,
                request.requests))

        loc = sig.location_updates.add()
        sen = sig.sensor_updates.add()

        sen.timestamp = sig.timestamp_ms_since_start - triangular_int(93, 4900, 3000)
        loc.timestamp_ms = sig.timestamp_ms_since_start - triangular_int(320, 3000, 1000)

        loc.name = 'fused'
        loc.latitude = request.latitude
        loc.longitude = request.longitude

        loc.altitude = altitude or uniform(150, 250)

        if random() > .85:
            # no reading for roughly 1 in 7 updates
            loc.device_course = -1
            loc.device_speed = -1
        else:
            loc.device_course = self.state.course
            loc.device_speed = triangular(0.25, 9.7, 8.2)

        loc.provider_status = 3
        loc.location_type = 1
        if isinstance(request.accuracy, float):
            loc.horizontal_accuracy = choose_weighted(
                (request.accuracy, 65, 200), (50, 90, 100))
            loc.vertical_accuracy = choose_weighted(
                (-1, 10, 12, 16, 24, 32, 48, 96),
                (50, 84, 89, 92, 96, 98, 99, 100))
        else:
            loc.horizontal_accuracy = request.accuracy
            if request.accuracy >= 10:
                loc.vertical_accuracy = choose_weighted(
                    (6, 8, 10, 12, 16, 24, 32, 48),
                    (4, 38, 73, 84, 88, 96, 99, 100))
            else:
                loc.vertical_accuracy = choose_weighted(
                    (3, 4, 6, 8, 10, 12),
                    (15, 54, 68, 81, 95, 100))

        if loc.vertical_accuracy == -1:
            loc.vertical_accuracy = uniform(10, 96)

        sen.acceleration_x = triangular(-1.5, 2.5, 0)
        sen.acceleration_y = triangular(-1.2, 1.4, 0)
        sen.acceleration_z = triangular(-1.4, .9, 0)
        sen.magnetic_field_accuracy = choose_weighted(
            (-1, 0, 1, 2),
            (8, 10, 52, 100))
        if sen.magnetic_field_accuracy == -1:
            sen.magnetic_field_x = 0
            sen.magnetic_field_y = 0
            sen.magnetic_field_z = 0
        else:
            sen.magnetic_field_x = self.state.magnetic_field_x
            sen.magnetic_field_y = self.state.magnetic_field_y
            sen.magnetic_field_z = self.state.magnetic_field_z

        sen.attitude_pitch = triangular(-1.56, 1.57, 0.475)
        sen.attitude_yaw = triangular(-1.56, 3.14, .1)
        sen.attitude_roll = triangular(-3.14, 3.14, 0)
        sen.rotation_rate_x = triangular(-3.2, 3.52, 0)
        sen.rotation_rate_y = triangular(-3.1, 4.88, 0)
        sen.rotation_rate_z = triangular(-6, 3.7, 0)
        sen.gravity_x = triangular(-1, 1, 0.01)
        sen.gravity_y = triangular(-1, 1, -.4)
        sen.gravity_z = triangular(-1, 1, -.4)
        sen.status = 3

        sig.version_hash = 0x4AE22D4661C83701

        try:
            for key, value in device_info.items():
                setattr(sig.device_info, key, value)
        except AttributeError:
            pass
        sig.ios_device_info.bool5 = True

        try:
            rtype = request.requests[0].request_type
        except (IndexError, AttributeError):
            pass
        else:
            randval = random()
            # GetMapObjects or GetPlayer: 50%
            # Encounter: 10%
            # Others: 3%
            if ((rtype in (2, 106) and randval > 0.5)
                    or (rtype == 102 and randval > 0.9)
                    or randval > 0.97):
                plat8 = PlatEightRequest()
                if self.state.message8:
                    plat8.field1 = self.state.message8
                plat = request.platform_requests.add()
                plat.type = 8
                plat.request_message = plat8.SerializeToString()

        sig.location_hash, sig.location_hash_by_token_seed, rh = await hashing
        sig.request_hashes.extend(rh)
        sig_request = SendEncryptedSignatureRequest()
        sig_request.encrypted_signature = pycrypt(
            sig.SerializeToString(), sig.timestamp_ms_since_start)

        plat = request.platform_requests.add()
        plat.type = 6
        plat.request_message = sig_request.SerializeToString()

        request.ms_since_last_locationfix = sig.timestamp_ms_since_start - loc.timestamp_ms

        self.log.debug('Generated protobuf request: \n\r%s', request)
        return request

    def _build_sub_requests(self, mainrequest, subrequest_list):
        self.log.debug('Generating sub RPC requests...')

        for entry in subrequest_list:
            if isinstance(entry, int):
                subrequest = mainrequest.requests.add()
                subrequest.request_type = entry
            else:
                entry_id, entry_content = entry

                proto_name = RequestType.Name(entry_id).lower() + '_message'

                try:
                    class_ = globals()[proto_name]
                except KeyError:
                    globals()[proto_name] = class_ = getattr(
                        import_module(
                            'pogoprotos.networking.requests.messages.' +
                            proto_name +
                            '_pb2'),
                        to_camel_case(proto_name))

                message = class_()

                for key, value in entry_content.items():
                    if isinstance(value, (list, tuple, array)):
                        self.log.debug(
                            "Found sequence: %s - trying as repeated", key)
                        try:
                            r = getattr(message, key)
                            r.extend(value)
                        except (AttributeError, ValueError) as e:
                            self.log.warning('Unknown argument %s inside %s (Exception: %s)', key, proto_name, e)
                    elif isinstance(value, dict):
                        r = getattr(message, key)
                        for k, v in value.items():
                            try:
                                setattr(r, k, v)
                            except (AttributeError, ValueError) as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, str(value), proto_name, e)
                    else:
                        try:
                            setattr(message, key, value)
                        except (AttributeError, ValueError) as e:
                            self.log.warning('Argument %s with value %s inside %s should be a sequence.', key, value, proto_name)
                            try:
                                self.log.debug("%s -> %s", key, value)
                                getattr(message, key).append(value)
                            except (AttributeError, ValueError) as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, value, proto_name, e)

                subrequest = mainrequest.requests.add()
                subrequest.request_type = entry_id
                subrequest.request_message = message.SerializeToString()

        return mainrequest

    def _parse_response(self, response_raw, subrequests):
        self.log.debug('Parsing main RPC response...')

        response_proto = ResponseEnvelope()
        try:
            response_proto.ParseFromString(response_raw)
        except DecodeError as e:
            raise MalformedNianticResponseException(
                'Could not parse response.') from e

        self.log.debug(
            'Protobuf structure of rpc response:\n\r%s',
            response_proto)

        if response_proto.HasField('auth_ticket'):
            self._auth_provider.set_ticket(response_proto.auth_ticket)

        if not self.state.message8:
            for plat_response in response_proto.platform_returns:
                if plat_response.type == 8:
                    resp = PlatEightResponse()
                    resp.ParseFromString(plat_response.response)
                    self.state.message8 = resp.message
                    break

        # some response validations
        status_code = response_proto.status_code
        if status_code in (1, 2):
            return self._parse_sub_responses(subrequests, response_proto)
        elif status_code == 53:
            raise ServerApiEndpointRedirectException(response_proto.api_url)
        elif status_code == 102:
            raise AuthTokenExpiredException
        elif status_code == 3:
            req_type = self.get_request_name(subrequests)
            raise BadRPCException("Bad Request on {}".format(req_type))
        else:
            try:
                err = StatusCode(status_code).name
            except ValueError:
                raise UnexpectedResponseException(
                    "Unknown status_code: {}".format(status_code))
            req_type = self.get_request_name(subrequests)
            raise InvalidRPCException("{} on {}.".format(err, req_type))

    def _parse_sub_responses(self, subrequests_list, response_proto):
        self.log.debug('Parsing sub RPC responses...')
        responses = {}

        for i, subresponse in enumerate(response_proto.returns):
            request_entry = subrequests_list[i]

            entry_name = RequestType.Name(
                request_entry if isinstance(
                    request_entry, int) else request_entry[0])
            proto_name = entry_name.lower() + '_response'

            try:
                class_ = globals()[proto_name]
            except KeyError:
                globals()[proto_name] = class_ = getattr(
                    import_module(
                        'pogoprotos.networking.responses.' +
                        proto_name +
                        '_pb2'),
                    to_camel_case(proto_name))

            message = class_()
            message.ParseFromString(subresponse)
            responses[entry_name] = message
        return responses


class RpcState:
    def __init__(self):
        self.start_time = None
        self.id_gen = IdGenerator()
        self.session_hash = urandom(16)
        self.mag_x_min = uniform(-80, 60)
        self.mag_x_max = self.mag_x_min + 20
        self.mag_y_min = uniform(-120, 90)
        self.mag_y_max = self.mag_y_min + 30
        self.mag_z_min = uniform(-70, 40)
        self.mag_z_max = self.mag_y_min + 15
        self._course = uniform(0, 359.99)
        self.message8 = None

    @property
    def request_id(self):
        return self.id_gen.request_id()

    @property
    def magnetic_field_x(self):
        return uniform(self.mag_x_min, self.mag_x_max)

    @property
    def magnetic_field_y(self):
        return uniform(self.mag_y_min, self.mag_y_max)

    @property
    def magnetic_field_z(self):
        return uniform(self.mag_z_min, self.mag_z_max)

    @property
    def course(self):
        self._course = triangular(0, 359.99, self._course)
        return self._course


class StatusCode(Enum):
    Unknown = 0
    Okay = 1
    OkayWithURL = 2
    BadRequest = 3
    InvalidRequest = 51
    InvalidPlatformRequest = 52
    Redirect = 53
    SessionInvalidated = 100
    InvalidAuthToken = 102
