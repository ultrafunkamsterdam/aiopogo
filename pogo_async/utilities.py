"""
pgoapi - Pokemon Go API
Copyright (c) 2016 tjado <https://github.com/tejado>
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.
Author: tjado <https://github.com/tejado>
"""

import time
import struct
import logging
import os
import sys
import platform

from json import JSONEncoder
from binascii import unhexlify
from math import pi
from array import array

EARTH_RADIUS = 6371009  # radius of Earth in meters
try:
    from s2 import (
        S1Angle as Angle,
        S2Cap as Cap,
        S2LatLng as LatLng,
        S2RegionCoverer as RegionCoverer
    )
    HAVE_S2 = True
    DEFAULT_ANGLE = Angle.Degrees(360 * 500 / (2 * pi * EARTH_RADIUS))
except ImportError:
    from s2sphere import Angle, Cap, LatLng, RegionCoverer
    HAVE_S2 = False
    DEFAULT_ANGLE = Angle.from_degrees(360 * 500 / (2 * pi * EARTH_RADIUS))

log = logging.getLogger(__name__)


def f2i(float):
  return struct.unpack('<Q', struct.pack('<d', float))[0]


def f2h(float):
  return hex(struct.unpack('<Q', struct.pack('<d', float))[0])


def h2f(hex):
  return struct.unpack('<d', struct.pack('<Q', int(hex,16)))[0]


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


def _cells_py(lat, lon, angle):
    region = Cap.from_axis_angle(LatLng.from_degrees(lat, lon).to_point(), angle)
    coverer = RegionCoverer()
    coverer.min_level = 15
    coverer.max_level = 15
    return coverer.get_covering(region)


def _cells_cpp(lat, lon, angle):
    region = Cap.FromAxisAngle(LatLng.FromDegrees(lat, lon).ToPoint(), angle)
    coverer = RegionCoverer()
    coverer.set_min_level(15)
    coverer.set_max_level(15)
    return coverer.GetCovering(region)


if HAVE_S2:
    _cells = _cells_cpp
else:
    _cells = _cells_py


def cell_ids(lat, lon, angle=DEFAULT_ANGLE, compact=False):
    cells = _cells(lat, lon, angle)
    if compact:
        return array('Q', (x.id() for x in cells))
    return tuple(x.id() for x in cells)


def get_cell_ids(lat, lon, radius=None, compact=False):
    # Max values allowed by server according to this comment:
    # https://github.com/AeonLucid/POGOProtos/issues/83#issuecomment-235612285
    if not radius:
        angle = DEFAULT_ANGLE
    else:
        if radius > 1500:
            radius = 1500  # radius = 1500 is max allowed by the server
        angle = Angle.from_degrees(360 * radius / (2 * pi * EARTH_RADIUS))
    cells = _cells(lat, lon, angle)

    if radius > 1250:
        del cells[100:]  # 100 is max allowed by the server
    if compact:
        return array('Q', (x.id() for x in cells))
    return tuple(x.id() for x in cells)


def get_time(ms = False):
    if ms:
        return int(time.time() * 1000)
    else:
        return int(time.time())


def get_format_time_diff(low, high, ms = True):
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
    Q = 127773      # M // A (To avoid overflow on A * seed)
    R = 2836        # M % A (To avoid overflow on A * seed)

    def __init__(self, seed=1):
        self.seed = seed
        self.request = 1

    def next(self):
        hi = self.seed // self.Q
        lo = self.seed % self.Q
        t = self.A * lo - self.R * hi
        if t < 0:
            t += self.M
        self.seed = t % 0x80000000
        return self.seed


def long_to_bytes(val, endianness='big'):
    """
    Use :ref:`string formatting` and :func:`~binascii.unhexlify` to
    convert ``val``, a :func:`long`, to a byte :func:`str`.
    :param long val: The value to pack
    :param str endianness: The endianness of the result. ``'big'`` for
      big-endian, ``'little'`` for little-endian.
    If you want byte- and word-ordering to differ, you're on your own.
    Using :ref:`string formatting` lets us use Python's C innards.
    """

    # one (1) hex digit per four (4) bits
    width = val.bit_length()

    # unhexlify wants an even multiple of eight (8) bits, but we don't
    # want more digits than we need (hence the ternary-ish 'or')
    width += 8 - ((width % 8) or 8)

    # format width specifier: four (4) bits per hex digit
    fmt = '%%0%dx' % (width // 4)

    # prepend zero (0) to the width, to zero-pad the output
    s = unhexlify(fmt % val)

    if endianness == 'little':
        # see http://stackoverflow.com/a/931095/309233
        s = s[::-1]

    return s


def get_lib_paths():
    # win32 doesn't necessarily mean 32 bits
    arch = platform.architecture()[0]
    plat = sys.platform
    if plat in ('win32', 'cygwin'):
        if arch == '64bit':
            encrypt_lib = "libpcrypt-windows-x86-64.dll"
            hash_lib = "libniahash-windows-x86-64.dll"
        else:
            encrypt_lib = "libpcrypt-windows-i686.dll"
            hash_lib = "libniahash-windows-i686.dll"
    elif plat == "darwin":
        if arch == '64bit':
            encrypt_lib = "libpcrypt-macos-x86-64.dylib"
            hash_lib = "libniahash-macos-x86-64.dylib"
        else:
            encrypt_lib = "libpcrypt-macos-i386.dylib"
            hash_lib = "libniahash-macos-i386.dylib"
    elif os.uname()[4].startswith("arm") and arch == '32bit':
        encrypt_lib = "libpcrypt-linux-arm32.so"
        hash_lib = "libniahash-linux-arm32.so"
    elif os.uname()[4].startswith("aarch64"):
        encrypt_lib = "libpcrypt-linux-arm64.so"
        hash_lib = "libniahash-linux-arm64.so"
    elif plat.startswith('linux'):
        if arch == '64bit':
            encrypt_lib = "libpcrypt-linux-x86-64.so"
            hash_lib = "libniahash-linux-x86-64.so"
        else:
            encrypt_lib = "libpcrypt-linux-i386.so"
            hash_lib = "libniahash-linux-i386.so"
    elif plat.startswith('freebsd'):
        if arch == '64bit':
            encrypt_lib = "libpcrypt-freebsd-x86-64.so"
            hash_lib = "libniahash-freebsd-x86-64.so"
        else:
            encrypt_lib = "libpcrypt-freebsd-i386.so"
            hash_lib = "libniahash-freebsd-i386.so"
    else:
        err = "Unexpected/unsupported platform: {}".format(plat)
        log.error(err)
        raise NotImplementedError(err)

    encrypt_lib_path = os.path.join(os.path.dirname(__file__), "lib", encrypt_lib)
    hash_lib_path = os.path.join(os.path.dirname(__file__), "lib", hash_lib)

    if not os.path.isfile(encrypt_lib_path):
        err = "Could not find {} encryption library {}".format(plat, encrypt_lib_path)
        log.error(err)
        raise OSError(err)

    if not os.path.isfile(hash_lib_path):
        err = "Could not find {} hashing library {}".format(plat, hash_lib_path)
        log.error(err)
        raise OSError(err)

    return encrypt_lib_path, hash_lib_path
