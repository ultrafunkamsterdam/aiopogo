from importlib import import_module
from asyncio import TimeoutError
from array import array
from os import urandom
from logging import getLogger
from struct import Struct
from enum import Enum

from google.protobuf import message
from protobuf_to_dict import protobuf_to_dict
from aiohttp import ClientError, DisconnectedError, HttpProcessingError
from pycrypt import pycrypt
try:
    from aiosocks.errors import SocksError
except ImportError:
    class SocksError(Exception): pass

from .exceptions import *
from .utilities import to_camel_case, get_time_ms, IdGenerator, CustomRandom
from .hash_server import HashServer
from .session import SessionManager

from .protos.pogoprotos import networking
from networking.envelopes.request_envelope_pb2 import RequestEnvelope
from networking.envelopes.response_envelope_pb2 import ResponseEnvelope
from networking.requests.request_type_pb2 import RequestType
from networking.envelopes.signal_log_pb2 import SignalLog
from networking.platform.requests.send_encrypted_signature_request_pb2 import SendEncryptedSignatureRequest
from networking.platform.requests.plat_eight_request_pb2 import PlatEightRequest
from networking.platform.responses.plat_eight_response_pb2 import PlatEightResponse


RPC_SESSIONS = SessionManager()
rand = CustomRandom()


class RpcApi:
    log = getLogger(__name__)

    def __init__(self, auth_provider, device_info, state, proxy=None):
        self._auth_provider = auth_provider
        self.state = state

        self._hash_engine = HashServer()
        if proxy and proxy.startswith('socks'):
            self._session = RPC_SESSIONS.get(proxy)
            self.proxy = None
        else:
            self._session = RPC_SESSIONS.get()
            self.proxy = proxy

        self.token2 = None
        self.device_info = device_info

    def get_class(self, cls):
        module_, class_ = cls.rsplit('.', 1)
        class_ = getattr(import_module(module_), to_camel_case(class_))
        return class_

    async def _make_rpc(self, endpoint, request_proto_plain):
        self.log.debug('Execution of RPC')

        request_proto_serialized = request_proto_plain.SerializeToString()
        try:
            async with self._session.post(endpoint, data=request_proto_serialized, proxy=self.proxy) as resp:
                resp.raise_for_status()

                content = await resp.read()
        except HttpProcessingError as e:
            if e.code == 400:
                raise BadRequestException("400: Bad RPC request. {}".format(e.message))
            elif e.code == 403:
                raise NianticIPBannedException("Seems your IP Address is banned or something else went badly wrong.")
            elif e.code >= 500:
                raise NianticOfflineException('{} Niantic server error: {}'.format(e.code, e.message))
            else:
                raise UnexpectedResponseException('Unexpected RPC response: {}, '.format(e.code, e.message))
        except (ProxyException, SocksError) as e:
            raise ProxyException('Proxy connection error during RPC request.') from e
        except (TimeoutException, TimeoutError) as e:
            raise NianticTimeoutException('RPC request timed out.') from e
        except (ClientError, DisconnectedError) as e:
            err = e.__cause__ or e
            raise NianticOfflineException('{} during RPC. {}'.format(err.__class__.__name__, e)) from e
        return content

    @staticmethod
    def get_request_name(subrequests):
        try:
            first = subrequests[0]
            if isinstance(first, dict):
                num = tuple(first.keys())[0]
            else:
                num = first
            return to_camel_case(RequestType.Name(num))
        except IndexError:
            return 'empty'
        except (ValueError, TypeError):
            return 'unknown'

    async def request(self, endpoint, subrequests, player_position):

        if not self._auth_provider or self._auth_provider.is_login() is False:
            raise NotLoggedInException

        request_proto = await self._build_main_request(subrequests, player_position)

        response = await self._make_rpc(endpoint, request_proto)
        response_dict = self._parse_main_response(response, subrequests)

        self.check_authentication(response_dict)

        # some response validations
        try:
            status_code = response_dict['status_code']
            if status_code in (1, 2):
                return response_dict

            if status_code == 102:
                raise AuthTokenExpiredException
            elif status_code == 53:
                api_url = response_dict['api_url']
                exception = ServerApiEndpointRedirectException()
                exception.set_redirected_endpoint(api_url)
                raise exception
            elif status_code == 3:
                req_type = self.get_request_name(subrequests)
                raise BadRPCException("Bad Request on {}".format(req_type))
            else:
                err = StatusCode(status_code).name
                req_type = self.get_request_name(subrequests)
                raise InvalidRPCException("{} on {}.".format(err, req_type))
        except ValueError:
            raise UnexpectedResponseException("Unknown status_code: {}".format(status_code))
        except (TypeError, KeyError):
            req_type = self.get_request_name(subrequests)
            raise UnexpectedResponseException('Could not parse status_code from {} response.'.format(req_type))

    def check_authentication(self, response_dict):
        try:
            auth_ticket = response_dict['auth_ticket']
            timestamp = auth_ticket['expire_timestamp_ms']
            if self._auth_provider.is_new_ticket(timestamp):
                had_ticket = self._auth_provider.has_ticket()

                self._auth_provider.set_ticket(
                    (timestamp, auth_ticket['start'], auth_ticket['end']))
        except (TypeError, KeyError):
            return

    async def _build_main_request(self, subrequests, player_position=None):
        self.log.debug('Generating main RPC request...')

        request = RequestEnvelope()
        request.status_code = 2

        request.request_id = self.state.request_id

        # 5: 43%, 10: 30%, 30: 5%, 50: 4%, 65: 10%, 200: 1%, float: 7%
        request.accuracy = rand.choose_weighted(
            (5, 10, 30, 50, 65, 200, -1),
            (43, 73, 78, 82, 92, 93, 100))
        if request.accuracy == -1:
            request.accuracy = rand.uniform(65, 200)

        if player_position:
            request.latitude, request.longitude, altitude = player_position

        # generate sub requests before SignalLog generation
        request = self._build_sub_requests(request, subrequests)

        ticket = self._auth_provider.get_ticket()
        if ticket:
            self.log.debug('Found Session Ticket - using this instead of oauth token')
            request.auth_ticket.expire_timestamp_ms, request.auth_ticket.start, request.auth_ticket.end = ticket
            ticket_serialized = request.auth_ticket.SerializeToString()
        else:
            self.log.debug('No Session Ticket found - using OAUTH Access Token')
            request.auth_info.provider = self._auth_provider.get_name()
            request.auth_info.token.contents = await self._auth_provider.get_access_token()

            if not self.token2:
                # 59: 50%, others: 5% each
                self.token2 = rand.choose_weighted(
                    (4, 19, 22, 26, 30, 44, 45, 50, 57, 58, 59),
                    (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20))
            request.auth_info.token.unknown2 = self.token2
            ticket_serialized = request.auth_info.SerializeToString()  # Sig uses this when no auth_ticket available

        sig = SignalLog()

        sig.field22 = self.state.session_hash
        sig.epoch_timestamp_ms = get_time_ms()
        if not self.state.start_time:
            self.state.start_time = sig.epoch_timestamp_ms - rand.randint(6000, 10000)
        sig.timestamp_ms_since_start = sig.epoch_timestamp_ms - self.state.start_time

        loop = HashServer.loop
        hashing = loop.create_task(self._hash_engine.hash(sig.epoch_timestamp_ms, request.latitude, request.longitude, request.accuracy, ticket_serialized, sig.field22, request.requests))

        loc = sig.location_updates.add()
        sen = sig.sensor_updates.add()

        sen.timestamp = sig.timestamp_ms_since_start - rand.triangular_int(93, 4900, 3000)
        loc.timestamp_ms = sig.timestamp_ms_since_start - rand.triangular_int(320, 3000, 1000)

        loc.name = 'fused'
        loc.latitude = request.latitude
        loc.longitude = request.longitude

        loc.altitude = altitude or rand.uniform(150, 250)

        if rand.random() > .85:
            # no reading for roughly 1 in 7 updates
            loc.device_course = -1
            loc.device_speed = -1
        else:
            loc.device_course = self.state.course
            loc.device_speed = rand.triangular(0.25, 9.7, 8.2)

        loc.provider_status = 3
        loc.location_type = 1
        if isinstance(request.accuracy, float):
            loc.horizontal_accuracy = rand.choose_weighted((request.accuracy, 65, 200), (50, 90, 100))
            loc.vertical_accuracy = rand.choose_weighted(
                (-1, 10, 12, 16, 24, 32, 48, 96),
                (50, 84, 89, 92, 96, 98, 99, 100))
        else:
            loc.horizontal_accuracy = request.accuracy
            if request.accuracy >= 10:
                loc.vertical_accuracy = rand.choose_weighted(
                    (6, 8, 10, 12, 16, 24, 32, 48),
                    (4, 38, 73, 84, 88, 96, 99, 100))
            else:
                loc.vertical_accuracy = rand.choose_weighted(
                    (3, 4, 6, 8, 10, 12),
                    (15, 54, 68, 81, 95, 100))

        if loc.vertical_accuracy == -1:
            loc.vertical_accuracy = rand.uniform(10, 96)

        sen.acceleration_x = rand.triangular(-1.5, 2.5, 0)
        sen.acceleration_y = rand.triangular(-1.2, 1.4, 0)
        sen.acceleration_z = rand.triangular(-1.4, .9, 0)
        sen.magnetic_field_accuracy = rand.choose_weighted(
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

        sen.attitude_pitch = rand.triangular(-1.56, 1.57, 0.475)
        sen.attitude_yaw = rand.triangular(-1.56, 3.14, .1)
        sen.attitude_roll = rand.triangular(-3.14, 3.14, 0)
        sen.rotation_rate_x = rand.triangular(-3.2, 3.52, 0)
        sen.rotation_rate_y = rand.triangular(-3.1, 4.88, 0)
        sen.rotation_rate_z = rand.triangular(-6, 3.7, 0)
        sen.gravity_x = rand.triangular(-1, 1, 0.01)
        sen.gravity_y = rand.triangular(-1, 1, -.4)
        sen.gravity_z = rand.triangular(-1, 1, -.4)
        sen.status = 3

        sig.version_hash = -816976800928766045

        try:
            for key in self.device_info:
                setattr(sig.device_info, key, self.device_info[key])
        except TypeError:
            pass
        sig.ios_device_info.bool5 = True

        try:
            rtype = request.requests[0].request_type
        except (IndexError, AttributeError):
            pass
        else:
            randval = rand.random()
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

        await hashing

        sig.location_hash_by_token_seed = self._hash_engine.location_auth_hash
        sig.location_hash = self._hash_engine.location_hash
        for req_hash in self._hash_engine.request_hashes:
            sig.request_hashes.append(req_hash)

        signature_proto = sig.SerializeToString()
        sig_request = SendEncryptedSignatureRequest()
        sig_request.encrypted_signature = pycrypt(signature_proto, sig.timestamp_ms_since_start, 3)
        plat = request.platform_requests.add()
        plat.type = 6
        plat.request_message = sig_request.SerializeToString()

        request.ms_since_last_locationfix = sig.timestamp_ms_since_start - loc.timestamp_ms

        self.log.debug('Generated protobuf request: \n\r%s', request)
        return request

    def _build_sub_requests(self, mainrequest, subrequest_list):
        self.log.debug('Generating sub RPC requests...')

        for entry in subrequest_list:
            if isinstance(entry, dict):
                entry_id = tuple(entry.items())[0][0]
                entry_content = entry[entry_id]

                entry_name = RequestType.Name(entry_id)

                proto_name = entry_name.lower() + '_message'
                proto_classname = 'pogoprotos.networking.requests.messages.' + proto_name + '_pb2.' + proto_name
                subrequest_extension = self.get_class(proto_classname)()

                self.log.debug("Subrequest class: %s", proto_classname)

                for key, value in entry_content.items():
                    if isinstance(value, (list, tuple, array)):
                        self.log.debug("Found sequence: %s - trying as repeated", key)
                        for i in value:
                            try:
                                self.log.debug("%s -> %s", key, i)
                                r = getattr(subrequest_extension, key)
                                r.append(i)
                            except (AttributeError, ValueError) as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, i, proto_name, e)
                    elif isinstance(value, dict):
                        for k in value.keys():
                            try:
                                r = getattr(subrequest_extension, key)
                                setattr(r, k, value[k])
                            except (AttributeError, ValueError) as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, str(value), proto_name, e)
                    else:
                        try:
                            setattr(subrequest_extension, key, value)
                        except (AttributeError, ValueError) as e:
                            try:
                                self.log.debug("%s -> %s", key, value)
                                r = getattr(subrequest_extension, key)
                                r.append(value)
                            except (AttributeError, ValueError) as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, value, proto_name, e)

                subrequest = mainrequest.requests.add()
                subrequest.request_type = entry_id
                subrequest.request_message = subrequest_extension.SerializeToString()

            elif isinstance(entry, int):
                subrequest = mainrequest.requests.add()
                subrequest.request_type = entry
            else:
                raise BadRequestException('Unknown value in request list')

        return mainrequest


    def _parse_main_response(self, response_raw, subrequests):
        self.log.debug('Parsing main RPC response...')

        response_proto = ResponseEnvelope()
        try:
            response_proto.ParseFromString(response_raw)
        except message.DecodeError as e:
            raise MalformedNianticResponseException('Could not parse response.') from e

        self.log.debug('Protobuf structure of rpc response:\n\r%s', response_proto)

        response_proto_dict = protobuf_to_dict(response_proto)
        response_proto_dict = self._parse_sub_responses(subrequests, response_proto_dict)

        if not self.state.message8 and 'platform_returns' in response_proto_dict:
            for plat_response in response_proto_dict['platform_returns']:
                if plat_response['type'] == 8:
                    try:
                        resp = PlatEightResponse()
                        resp.ParseFromString(plat_response['response'])
                        self.state.message8 = resp.message
                    except KeyError:
                        pass
                    break

        if not response_proto_dict:
            raise MalformedNianticResponseException('Could not convert protobuf to dict.')

        return response_proto_dict


    def _parse_sub_responses(self, subrequests_list, response_proto_dict):
        self.log.debug('Parsing sub RPC responses...')
        response_proto_dict['responses'] = {}

        if response_proto_dict['status_code'] == 53:
            exception = ServerApiEndpointRedirectException()
            exception.set_redirected_endpoint(response_proto_dict['api_url'])
            raise exception

        try:
            subresponses = response_proto_dict['returns']
            del response_proto_dict['returns']
        except KeyError:
            return response_proto_dict

        for i, subresponse in enumerate(subresponses):
            request_entry = subrequests_list[i]
            if isinstance(request_entry, int):
                entry_id = request_entry
            else:
                entry_id = tuple(request_entry.items())[0][0]

            entry_name = RequestType.Name(entry_id)
            proto_name = entry_name.lower() + '_response'
            proto_classname = 'pogoprotos.networking.responses.' + proto_name + '_pb2.' + proto_name

            self.log.debug("Parsing class: %s", proto_classname)

            try:
                subresponse_extension = self.get_class(proto_classname)()
            except (AttributeError, TypeError):
                subresponse_extension = None
                error = 'Protobuf definition for {} not found'.format(proto_classname)
                subresponse_return = error
                self.log.warning(error)
            else:
                try:
                    subresponse_extension.ParseFromString(subresponse)
                    subresponse_return = protobuf_to_dict(subresponse_extension)
                except (AttributeError, ValueError, TypeError):
                    error = "Protobuf definition for {} seems not to match".format(proto_classname)
                    subresponse_return = error
                    self.log.warning(error)

            response_proto_dict['responses'][entry_name] = subresponse_return

        return response_proto_dict


class RpcState:
    def __init__(self):
        self.start_time = None
        self.id_gen = IdGenerator()
        self.session_hash = urandom(16)
        self.mag_x_min = rand.uniform(-80, 60)
        self.mag_x_max = self.mag_x_min + 20
        self.mag_y_min = rand.uniform(-120, 90)
        self.mag_y_max = self.mag_y_min + 30
        self.mag_z_min = rand.uniform(-70, 40)
        self.mag_z_max = self.mag_y_min + 15
        self._course = rand.uniform(0, 359.99)
        self.message8 = None

    @property
    def request_id(self):
        return self.id_gen.request_id()

    @property
    def magnetic_field_x(self):
        return rand.uniform(self.mag_x_min, self.mag_x_max)

    @property
    def magnetic_field_y(self):
        return rand.uniform(self.mag_y_min, self.mag_y_max)

    @property
    def magnetic_field_z(self):
        return rand.uniform(self.mag_z_min, self.mag_z_max)

    @property
    def course(self):
        self._course = rand.triangular(0, 359.99, self._course)
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
