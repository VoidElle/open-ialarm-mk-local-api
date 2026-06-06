import asyncio
import logging

from ._internal.meian_client import MeianClient
from .enums.alarm_status_enum import AlarmStatusEnum
from .enums.zone_status_enum import ZoneStatusEnum
from .models.alarm_status_model import AlarmStatusModel
from .models.network_info_model import NetworkInfoModel
from .models.zone_model import ZoneModel

logger = logging.getLogger(__name__)


class IAlarmMkClient:
    """Async client for iAlarm-MK alarm panels.

    Wraps :class:`MeianClient` (synchronous TCP) and offloads every
    blocking call to a thread-pool executor so the event loop is never
    blocked.

    Typical usage::

        async with IAlarmMkClient("192.168.1.100", 18034, "user", "pass") as client:
            status = await client.get_status()
    """

    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 10.0):
        self._client = MeianClient(host, port, username, password, timeout)
        self._host = host
        self._port = port
        logger.debug("IAlarmMkClient initialised for %s:%d", host, port)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> None:
        # MeianClient.login() does blocking TCP I/O; run it off the event loop.
        logger.debug("connect: logging in to %s:%d", self._host, self._port)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.login)
        logger.debug("connect: logged in successfully")

    async def disconnect(self) -> None:
        logger.debug("disconnect: logging out from %s:%d", self._host, self._port)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.logout)
        logger.debug("disconnect: done")

    async def get_status(self) -> AlarmStatusModel:
        """Return the current arm/disarm status of the panel."""
        logger.debug("get_status: requesting alarm status")
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, self._client.get_alarm_status)
        raw = data.get("DevStatus", 0) or 0
        try:
            status = AlarmStatusEnum(int(raw))
        except ValueError:
            # Panel returned an undocumented status code; report as unavailable.
            logger.warning("get_status: unknown DevStatus value %r, mapping to UNAVAILABLE", raw)
            status = AlarmStatusEnum.UNAVAILABLE
        logger.debug("get_status: status=%s (%d)", status.name, status.value)
        return AlarmStatusModel(status=status)

    async def get_network_info(self) -> NetworkInfoModel:
        """Return the panel's network configuration (name, MAC, IP)."""
        logger.debug("get_network_info: requesting network info")
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, self._client.get_network_info)
        model = NetworkInfoModel.from_dict(data)
        logger.debug("get_network_info: name=%s mac=%s ip=%s", model.name, model.mac, model.ip)
        return model

    async def get_zones(self) -> list[ZoneModel]:
        """Return all configured zones as a list of :class:`ZoneModel`."""
        logger.debug("get_zones: requesting zone list")
        loop = asyncio.get_running_loop()
        raw_zones = await loop.run_in_executor(None, self._client.get_zones)
        zones: list[ZoneModel] = []
        for i, zone in enumerate(raw_zones):
            if zone is None:
                logger.debug("get_zones: skipping None entry at index %d", i)
                continue
            status_raw = zone.get("Status", 0) or 0
            zones.append(
                ZoneModel(
                    index=i,
                    name=zone.get("Name", ""),
                    zone_type=zone.get("Type", 0) or 0,
                    status=ZoneStatusEnum(int(status_raw)),
                )
            )
        logger.debug("get_zones: mapped %d zone(s) (raw entries=%d)", len(zones), len(raw_zones))
        return zones

    async def arm_away(self) -> None:
        """Arm the panel in away mode (all zones active)."""
        logger.debug("arm_away: sending ARM command")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 0)
        logger.debug("arm_away: done")

    async def disarm(self) -> None:
        """Disarm the panel."""
        logger.debug("disarm: sending DISARM command")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 1)
        logger.debug("disarm: done")

    async def arm_stay(self) -> None:
        """Arm the panel in stay mode (perimeter zones only)."""
        logger.debug("arm_stay: sending STAY command")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 2)
        logger.debug("arm_stay: done")

    async def cancel_alarm(self) -> None:
        """Cancel an active alarm."""
        logger.debug("cancel_alarm: sending CLEAR command")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 3)
        logger.debug("cancel_alarm: done")

    async def arm_partial(self) -> None:
        """Arm the panel in partial mode."""
        logger.debug("arm_partial: sending PARTIAL command")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 8)
        logger.debug("arm_partial: done")

