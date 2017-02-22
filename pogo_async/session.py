from aiohttp import TCPConnector, ClientSession
from asyncio import get_event_loop
from yarl import URL

try:
    from aiosocks import Socks4Addr, Socks5Addr, Socks5Auth
    from aiosocks.connector import SocksConnector
except ImportError:
    class SocksConnector:
        def __init__(s, *args, **kwargs):
            raise ImportError('Install aiosocks to use socks proxies.')
    Socks4Addr = Socks5Addr = Socks5Auth = SocksConnector

CONN_TIMEOUT = 10


def socks_connector(proxy, loop=None):
    loop = loop or get_event_loop()
    proxy = URL(proxy)
    auth = None
    if proxy.scheme == 'socks4':
        addr = Socks4Addr(proxy.host, proxy.port)
    else:
        addr = Socks5Addr(proxy.host, proxy.port)
        if proxy.user and proxy.password:
            auth = Socks5Auth(proxy.user, proxy.password)
    return SocksConnector(proxy=addr,
                          proxy_auth=auth,
                          limit=300,
                          loop=loop,
                          remote_resolve=False,
                          verify_ssl=False)


class SessionManager:
    def __init__(self, loop=None):
        self.loop = loop or get_event_loop()
        self.sessions = {}

    def get(self, proxy=None, headers={'User-Agent': 'Niantic App'}):
        try:
            return self.sessions[proxy]
        except KeyError:
            if proxy:
                conn = socks_connector(proxy, self.loop)
            else:
                conn = TCPConnector(limit=300,
                                    loop=self.loop,
                                    verify_ssl=False,
                                    conn_timeout=CONN_TIMEOUT)

            self.sessions[proxy] = ClientSession(connector=conn,
                                                 loop=self.loop,
                                                 headers=headers)
            return self.sessions[proxy]

    def close(self):
        for session in self.sessions.values():
            session.close()
