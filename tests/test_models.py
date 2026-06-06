import unittest

from open_ialarmmk_local_api.enums.alarm_status_enum import AlarmStatusEnum
from open_ialarmmk_local_api.enums.zone_status_enum import ZoneStatusEnum
from open_ialarmmk_local_api.models.alarm_status_model import AlarmStatusModel
from open_ialarmmk_local_api.models.network_info_model import NetworkInfoModel
from open_ialarmmk_local_api.models.zone_model import ZoneModel


class TestNetworkInfoModel(unittest.TestCase):
    def test_from_dict_full(self):
        info = NetworkInfoModel.from_dict({"Mac": "AA:BB:CC:DD:EE:FF", "Name": "MyPanel", "Ip": "192.168.1.10"})
        self.assertEqual(info.mac, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(info.name, "MyPanel")
        self.assertEqual(info.ip, "192.168.1.10")

    def test_from_dict_defaults_name(self):
        info = NetworkInfoModel.from_dict({"Mac": "AA:BB:CC:DD:EE:FF", "Ip": "192.168.1.10"})
        self.assertEqual(info.name, "iAlarm-MK")

    def test_from_dict_empty_dict(self):
        info = NetworkInfoModel.from_dict({})
        self.assertEqual(info.mac, "")
        self.assertEqual(info.name, "iAlarm-MK")
        self.assertEqual(info.ip, "")


class TestAlarmStatusModel(unittest.TestCase):
    def test_stores_status(self):
        model = AlarmStatusModel(status=AlarmStatusEnum.ARMED_AWAY)
        self.assertEqual(model.status, AlarmStatusEnum.ARMED_AWAY)

    def test_all_enum_members_round_trip(self):
        for member in AlarmStatusEnum:
            model = AlarmStatusModel(status=member)
            self.assertEqual(model.status, member)


class TestAlarmStatusEnum(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(AlarmStatusEnum.ARMED_AWAY.value, 0)
        self.assertEqual(AlarmStatusEnum.DISARMED.value, 1)
        self.assertEqual(AlarmStatusEnum.ARMED_STAY.value, 2)
        self.assertEqual(AlarmStatusEnum.CANCEL.value, 3)
        self.assertEqual(AlarmStatusEnum.TRIGGERED.value, 4)
        self.assertEqual(AlarmStatusEnum.ALARM_ARMING.value, 5)
        self.assertEqual(AlarmStatusEnum.UNAVAILABLE.value, 6)
        self.assertEqual(AlarmStatusEnum.ARMED_PARTIAL.value, 8)


class TestZoneStatusEnum(unittest.TestCase):
    def test_not_used_is_zero(self):
        self.assertEqual(int(ZoneStatusEnum.NOT_USED), 0)

    def test_flags_combine(self):
        flags = ZoneStatusEnum.IN_USE | ZoneStatusEnum.ALARM
        self.assertTrue(flags & ZoneStatusEnum.IN_USE)
        self.assertTrue(flags & ZoneStatusEnum.ALARM)
        self.assertFalse(flags & ZoneStatusEnum.BYPASS)

    def test_all_bit_positions(self):
        self.assertEqual(int(ZoneStatusEnum.IN_USE), 1)
        self.assertEqual(int(ZoneStatusEnum.ALARM), 2)
        self.assertEqual(int(ZoneStatusEnum.BYPASS), 4)
        self.assertEqual(int(ZoneStatusEnum.FAULT), 8)
        self.assertEqual(int(ZoneStatusEnum.LOW_BATTERY), 16)
        self.assertEqual(int(ZoneStatusEnum.LOSS), 32)


class TestZoneModel(unittest.TestCase):
    def _make(self, status: ZoneStatusEnum) -> ZoneModel:
        return ZoneModel(index=0, name="Test", zone_type=1, status=status)

    def test_is_open_requires_in_use_and_fault(self):
        self.assertTrue(self._make(ZoneStatusEnum.IN_USE | ZoneStatusEnum.FAULT).is_open)

    def test_is_open_false_when_only_in_use(self):
        self.assertFalse(self._make(ZoneStatusEnum.IN_USE).is_open)

    def test_is_open_false_when_only_fault(self):
        self.assertFalse(self._make(ZoneStatusEnum.FAULT).is_open)

    def test_is_open_false_when_not_used(self):
        self.assertFalse(self._make(ZoneStatusEnum.NOT_USED).is_open)

    def test_is_bypassed(self):
        self.assertTrue(self._make(ZoneStatusEnum.BYPASS).is_bypassed)
        self.assertFalse(self._make(ZoneStatusEnum.IN_USE).is_bypassed)

    def test_low_battery(self):
        self.assertTrue(self._make(ZoneStatusEnum.LOW_BATTERY).low_battery)
        self.assertFalse(self._make(ZoneStatusEnum.IN_USE).low_battery)

    def test_signal_loss(self):
        self.assertTrue(self._make(ZoneStatusEnum.LOSS).signal_loss)
        self.assertFalse(self._make(ZoneStatusEnum.IN_USE).signal_loss)

    def test_all_flags_simultaneously(self):
        all_flags = (
            ZoneStatusEnum.IN_USE
            | ZoneStatusEnum.ALARM
            | ZoneStatusEnum.BYPASS
            | ZoneStatusEnum.FAULT
            | ZoneStatusEnum.LOW_BATTERY
            | ZoneStatusEnum.LOSS
        )
        zone = self._make(all_flags)
        self.assertTrue(zone.is_open)
        self.assertTrue(zone.is_bypassed)
        self.assertTrue(zone.low_battery)
        self.assertTrue(zone.signal_loss)
