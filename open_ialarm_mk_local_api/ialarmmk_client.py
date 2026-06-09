import asyncio
import logging
from typing import Callable

from ._internal.meian_client import MeianClient
from .enums.alarm_status_enum import AlarmStatusEnum
from .enums.zone_status_enum import ZoneStatusEnum
from .exceptions.connection_error import IAlarmMkConnectionError
from .models.alarm_status_model import AlarmStatusModel
from .models.network_info_model import NetworkInfoModel
from .models.zone_model import ZoneModel

logger = logging.getLogger(__name__)

_KEEPALIVE_DEFAULT = 30  # seconds between application-level keepalive pings


class IAlarmMkClient:
    """Async client for iAlarm-MK alarm panels.

    Wraps :class:`MeianClient` (synchronous TCP) and offloads every
    blocking call to a thread-pool executor so the event loop is never
    blocked.

    A per-instance :class:`asyncio.Lock` serializes all commands so
    concurrent callers (e.g. Home Assistant coordinators) never race on
    the underlying socket.

    On ``IAlarmMkConnectionError`` each command automatically attempts
    one reconnect before re-raising, which covers the common case of an
    idle connection being dropped by the panel.

    A background keepalive task polls ``get_alarm_status`` every
    ``keepalive_interval`` seconds to prevent the panel from dropping an
    idle connection. Set ``keepalive_interval=None`` to disable.

    **Unsolicited push events on the command connection**

    When the command TCP connection is kept open (i.e. not closed after
    each command), the panel will also push real-time alarm event frames
    (``@alA`` / ``!lmX``) on it, the same events delivered by
    :class:`IAlarmMkPushClient` on a dedicated push connection.  Set
    ``on_event`` to a callable to receive these events:

    .. code-block:: python

        def handle(event: dict):
            print("panel event:", event)

        client.on_event = handle

    The callback is invoked from a worker thread; schedule any
    asyncio work with ``asyncio.run_coroutine_threadsafe``.

    This means that for use cases where a persistent command connection
    is already maintained (e.g. Home Assistant), a separate
    :class:`IAlarmMkPushClient` connection is not required; events
    arrive automatically on the existing connection.

    Typical usage::

        async with IAlarmMkClient("192.168.1.100", 8000, "user", "pass") as client:
            status = await client.get_status()
    """

    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: float = 10.0, keepalive_interval: int | None = _KEEPALIVE_DEFAULT):
        self._client = MeianClient(host, port, username, password, timeout)
        self._host = host
        self._port = port
        self._lock = asyncio.Lock()
        self._keepalive_interval = keepalive_interval
        self._keepalive_task: asyncio.Task | None = None
        logger.debug("IAlarmMkClient initialised for %s:%d", host, port)

    @property
    def on_event(self) -> Callable[[dict], None] | None:
        """Callback invoked (from a worker thread) when the panel pushes an unsolicited event."""
        return self._client.on_unsolicited

    @on_event.setter
    def on_event(self, callback: Callable[[dict], None] | None) -> None:
        self._client.on_unsolicited = callback

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> None:
        """Open a TCP connection, authenticate, and start the keepalive task."""
        logger.debug("connect: logging in to %s:%d", self._host, self._port)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.login)
        logger.debug("connect: logged in successfully")
        self._start_keepalive()

    async def disconnect(self) -> None:
        """Stop the keepalive task and close the TCP connection."""
        self._stop_keepalive()
        logger.debug("disconnect: logging out from %s:%d", self._host, self._port)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.logout)
        logger.debug("disconnect: done")

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    def _start_keepalive(self) -> None:
        if self._keepalive_interval is None:
            return
        self._stop_keepalive()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._keepalive_task = loop.create_task(
            self._keepalive_loop(), name="ialarm-mk-keepalive"
        )
        logger.debug("_start_keepalive: task started (interval=%ds)", self._keepalive_interval)

    def _stop_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            logger.debug("_stop_keepalive: task cancelled")
        self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Poll get_alarm_status every keepalive_interval seconds to keep the connection alive."""
        try:
            while True:
                await asyncio.sleep(self._keepalive_interval)
                async with self._lock:
                    loop = asyncio.get_running_loop()
                    try:
                        await loop.run_in_executor(None, self._client.get_alarm_status)
                        logger.debug("_keepalive_loop: ping ok")
                    except IAlarmMkConnectionError:
                        logger.warning("_keepalive_loop: connection lost, attempting reconnect")
                        try:
                            await loop.run_in_executor(None, self._client.login)
                            logger.debug("_keepalive_loop: reconnected")
                        except Exception as exc:
                            logger.error("_keepalive_loop: reconnect failed: %s", exc)
        except asyncio.CancelledError:
            logger.debug("_keepalive_loop: cancelled")

    # ------------------------------------------------------------------
    # Internal command runner
    # ------------------------------------------------------------------

    async def _run(self, fn, *args):
        """Run a blocking MeianClient call under the instance lock.

        On IAlarmMkConnectionError, reconnects once and retries before
        re-raising so callers are shielded from transient drops.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                return await loop.run_in_executor(None, fn, *args)
            except IAlarmMkConnectionError:
                logger.warning("_run: connection lost, attempting reconnect to %s:%d", self._host, self._port)
                try:
                    await loop.run_in_executor(None, self._client.login)
                    logger.debug("_run: reconnected, retrying command")
                    return await loop.run_in_executor(None, fn, *args)
                except Exception as exc:
                    logger.error("_run: reconnect failed: %s", exc)
                    raise

    async def get_status(self) -> AlarmStatusModel:
        """Return the current arm/disarm status of the panel."""
        logger.debug("get_status: requesting alarm status")
        data = await self._run(self._client.get_alarm_status)
        raw = (data or {}).get("DevStatus")
        try:
            status = AlarmStatusEnum(int(raw))
        except (TypeError, ValueError):
            logger.debug("get_status: DevStatus %r not parseable (panel may be transitioning), returning UNAVAILABLE", raw)
            status = AlarmStatusEnum.UNAVAILABLE
        logger.debug("get_status: status=%s (%d)", status.name, status.value)
        return AlarmStatusModel(status=status)

    async def get_network_info(self) -> NetworkInfoModel:
        """Return the panel's network configuration (name, MAC, IP)."""
        logger.debug("get_network_info: requesting network info")
        data = await self._run(self._client.get_network_info)
        model = NetworkInfoModel.from_dict(data or {})
        logger.debug("get_network_info: name=%s mac=%s ip=%s", model.name, model.mac, model.ip)
        return model

    async def get_zones(self) -> list[ZoneModel]:
        """Return all configured zones as a list of :class:`ZoneModel`."""
        logger.debug("get_zones: requesting zone list")
        raw_zones = await self._run(self._client.get_zones)
        zones: list[ZoneModel] = []
        for i, zone in enumerate(raw_zones):
            if zone is None:
                logger.debug("get_zones: skipping None entry at index %d", i)
                continue
            status_raw = zone.get("Status") or 0
            zones.append(
                ZoneModel(
                    index=i,
                    name=zone.get("Name") or "",
                    zone_type=zone.get("Type") or 0,
                    status=ZoneStatusEnum(int(status_raw)),
                )
            )
        logger.debug("get_zones: mapped %d zone(s) (raw entries=%d)", len(zones), len(raw_zones))
        return zones

    async def arm_away(self) -> None:
        """Arm the panel in away mode (all zones active)."""
        logger.debug("arm_away: sending ARM command")
        await self._run(self._client.set_alarm_status, 0)
        logger.debug("arm_away: done")

    async def disarm(self) -> None:
        """Disarm the panel."""
        logger.debug("disarm: sending DISARM command")
        await self._run(self._client.set_alarm_status, 1)
        logger.debug("disarm: done")

    async def arm_stay(self) -> None:
        """Arm the panel in stay mode (perimeter zones only)."""
        logger.debug("arm_stay: sending STAY command")
        await self._run(self._client.set_alarm_status, 2)
        logger.debug("arm_stay: done")

    async def cancel_alarm(self) -> None:
        """Cancel an active alarm."""
        logger.debug("cancel_alarm: sending CLEAR command")
        await self._run(self._client.set_alarm_status, 3)
        logger.debug("cancel_alarm: done")

    async def arm_partial(self) -> None:
        """Arm the panel in partial mode."""
        logger.debug("arm_partial: sending PARTIAL command")
        await self._run(self._client.set_alarm_status, 8)
        logger.debug("arm_partial: done")


