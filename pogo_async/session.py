from aiohttp import TCPConnector, ClientSession
from asyncio import get_event_loop
from yarl import URL
try:
    from aiosocks import Socks4Addr, Socks5Addr, Socks5Auth
    from aiosocks.connector import SocksConnector
except ModuleNotFoundError:
    pass

def proxy_connector(proxy, loop=None):
    try:
        proxy = URL(proxy)
        auth = None
        if proxy.scheme == 'socks4':
            addr = Socks4Addr(proxy.host, proxy.port)
        else:
            addr = Socks5Addr(proxy.host, proxy.port)
            if proxy.user and proxy.password:
                auth = Socks5Auth(proxy.user, proxy.password)
        return SocksConnector(proxy=addr, proxy_auth=auth, limit=250, loop=loop, remote_resolve=False)
    except NameError as e:
        raise ModuleNotFoundError('Install aiosocks to use socks proxies.') from e

class Session:
    sessions = {}
    loop = get_event_loop()

    @classmethod
    def get(cls, proxy=None):
        if proxy in cls.sessions:
            return cls.sessions[proxy]
        if proxy:
            conn = proxy_connector(proxy, loop=cls.loop)
        else:
            conn = TCPConnector(limit=250, loop=cls.loop)
        cls.sessions[proxy] = ClientSession(connector=conn,
                                            loop=cls.loop,
                                            headers={'User-Agent': 'Niantic App'})
        return cls.sessions[proxy]

    @classmethod
    def close(cls):
        for session in cls.sessions.values():
            session.close()
