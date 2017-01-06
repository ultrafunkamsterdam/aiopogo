from aiohttp import TCPConnector, ClientSession
from asyncio import get_event_loop

class Session:
    @classmethod
    def get(cls):
        try:
            return cls._session
        except AttributeError:
            loop = get_event_loop()
            connector = TCPConnector(limit=150, loop=loop)
            cls._session = ClientSession(connector=connector,
                                         loop=loop,
                                         headers={'User-Agent': 'Niantic App'})
            return cls._session

    @classmethod
    def close(cls):
        try:
            cls._session.close()
        except AttributeError:
            pass
