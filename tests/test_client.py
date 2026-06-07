import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from open_ialarm_mk_local_api.enums.alarm_status_enum import AlarmStatusEnum
from open_ialarm_mk_local_api.enums.zone_status_enum import ZoneStatusEnum
from open_ialarm_mk_local_api.exceptions.connection_error import IAlarmMkConnectionError
from open_ialarm_mk_local_api.exceptions.login_error import IAlarmMkLoginError
from open_ialarm_mk_local_api.ialarmmk_client import IAlarmMkClient
from open_ialarm_mk_local_api.ialarmmk_push_client import IAlarmMkPushClient


class TestIAlarmMkClient(unittest.IsolatedAsyncioTestCase):

    # ------------------------------------------------------------------
    # connect / disconnect
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_connect_and_disconnect_delegate(self, meian_client_cls):
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        await client.connect()
        await client.disconnect()

        backend.login.assert_called_once_with()
        backend.logout.assert_called_once_with()

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_context_manager_connects_and_disconnects(self, meian_client_cls):
        backend = MagicMock()
        meian_client_cls.return_value = backend

        async with IAlarmMkClient("host", 1234, "user", "pass"):
            backend.login.assert_called_once()

        backend.logout.assert_called_once()

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_connect_propagates_connection_error(self, meian_client_cls):
        backend = MagicMock()
        backend.login.side_effect = IAlarmMkConnectionError("timeout")
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        with self.assertRaises(IAlarmMkConnectionError):
            await client.connect()

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_connect_propagates_login_error(self, meian_client_cls):
        backend = MagicMock()
        backend.login.side_effect = IAlarmMkLoginError("bad creds")
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        with self.assertRaises(IAlarmMkLoginError):
            await client.connect()

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_status_maps_known_value(self, meian_client_cls):
        backend = MagicMock()
        backend.get_alarm_status.return_value = {"DevStatus": 2}
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        status = await client.get_status()

        self.assertEqual(status.status, AlarmStatusEnum.ARMED_STAY)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_status_maps_unknown_value_to_unavailable(self, meian_client_cls):
        backend = MagicMock()
        backend.get_alarm_status.return_value = {"DevStatus": 99}
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        status = await client.get_status()

        self.assertEqual(status.status, AlarmStatusEnum.UNAVAILABLE)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_status_handles_none_dev_status(self, meian_client_cls):
        backend = MagicMock()
        backend.get_alarm_status.return_value = {"DevStatus": None}
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        status = await client.get_status()

        self.assertEqual(status.status, AlarmStatusEnum.UNAVAILABLE)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_status_all_enum_values(self, meian_client_cls):
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        for member in AlarmStatusEnum:
            backend.get_alarm_status.return_value = {"DevStatus": member.value}
            status = await client.get_status()
            self.assertEqual(status.status, member)

    # ------------------------------------------------------------------
    # get_network_info
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_network_info_maps_model(self, meian_client_cls):
        backend = MagicMock()
        backend.get_network_info.return_value = {"Mac": "AA", "Name": "Panel", "Ip": "10.0.0.2"}
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        info = await client.get_network_info()

        self.assertEqual(info.mac, "AA")
        self.assertEqual(info.name, "Panel")
        self.assertEqual(info.ip, "10.0.0.2")

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_network_info_missing_name_defaults(self, meian_client_cls):
        backend = MagicMock()
        backend.get_network_info.return_value = {"Mac": "BB:CC", "Ip": "1.2.3.4"}
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        info = await client.get_network_info()

        self.assertEqual(info.name, "iAlarm-MK")

    # ------------------------------------------------------------------
    # get_zones
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_zones_maps_zone_models(self, meian_client_cls):
        backend = MagicMock()
        backend.get_zones.return_value = [
            {"Name": "Front Door", "Type": 1, "Status": int(ZoneStatusEnum.IN_USE | ZoneStatusEnum.FAULT)},
            None,
            {"Name": "Kitchen", "Type": 3, "Status": int(ZoneStatusEnum.BYPASS)},
        ]
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        zones = await client.get_zones()

        self.assertEqual(len(zones), 2)
        self.assertEqual(zones[0].name, "Front Door")
        self.assertTrue(zones[0].is_open)
        self.assertEqual(zones[1].status, ZoneStatusEnum.BYPASS)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_zones_empty_list(self, meian_client_cls):
        backend = MagicMock()
        backend.get_zones.return_value = []
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        zones = await client.get_zones()

        self.assertEqual(zones, [])

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_zones_none_status_treated_as_zero(self, meian_client_cls):
        backend = MagicMock()
        backend.get_zones.return_value = [{"Name": "Sensor", "Type": 0, "Status": None}]
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        zones = await client.get_zones()

        self.assertEqual(zones[0].status, ZoneStatusEnum.NOT_USED)
        self.assertFalse(zones[0].is_open)
        self.assertFalse(zones[0].is_bypassed)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_get_zones_preserves_enumerate_index(self, meian_client_cls):
        backend = MagicMock()
        backend.get_zones.return_value = [
            None,
            {"Name": "Zone B", "Type": 0, "Status": 0},
        ]
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        zones = await client.get_zones()

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].index, 1)

    # ------------------------------------------------------------------
    # arm / disarm commands
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_alarm_commands_forward_expected_status_codes(self, meian_client_cls):
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        await client.arm_away()
        await client.disarm()
        await client.arm_stay()
        await client.cancel_alarm()
        await client.arm_partial()

        self.assertEqual(
            [c.args[0] for c in backend.set_alarm_status.call_args_list],
            [0, 1, 2, 3, 8],
        )

    # ------------------------------------------------------------------
    # _run: lock serialization
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_concurrent_calls_are_serialized(self, meian_client_cls):
        """Concurrent commands must not overlap on the socket."""
        order = []
        def slow_status():
            order.append("start")
            import time; time.sleep(0.01)
            order.append("end")
            return {"DevStatus": 1}

        backend = MagicMock()
        backend.get_alarm_status.side_effect = slow_status
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        await asyncio.gather(client.get_status(), client.get_status())

        self.assertEqual(order, ["start", "end", "start", "end"])

    # ------------------------------------------------------------------
    # _run: auto-reconnect
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_reconnects_on_connection_error_and_retries(self, meian_client_cls):
        """On IAlarmMkConnectionError, client reconnects and retries the command."""
        call_count = 0
        def flaky_status():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise IAlarmMkConnectionError("dropped")
            return {"DevStatus": 1}

        backend = MagicMock()
        backend.get_alarm_status.side_effect = flaky_status
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        result = await client.get_status()

        self.assertEqual(result.status, AlarmStatusEnum.DISARMED)
        backend.login.assert_called_once()

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_raises_if_reconnect_also_fails(self, meian_client_cls):
        """If reconnect itself fails, the original error is re-raised."""
        backend = MagicMock()
        backend.get_alarm_status.side_effect = IAlarmMkConnectionError("dropped")
        backend.login.side_effect = IAlarmMkConnectionError("still down")
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass")

        with self.assertRaises(IAlarmMkConnectionError):
            await client.get_status()

    # ------------------------------------------------------------------
    # keepalive task
    # ------------------------------------------------------------------

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_keepalive_task_started_on_connect(self, meian_client_cls):
        """Keepalive background task is created after connect()."""
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass", keepalive_interval=30)
        await client.connect()
        self.assertIsNotNone(client._keepalive_task)
        self.assertFalse(client._keepalive_task.done())
        await client.disconnect()

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_keepalive_task_cancelled_on_disconnect(self, meian_client_cls):
        """Keepalive task is cancelled when disconnect() is called."""
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass", keepalive_interval=30)
        await client.connect()
        await client.disconnect()
        self.assertIsNone(client._keepalive_task)

    @patch("open_ialarm_mk_local_api.ialarmmk_client.MeianClient")
    async def test_keepalive_disabled_when_interval_is_none(self, meian_client_cls):
        """No keepalive task is created when keepalive_interval=None."""
        backend = MagicMock()
        meian_client_cls.return_value = backend
        client = IAlarmMkClient("host", 1234, "user", "pass", keepalive_interval=None)
        await client.connect()
        self.assertIsNone(client._keepalive_task)
        await client.disconnect()


class TestIAlarmMkPushClient(unittest.IsolatedAsyncioTestCase):

    async def test_cancel_before_subscribe_is_safe(self):
        client = IAlarmMkPushClient("host", 1234, "user", lambda e: None)
        client.cancel()
        self.assertTrue(client._cancelled)

    async def test_cancel_closes_transport(self):
        client = IAlarmMkPushClient("host", 1234, "user", lambda e: None)
        transport = MagicMock()
        transport.is_closing.return_value = False
        client._transport = transport
        client.cancel()
        transport.close.assert_called_once()

    async def test_subscribe_stops_when_cancelled(self):
        client = IAlarmMkPushClient("host", 1234, "user", lambda e: None)
        client._cancelled = True
        # Should return immediately without attempting any connection.
        await client.subscribe()
