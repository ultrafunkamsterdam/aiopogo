from time import time
from json import JSONEncoder
from array import array
from logging import getLogger
from random import Random
from bisect import bisect
from struct import pack, unpack

from pogeo import get_cell_ids

log = getLogger(__name__)


def f2i(f):
    return unpack('<q', pack('<d', f))[0]


def to_camel_case(value):
    return ''.join(word.capitalize() if word else '_' for word in value.split('_'))


# JSON Encoder to handle bytes
class JSONByteEncoder(JSONEncoder):
    def default(self, o):
        return o.decode('ascii')


def get_cell_ids_compact(lat, lon, radius=500):
    return array('Q', get_cell_ids(lat, lon, radius))


def get_time_ms():
    return int(time() * 1000)


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


class IdGenerator:
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


class CustomRandom(Random):
    def choose_weighted(self, population, cum_weights):
        """Return an item from population according to provided weights.
        """
        if len(cum_weights) != len(population):
            raise ValueError('The number of weights does not match the population')
        total = cum_weights[-1]
        return population[bisect(cum_weights, self.random() * total)]

    def triangular_int(self, low, high, mode):
        """Triangular distribution.

        Continuous distribution bounded by given lower and upper limits,
        and having a given mode value in-between.

        http://en.wikipedia.org/wiki/Triangular_distribution
        """
        u = self.random()
        try:
            c = (mode - low) / (high - low)
        except ZeroDivisionError:
            return low
        if u > c:
            u = 1 - u
            c = 1 - c
            low, high = high, low
        return int(low + (high - low) * (u * c) ** 0.5)
