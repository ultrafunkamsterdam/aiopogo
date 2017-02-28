import random

from importlib import import_module
from asyncio import ensure_future, TimeoutError
from array import array
from os import urandom
from logging import getLogger
from struct import Struct

from google.protobuf import message
from protobuf_to_dict import protobuf_to_dict
from aiohttp import ClientError, DisconnectedError, HttpProcessingError
from pycrypt import pycrypt

from .exceptions import *
from .utilities import to_camel_case, get_time_ms, get_lib_path, Rand
from .hash_library import HashLibrary
from .hash_server import HashServer
from .session import SessionManager

from .protos.pogoprotos import networking
from networking.envelopes.request_envelope_pb2 import RequestEnvelope
from networking.envelopes.response_envelope_pb2 import ResponseEnvelope
from networking.requests.request_type_pb2 import RequestType
from networking.envelopes.signal_log_pb2 import SignalLog
from networking.platform.requests.send_encrypted_signature_request_pb2 import SendEncryptedSignatureRequest
from networking.platform.requests.plat_eight_request_pb2 import PlatEightRequest

RPC_SESSIONS = SessionManager()


class RpcApi:
    hash_lib_path = None
    log = getLogger(__name__)

    def __init__(self, auth_provider, device_info, state, proxy=None):
        self._auth_provider = auth_provider
        self.state = state

        self._hash_engine = None
        self._api_version = 0.45
        self._encrypt_version = 2
        self.request_proto = None
        if proxy and proxy.startswith('socks'):
            self._session = RPC_SESSIONS.get(proxy)
            self.proxy = None
        else:
            self._session = RPC_SESSIONS.get()
            self.proxy = proxy

        # data fields for SignalAgglom
        self.token2 = random.randint(1, 59)

        self.device_info = device_info

    def activate_hash_library(self):
        if not self.hash_lib_path:
            RpcApi.hash_lib_path = get_lib_path()
        self._hash_server = False
        self._hash_engine = HashLibrary(self.hash_lib_path)

    def activate_hash_server(self, auth_token):
        self._hash_server = True
        self._hash_engine = HashServer(auth_token)

    def set_api_version(self, api_version):
        self._api_version = api_version
        if api_version > 0.45:
            self._encrypt_version = 3

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
        except ProxyException as e:
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
        except Exception:
            return 'unknown'

    async def request(self, endpoint, subrequests, player_position):

        if not self._auth_provider or self._auth_provider.is_login() is False:
            raise NotLoggedInException

        self.request_proto = self.request_proto or await self._build_main_request(subrequests, player_position)

        response = await self._make_rpc(endpoint, self.request_proto)
        response_dict = self._parse_main_response(response, subrequests)

        self.check_authentication(response_dict)

        # some response validations
        try:
            status_code = response_dict['status_code']
            if status_code == 102:
                raise AuthTokenExpiredException
            elif status_code == 52:
                req_type = self.get_request_name(subrequests)
                raise NianticThrottlingException("Request throttled on {} request.".format(req_type))
            elif status_code == 53:
                api_url = response_dict['api_url']
                exception = ServerApiEndpointRedirectException()
                exception.set_redirected_endpoint(api_url)
                raise exception
        except TypeError:
            req_type = self.get_request_name(subrequests)
            raise UnexpectedResponseException('Could not parse status_code from {} response.'.format(req_type))

        return response_dict

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

        request.request_id = self.state.rand.request_id()
        request.accuracy = random.choice((5, 5, 5, 5, 5, 5, 5, 5, 5, 10, 10, 10, 30, 30, 50, 65, random.uniform(66, 80)))

        if player_position:
            request.latitude, request.longitude, altitude = player_position

        # generate sub requests before Signature generation
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
            request.auth_info.token.unknown2 = self.token2
            ticket_serialized = request.auth_info.SerializeToString()  # Sig uses this when no auth_ticket available

        sig = SignalLog()

        sig.field22 = self.state.session_hash
        sig.epoch_timestamp_ms = get_time_ms()
        sig.timestamp_ms_since_start = sig.epoch_timestamp_ms - self.state.start_time
        if sig.timestamp_ms_since_start < 5000:
            sig.timestamp_ms_since_start = random.randint(5000, 8000)

        if self._hash_server:
            hashing = ensure_future(self._hash_engine.hash(sig.epoch_timestamp_ms, request.latitude, request.longitude, request.accuracy, ticket_serialized, sig.field22, request.requests))

        loc = sig.location_updates.add()
        sen = sig.sensor_updates.add()

        sen.timestamp = sig.timestamp_ms_since_start - random.randint(100, 5000)
        loc.timestamp_ms = sig.timestamp_ms_since_start - random.randint(1000, 30000)

        loc.name = 'fused'
        loc.latitude = request.latitude
        loc.longitude = request.longitude

        loc.altitude = altitude or random.triangular(300, 400, 350)

        if random.random() > .95:
            # no reading for roughly 1 in 20 updates
            loc.device_course = -1
            loc.device_speed = -1
        else:
            loc.device_course = self.state.get_course()
            loc.device_speed = random.triangular(0.2, 4.25, 1)

        loc.provider_status = 3
        loc.location_type = 1
        if request.accuracy >= 65:
            loc.vertical_accuracy = random.triangular(35, 100, 65)
            loc.horizontal_accuracy = random.choice((request.accuracy, 65, 65, random.uniform(66,80), 200))
        else:
            if request.accuracy > 10:
                loc.vertical_accuracy = random.choice((24, 32, 48, 48, 64, 64, 96, 128))
            else:
                loc.vertical_accuracy = random.choice((3, 4, 6, 6, 6, 6, 8, 12, 24))
            loc.horizontal_accuracy = request.accuracy

        sen.acceleration_x = random.triangular(-1.7, 1.2, 0)
        sen.acceleration_y = random.triangular(-1.4, 1.9, 0)
        sen.acceleration_z = random.triangular(-1.4, .9, 0)
        sen.magnetic_field_x = random.triangular(-54, 50, 0)
        sen.magnetic_field_y = random.triangular(-51, 57, -4.8)
        sen.magnetic_field_z = random.triangular(-56, 43, -30)
        sen.magnetic_field_accuracy = random.choice((-1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2))
        sen.attitude_pitch = random.triangular(-1.5, 1.5, 0.4)
        sen.attitude_yaw = random.triangular(-3.1, 3.1, .198)
        sen.attitude_roll = random.triangular(-2.8, 3.04, 0)
        sen.rotation_rate_x = random.triangular(-4.7, 3.9, 0)
        sen.rotation_rate_y = random.triangular(-4.7, 4.3, 0)
        sen.rotation_rate_z = random.triangular(-4.7, 6.5, 0)
        sen.gravity_x = random.triangular(-1, 1, 0)
        sen.gravity_y = random.triangular(-1, 1, -.2)
        sen.gravity_z = random.triangular(-1, .7, -0.7)
        sen.status = 3

        sig.version_hash = -816976800928766045

        if self.device_info:
            for key in self.device_info:
                setattr(sig.device_info, key, self.device_info[key])
            if self.device_info.get('brand', 'Apple') == 'Apple':
                sig.ios_device_info.bool5 = True

        try:
            if request.requests[0].request_type in (RequestType.Value('GET_MAP_OBJECTS'), RequestType.Value('GET_PLAYER')):
                plat_eight = PlatEightRequest()
                plat_eight.field1 = '90f6a704505bccac73cec99b07794993e6fd5a12'
                plat8 = request.platform_requests.add()
                plat8.type = 8
                plat8.request_message = plat_eight.SerializeToString()
        except (IndexError, AttributeError):
            pass

        if self._hash_server:
            await hashing
        else:
            self._hash_engine.hash(sig.epoch_timestamp_ms, request.latitude, request.longitude, request.accuracy, ticket_serialized, sig.field22, request.requests)

        sig.location_hash_by_token_seed = self._hash_engine.location_auth_hash
        sig.location_hash = self._hash_engine.location_hash
        for req_hash in self._hash_engine.request_hashes:
            sig.request_hashes.append(req_hash)

        signature_proto = sig.SerializeToString()
        sig_request = SendEncryptedSignatureRequest()
        sig_request.encrypted_signature = pycrypt(signature_proto, sig.timestamp_ms_since_start, self._encrypt_version)
        plat = request.platform_requests.add()
        plat.type = 6
        plat.request_message = sig_request.SerializeToString()

        request.ms_since_last_locationfix = int(random.triangular(300, 30000, 10000))

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
                            except Exception as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, i, proto_name, e)
                    elif isinstance(value, dict):
                        for k in value.keys():
                            try:
                                r = getattr(subrequest_extension, key)
                                setattr(r, k, value[k])
                            except Exception as e:
                                self.log.warning('Argument %s with value %s unknown inside %s (Exception: %s)', key, str(value), proto_name, e)
                    else:
                        try:
                            setattr(subrequest_extension, key, value)
                        except Exception as e:
                            try:
                                self.log.debug("%s -> %s", key, value)
                                r = getattr(subrequest_extension, key)
                                r.append(value)
                            except Exception as e:
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

        if not response_proto_dict:
            raise MalformedNianticResponseException('Could not convert protobuf to dict.')

        return response_proto_dict


    def _parse_sub_responses(self, subrequests_list, response_proto_dict):
        self.log.debug('Parsing sub RPC responses...')
        response_proto_dict['responses'] = {}

        if response_proto_dict.get('status_code', 1) == 53:
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
            except Exception:
                subresponse_extension = None
                error = 'Protobuf definition for {} not found'.format(proto_classname)
                subresponse_return = error
                self.log.warning(error)
            else:
                try:
                    subresponse_extension.ParseFromString(subresponse)
                    subresponse_return = protobuf_to_dict(subresponse_extension)
                except Exception:
                    error = "Protobuf definition for {} seems not to match".format(proto_classname)
                    subresponse_return = error
                    self.log.warning(error)

            response_proto_dict['responses'][entry_name] = subresponse_return

        return response_proto_dict


class RpcState:
    def __init__(self):
        self.start_time = get_time_ms()
        self.rand = Rand()
        self.session_hash = urandom(16)
        self.course = random.uniform(0, 359.99)

    def get_course(self):
        self.course = random.triangular(0, 359.99, self.course)
        return self.course
