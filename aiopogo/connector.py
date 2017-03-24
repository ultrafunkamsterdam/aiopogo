from aiohttp.connector import Connection, helpers, TCPConnector, _TransportPlaceholder, ClientConnectorError


class TimedConnection(Connection):
    def __init__(self, *args, time=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._time = time or self._loop.time()

    def release(self):
        self._notify_release()
        self._connector._release(
            self._key, self._protocol, time=self._time, should_close=False)
        self._protocol = None


class TimedConnector(TCPConnector):
    _conn_duration = 7.5

    async def connect(self, req):
        """Get from pool or create new connection."""
        key = (req.host, req.port, req.ssl)

        if self._limit:
            # total calc available connections
            available = self._limit - len(self._waiters) - len(self._acquired)

            # check limit per host
            if (self._limit_per_host and available > 0 and
                    key in self._acquired_per_host):
                available = self._limit_per_host - len(
                    self._acquired_per_host.get(key))

        elif self._limit_per_host and key in self._acquired_per_host:
            # check limit per host
            available = self._limit_per_host - len(
                self._acquired_per_host.get(key))
        else:
            available = 1

        # Wait if there are no available connections.
        if available <= 0:
            fut = helpers.create_future(self._loop)

            # This connection will now count towards the limit.
            waiters = self._waiters[key]
            waiters.append(fut)
            await fut
            waiters.remove(fut)
            if not waiters:
                del self._waiters[key]

        proto, time = self._get(key)
        if proto is None:
            placeholder = _TransportPlaceholder()
            self._acquired.add(placeholder)
            self._acquired_per_host[key].add(placeholder)
            try:
                proto = await self._create_connection(req)
            except OSError as exc:
                raise ClientConnectorError(
                    exc.errno,
                    'Cannot connect to host {0[0]}:{0[1]} ssl:{0[2]} [{1}]'
                    .format(key, exc.strerror)) from exc
            finally:
                self._acquired.remove(placeholder)
                self._acquired_per_host[key].remove(placeholder)

        self._acquired.add(proto)
        self._acquired_per_host[key].add(proto)
        return TimedConnection(self, key, proto, self._loop, time=time)

    def _get(self, key):
        try:
            conns = self._conns[key]
        except KeyError:
            return None, None

        t1 = self._loop.time()
        while conns:
            proto, t0 = conns.pop()
            if proto.is_connected():
                if t1 - t0 > self._conn_duration:
                    transport = proto.close()
                    # only for SSL transports
                    if key[-1] and not self._cleanup_closed_disabled:
                        self._cleanup_closed_transports.append(transport)
                else:
                    if not conns:
                        # The very last connection was reclaimed: drop the key
                        del self._conns[key]
                    return proto, t0

        # No more connections: drop the key
        del self._conns[key]
        return None, None

    def _release(self, key, protocol, *, time=None, should_close=False):
        if self._closed:
            # acquired connection is already released on connector closing
            return

        self._release_acquired(key, protocol)

        if self._force_close:
            should_close = True

        if should_close or protocol.should_close:
            transport = protocol.close()

            if key[-1] and not self._cleanup_closed_disabled:
                self._cleanup_closed_transports.append(transport)
        else:
            conns = self._conns.get(key)
            if conns is None:
                conns = self._conns[key] = []
            conns.append((protocol, time or self._loop.time()))

            if self._cleanup_handle is None:
                self._cleanup_handle = helpers.weakref_handle(
                    self, '_cleanup', self._keepalive_timeout, self._loop)
