from aiohttp import ProxyConnectionError
from asyncio import TimeoutError


class AiopogoError(Exception):
    """Any custom exception in this module"""

class HashServerException(AiopogoError):
    """Parent class of all hashing server errors"""

class ProxyException(ProxyConnectionError):
    """Raised when there is an error connecting to a proxy server."""

class TimeoutException(AiopogoError, TimeoutError):
    """Raised when a request times out."""


class AuthException(AiopogoError):
    """Raised when logging in fails"""

class ActivationRequiredException(AuthException):
    """Raised when an account needs to verify its email."""

class AuthTimeoutException(AuthException, TimeoutException):
    """Raised when an auth request times out."""

class InvalidCredentialsException(AuthException, ValueError):
    """Raised when the username, password, or provider are empty/invalid"""


class AuthTokenExpiredException(AiopogoError):
    """Raised when your auth token has expired (code 102)"""


class BadRequestException(AiopogoError):
    """Raised when HTTP code 400 is returned"""

class BadHashRequestException(BadRequestException, HashServerException):
    """Raised when hashing server returns code 400"""

class InvalidRPCException(BadRequestException):
    """Raised when an invalid RPC is made."""

class BadRPCException(InvalidRPCException):
    """Raised when an invalid RPC is made."""


class BannedAccountException(AiopogoError):
    """Raised when an account is banned"""


class ExpiredHashKeyException(HashServerException):
    """Raised when a hash key has expired."""


class MalformedResponseException(AiopogoError):
    """Raised when the response is empty or not in an expected format"""

class MalformedNianticResponseException(AiopogoError):
    """Raised when a Niantic response is empty or not in an expected format"""

class MalformedHashResponseException(MalformedResponseException, HashServerException):
    """Raised when the response from the hash server cannot be parsed."""


class NoPlayerPositionSetException(AiopogoError, ValueError):
    """Raised when either lat or lng is None"""


class NotLoggedInException(AiopogoError):
    """Raised when attempting to make a request while not authenticated"""


class ServerBusyOrOfflineException(AiopogoError):
    """Raised when unable to establish a connection with a server"""

class AuthConnectionException(AuthException, ServerBusyOrOfflineException):
    """Raised when there's a connection error during auth."""

class NianticOfflineException(ServerBusyOrOfflineException):
    """Raised when unable to establish a conection with Niantic"""

class NianticTimeoutException(NianticOfflineException, TimeoutException):
    """Raised when an RPC request times out."""

class HashingOfflineException(ServerBusyOrOfflineException, HashServerException):
    """Raised when unable to establish a conection with the hashing server"""

class HashingTimeoutException(HashingOfflineException, TimeoutException):
    """Raised when a request to the hashing server times out."""


class PleaseInstallProtobufVersion3(AiopogoError):
    """Raised when Protobuf is unavailable or too old"""


class ServerSideAccessForbiddenException(AiopogoError):
    """Raised when access to a server is forbidden"""

class NianticIPBannedException(ServerSideAccessForbiddenException):
    """Raised when Niantic returns a 403, meaning your IP is probably banned"""

class HashingForbiddenException(ServerSideAccessForbiddenException, HashServerException):
    """Raised when the hashing server returns 403"""

class TempHashingBanException(HashingForbiddenException):
    """Raised when your IP is temporarily banned for sending too many requests with invalid keys."""


class ServerSideRequestThrottlingException(AiopogoError):
    """Raised when too many requests were made in a short period"""

class HashingQuotaExceededException(ServerSideRequestThrottlingException, HashServerException):
    """Raised when you exceed your hashing server quota"""


class UnexpectedResponseException(AiopogoError):
    """Raised when an unhandled HTTP status code is received"""

class UnexpectedHashResponseException(UnexpectedResponseException, HashServerException):
    """Raised when an unhandled HTTP code is received from the hash server"""


class ServerApiEndpointRedirectException(AiopogoError):
    """Raised when the API redirects you to another endpoint"""
    def __init__(self):
        self._api_endpoint = None

    def get_redirected_endpoint(self):
        return self._api_endpoint

    def set_redirected_endpoint(self, api_endpoint):
        self._api_endpoint = api_endpoint
