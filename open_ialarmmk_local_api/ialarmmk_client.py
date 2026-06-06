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
    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 10.0):
        self._client = MeianClient(host, port, username, password, timeout)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> None:
        # MeianClient.login() does blocking TCP I/O; run it off the event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.login)

    async def disconnect(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.logout)

    async def get_status(self) -> AlarmStatusModel:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, self._client.get_alarm_status)
        raw = data.get("DevStatus", 0) or 0
        try:
            status = AlarmStatusEnum(int(raw))
        except ValueError:
            # Panel returned an undocumented status code; report as unavailable.
            status = AlarmStatusEnum.UNAVAILABLE
        return AlarmStatusModel(status=status)

    async def get_network_info(self) -> NetworkInfoModel:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, self._client.get_network_info)
        return NetworkInfoModel.from_dict(data)

    async def get_zones(self) -> list[ZoneModel]:
        loop = asyncio.get_running_loop()
        raw_zones = await loop.run_in_executor(None, self._client.get_zones)
        zones: list[ZoneModel] = []
        for i, zone in enumerate(raw_zones):
            if zone is None:
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
        return zones

    async def arm_away(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 0)

    async def disarm(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 1)

    async def arm_stay(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 2)

    async def cancel_alarm(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 3)

    async def arm_partial(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.set_alarm_status, 8)
