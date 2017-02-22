import asyncio

from aiohttp import helpers
from aiohttp.connector import Connection, TCPConnector
from aiohttp.errors import ClientOSError, ClientTimeoutError


class TimedConnection(Connection):
    def __init__(self, *args, time=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._time = time or self._loop.time()

    def release(self):
        if self._transport is not None:
            self._connector._release(
                self._key, self._request, self._transport, self._protocol,
                time=self._time, should_close=False)
            self._transport = None


class TimedConnector(TCPConnector):
    _conn_duration = 7.5

    async def connect(self, req):
        """Get from pool or create new connection."""
        key = (req.host, req.port, req.ssl)

        limit = self._limit
        if limit is not None:
            fut = helpers.create_future(self._loop)
            waiters = self._waiters[key]

            # The limit defines the maximum number of concurrent connections
            # for a key. Waiters must be counted against the limit, even before
            # the underlying connection is created.
            available = limit - len(waiters) - len(self._acquired[key])

            # Don't wait if there are connections available.
            if available > 0:
                fut.set_result(None)

            # This connection will now count towards the limit.
            waiters.append(fut)

        try:
            if limit is not None:
                await fut

            transport, proto, time = self._get(key)
            if transport is None:
                try:
                    if self._conn_timeout:
                        transport, proto = await asyncio.wait_for(
                            self._create_connection(req),
                            self._conn_timeout, loop=self._loop)
                    else:
                        transport, proto = \
                            await self._create_connection(req)

                except asyncio.TimeoutError as exc:
                    raise ClientTimeoutError(
                        'Connection timeout to host {0[0]}:{0[1]} ssl:{0[2]}'
                        .format(key)) from exc
                except OSError as exc:
                    raise ClientOSError(
                        exc.errno,
                        'Cannot connect to host {0[0]}:{0[1]} ssl:{0[2]} [{1}]'
                        .format(key, exc.strerror)) from exc
        except:
            self._release_waiter(key)
            raise

        self._acquired[key].add(transport)
        conn = TimedConnection(self, key, req, transport, proto, self._loop, time=time)
        return conn

    def _get(self, key):
        try:
            conns = self._conns[key]
        except KeyError:
            return None, None, None

        t1 = self._loop.time()
        while conns:
            transport, proto, t0 = conns.pop()
            if transport is not None and proto.is_connected():
                if t1 - t0 > self._conn_duration:
                    transport.close()
                    if key[-1] and not self._cleanup_closed_disabled:
                        self._cleanup_closed_transports.append(transport)
                else:
                    if not conns:
                        # The very last connection was reclaimed: drop the key
                        del self._conns[key]
                    return transport, proto, t0

        # No more connections: drop the key
        del self._conns[key]
        return None, None, None

    def _release(self, key, req, transport, protocol, *, time=None, should_close=False):
        if self._closed:
            # acquired connection is already released on connector closing
            return

        acquired = self._release_acquired(key, transport)

        if self._limit is not None and acquired is not None:
            if len(acquired) < self._limit:
                self._release_waiter(key)

        resp = req.response

        if not should_close and resp is not None:
            should_close = resp._should_close

        reader = protocol.reader
        if should_close or (reader.output and not reader.output.at_eof()):
            transport.close()

            if key[-1] and not self._cleanup_closed_disabled:
                self._cleanup_closed_transports.append(transport)
        else:
            conns = self._conns.get(key)
            if conns is None:
                conns = self._conns[key] = []
            conns.append((transport, protocol, time))
            reader.unset_parser()
