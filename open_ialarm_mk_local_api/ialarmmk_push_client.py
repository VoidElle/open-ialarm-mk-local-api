import asyncio
import logging
from typing import Callable

from ._internal.meian_push_client import MeianPushProtocol

logger = logging.getLogger(__name__)

# Seconds to wait before reconnecting after a dropped push connection.
_RECONNECT_DELAY = 5


class IAlarmMkPushClient:
    """High-level push-event subscriber for iAlarm-MK panels.

    Opens a **dedicated** TCP connection to the panel's push port and
    invokes *on_event* for every alarm event received.  The connection
    is re-established automatically if it drops.

    .. note::
        If you are already holding a persistent command connection via
        :class:`IAlarmMkClient`, the panel will push the same alarm
        events on that connection too (see ``IAlarmMkClient.on_event``).
        In that case this class is redundant and adds an unnecessary
        second TCP connection.  Use ``IAlarmMkPushClient`` only when you
        need a push-only (read-only) connection with no command socket.

    Usage::

        def on_event(event: dict):
            print(event)

        client = IAlarmMkPushClient("192.168.1.100", 18034, "user", on_event)
        await client.subscribe()   # blocks until cancel() is called
    """

    def __init__(self, host: str, port: int, username: str, on_event: Callable[[dict], None]):
        self._host = host
        self._port = port
        self._username = username
        self._on_event = on_event
        self._cancelled = False
        self._transport = None
        logger.debug("IAlarmMkPushClient initialised for %s:%d (user=%s)", host, port, username)

    async def subscribe(self) -> None:
        """Connect to the panel push port and forward events to *on_event*.

        Reconnects automatically after any disconnection or error.
        Call :meth:`cancel` to stop the loop.
        """
        logger.debug("subscribe: starting push subscription loop for %s:%d", self._host, self._port)
        while not self._cancelled:
            try:
                loop = asyncio.get_running_loop()
                on_con_lost = loop.create_future()
                logger.debug("subscribe: connecting to %s:%d", self._host, self._port)
                transport, _protocol = await loop.create_connection(
                    lambda: MeianPushProtocol(self._username, self._on_event, on_con_lost),
                    self._host,
                    self._port,
                )
                self._transport = transport
                logger.debug("subscribe: connected, waiting for events")
                await on_con_lost
                logger.debug("subscribe: connection closed")
            except Exception as exc:
                logger.warning("subscribe: connection lost (%s). Reconnecting in %ds...", exc, _RECONNECT_DELAY)
                if self._cancelled:
                    break
                await asyncio.sleep(_RECONNECT_DELAY)
        logger.debug("subscribe: push subscription loop ended")

    def cancel(self) -> None:
        """Stop the subscription loop and close the current connection."""
        logger.debug("cancel: cancelling push subscription")
        self._cancelled = True
        if self._transport and not self._transport.is_closing():
            self._transport.close()
        logger.debug("cancel: done")

