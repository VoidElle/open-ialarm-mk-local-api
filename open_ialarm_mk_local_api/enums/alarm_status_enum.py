from enum import IntEnum


class AlarmStatusEnum(IntEnum):
    ARMED_AWAY = 0
    DISARMED = 1
    ARMED_STAY = 2
    CANCEL = 3
    TRIGGERED = 4
    ALARM_ARMING = 5
    UNAVAILABLE = 6
    ARMED_PARTIAL = 8
