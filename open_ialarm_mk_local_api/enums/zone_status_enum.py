from enum import IntFlag


class ZoneStatusEnum(IntFlag):
    NOT_USED = 0
    IN_USE = 1 << 0
    ALARM = 1 << 1
    BYPASS = 1 << 2
    FAULT = 1 << 3
    LOW_BATTERY = 1 << 4
    LOSS = 1 << 5
