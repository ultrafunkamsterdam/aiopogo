from asyncio import get_event_loop

from aiohttp import ClientSession, ClientRequest, TCPConnector

try:
    from aiosocks.connector import ProxyClientRequest, ProxyConnector
except ImportError:
    class ProxyConnector:
        def __init__(self, *args, **kwargs):
            raise ImportError('Install aiosocks to use socks proxies.')
    ProxyClientRequest = ProxyConnector


class SessionManager:
    __slots__ = (
        'loop',
        'session',
        'connector',
        'socks_session',
        'socks_connector')

    def __init__(self):
        self.loop = get_event_loop()

    def get(self, proxy=None):
        socks = proxy and proxy.scheme in ('socks4', 'socks5')
        try:
            return self.socks_session if socks else self.session
        except AttributeError:
            session = ClientSession(connector=self.get_connector(socks),
                                    loop=self.loop,
                                    headers=(
                                        ('Content-Type', 'application/x-www-form-urlencoded'),
                                        ('User-Agent', 'Niantic App'),
                                        ('Accept-Language', 'en-us')),
                                    request_class=ProxyClientRequest if socks else ClientRequest,
                                    raise_for_status=True,
                                    conn_timeout=10.0)
            if socks:
                self.socks_session = session
            else:
                self.session = session
            return session

    def get_connector(self, socks, limit=400):
        try:
            return self.socks_connector if socks else self.connector
        except AttributeError:
            if socks:
                self.socks_connector = ProxyConnector(limit=limit,
                                                      loop=self.loop,
                                                      verify_ssl=False)
                return self.socks_connector

            self.connector = TCPConnector(limit=limit,
                                          loop=self.loop,
                                          verify_ssl=False)
            return self.connector

    def close(self):
        try:
            self.session.close()
        except AttributeError:
            pass
        try:
            self.socks_session.close()
        except AttributeError:
            pass


SESSIONS = SessionManager()
