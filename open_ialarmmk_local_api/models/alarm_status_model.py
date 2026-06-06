from dataclasses import dataclass

from ..enums.alarm_status_enum import AlarmStatusEnum


@dataclass
class AlarmStatusModel:
    status: AlarmStatusEnum
