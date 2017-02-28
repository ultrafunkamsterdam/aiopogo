import time
import struct
import os
import sys
import platform

from json import JSONEncoder
from binascii import unhexlify
from math import pi
from array import array
from logging import getLogger

EARTH_RADIUS = 6371009  # radius of Earth in meters
try:
    import pogeo
    HAVE_POGEO = True
except ImportError:
    HAVE_POGEO = False
    from s2sphere import Angle, Cap, LatLng, RegionCoverer
    DEFAULT_ANGLE = Angle.from_degrees(360 * 500 / (2 * pi * EARTH_RADIUS))

log = getLogger(__name__)


def f2i(float):
    return struct.unpack('<Q', struct.pack('<d', float))[0]


def f2h(float):
    return hex(struct.unpack('<Q', struct.pack('<d', float))[0])


def d2h(f):
    hex_str = f2h(f)[2:].replace('L','')
    hex_str = ("0" * (len(hex_str) % 2)) + hex_str
    return unhexlify(hex_str)


def to_camel_case(value):
    return ''.join(word.capitalize() if word else '_' for word in value.split('_'))


# JSON Encoder to handle bytes
class JSONByteEncoder(JSONEncoder):
    def default(self, o):
        return o.decode('ascii')


if HAVE_POGEO:
    def get_cell_ids(lat, lon, radius=500, compact=False):
        if compact:
            return array('Q', pogeo.get_cell_ids(lat, lon, radius))
        else:
            return pogeo.get_cell_ids(lat, lon, radius)
else:
    def get_cell_ids(lat, lon, radius=None, compact=False):
        if radius:
            angle = Angle.from_degrees(360 * radius / (2 * pi * EARTH_RADIUS))
        else:
            angle = DEFAULT_ANGLE
        region = Cap.from_axis_angle(LatLng.from_degrees(lat, lon).to_point(), angle)
        coverer = RegionCoverer()
        coverer.min_level = 15
        coverer.max_level = 15
        covering = coverer.get_covering(region)
        if compact:
            return array('Q', (x.id() for x in covering))
        return tuple(x.id() for x in covering)


def get_time():
    return int(time.time())


def get_time_ms():
    return int(time.time() * 1000)


def get_format_time_diff(low, high, ms=True):
    diff = (high - low)
    if ms:
        m, s = divmod(diff / 1000, 60)
    else:
        m, s = divmod(diff, 60)
    h, m = divmod(m, 60)

    return (h, m, s)


def parse_api_endpoint(api_url):
    if not api_url.startswith("https"):
        api_url = 'https://{}/rpc'.format(api_url)
    return api_url


class Rand:
    '''Lehmer random number generator'''
    M = 0x7fffffff  # 2^31 - 1 (A large prime number)
    A = 16807       # Prime root of M

    def __init__(self, seed=16807):
        self.seed = seed
        self.request = 1

    def next(self):
        self.seed = (self.seed * self.A) % self.M
        return self.seed

    def request_id(self):
        self.request += 1
        return (self.next() << 32) | self.request


def get_lib_path():
    # win32 doesn't necessarily mean 32 bits
    arch = platform.architecture()[0]
    plat = sys.platform
    if plat in ('win32', 'cygwin'):
        if arch == '64bit':
            hash_lib = "libniahash-windows-x86-64.dll"
        else:
            hash_lib = "libniahash-windows-i686.dll"
    elif plat == "darwin":
        if arch == '64bit':
            hash_lib = "libniahash-macos-x86-64.dylib"
        else:
            hash_lib = "libniahash-macos-i386.dylib"
    elif os.uname()[4].startswith("arm") and arch == '32bit':
        hash_lib = "libniahash-linux-arm32.so"
    elif os.uname()[4].startswith("aarch64"):
        hash_lib = "libniahash-linux-arm64.so"
    elif plat.startswith('linux'):
        if arch == '64bit':
            hash_lib = "libniahash-linux-x86-64.so"
        else:
            hash_lib = "libniahash-linux-i386.so"
    elif plat.startswith('freebsd'):
        if arch == '64bit':
            hash_lib = "libniahash-freebsd-x86-64.so"
        else:
            hash_lib = "libniahash-freebsd-i386.so"
    else:
        err = "Unexpected/unsupported platform: {}".format(plat)
        log.error(err)
        raise NotImplementedError(err)

    hash_lib_path = os.path.join(os.path.dirname(__file__), "lib", hash_lib)
    if not os.path.isfile(hash_lib_path):
        err = "Could not find {} hashing library {}".format(plat, hash_lib_path)
        log.error(err)
        raise OSError(err)

    return hash_lib_path
