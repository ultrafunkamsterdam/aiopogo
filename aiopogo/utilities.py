from time import time
from json import JSONEncoder
from struct import pack, unpack


def f2i(float_val):
    return unpack('<q', pack('<d', float_val))[0]


def to_camel_case(string):
    return ''.join(word.capitalize() for word in string.split('_'))


# JSON Encoder to handle bytes
class JSONByteEncoder(JSONEncoder):
    def default(self, o):
        return o.decode('ascii')


def get_time_ms():
    return int(time() * 1000)


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
