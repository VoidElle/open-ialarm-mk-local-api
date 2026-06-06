import asyncio
import logging
from typing import Callable

from ._internal.meian_push_client import MeianPushProtocol

logger = logging.getLogger(__name__)


class IAlarmMkPushClient:
    def __init__(self, host: str, port: int, username: str, on_event: Callable[[dict], None]):
        self._host = host
        self._port = port
        self._username = username
        self._on_event = on_event
        self._cancelled = False
        self._transport = None

    async def subscribe(self) -> None:
        """Connect to the panel push port and forward events to *on_event*.

        Reconnects automatically after any disconnection or error.
        Call :meth:`cancel` to stop the loop.
        """
        while not self._cancelled:
            try:
                loop = asyncio.get_running_loop()
                on_con_lost = loop.create_future()
                transport, _protocol = await loop.create_connection(
                    lambda: MeianPushProtocol(self._username, self._on_event, on_con_lost),
                    self._host,
                    self._port,
                )
                self._transport = transport
                await on_con_lost
            except Exception as exc:
                logger.warning("Push connection lost: %s. Reconnecting...", exc)
                if self._cancelled:
                    break
                await asyncio.sleep(5)

    def cancel(self) -> None:
        self._cancelled = True
        if self._transport and not self._transport.is_closing():
            self._transport.close()
