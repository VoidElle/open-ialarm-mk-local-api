from dataclasses import dataclass

from ..enums.zone_status_enum import ZoneStatusEnum


@dataclass
class ZoneModel:
    index: int
    name: str
    zone_type: int
    status: ZoneStatusEnum

    @property
    def is_open(self) -> bool:
        return bool(self.status & ZoneStatusEnum.IN_USE and self.status & ZoneStatusEnum.FAULT)

    @property
    def is_bypassed(self) -> bool:
        return bool(self.status & ZoneStatusEnum.BYPASS)

    @property
    def low_battery(self) -> bool:
        return bool(self.status & ZoneStatusEnum.LOW_BATTERY)

    @property
    def signal_loss(self) -> bool:
        return bool(self.status & ZoneStatusEnum.LOSS)
