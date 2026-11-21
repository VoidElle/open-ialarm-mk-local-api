"""Tests for the low-level Meian wire-protocol helpers in _internal/."""

import socket
import unittest
from unittest.mock import MagicMock, patch

from open_ialarm_mk_local_api._internal.meian_push_client import MeianPushProtocol
from open_ialarm_mk_local_api._internal.meian_client import (
    MeianClient,
    _bol,
    _create,
    _ipa,
    _mac,
    _pwd,
    _s32,
    _select,
    _str,
    _typ,
    _xor,
    _xmlread,
)
from open_ialarm_mk_local_api.exceptions.connection_error import IAlarmMkConnectionError
from open_ialarm_mk_local_api.exceptions.login_error import IAlarmMkLoginError


# ---------------------------------------------------------------------------
# Helper: build a valid Meian wire frame from raw XML bytes
# ---------------------------------------------------------------------------

def _build_frame(xml: bytes, seq: int = 1) -> bytes:
    """Return a complete @ieM framed message for xml."""
    return b"@ieM%04d%04d0000%s%04d" % (len(xml), seq, _xor(xml), seq)


def _build_unsolicited_frame(xml: bytes, magic: bytes, seq: int = 0) -> bytes:
    """Return a complete unsolicited alarm frame."""
    payload = _xor(xml) if magic == b"@alA" else xml
    return magic + (b"%04d%04d0000%s%04d" % (len(xml), seq, payload, seq))


def _chunked_recv(data: bytes, chunk_size: int):
    """Return a side_effect list that feeds *data* in chunks of *chunk_size*."""
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
    return chunks + [b""]  # trailing empty signals EOF (should never be reached)


class TestXor(unittest.TestCase):
    def test_xor_is_own_inverse(self):
        payload = b"Hello, iAlarm-MK! " * 10
        self.assertEqual(bytes(_xor(_xor(payload))), payload)

    def test_xor_empty(self):
        self.assertEqual(_xor(b""), bytearray())

    def test_xor_single_byte(self):
        # XOR of 0x00 with first key byte (0x0c) should give 0x0c.
        result = _xor(b"\x00")
        self.assertEqual(result[0], 0x0C)

    def test_xor_wraps_at_128(self):
        # Byte 0 and byte 128 share the same key byte.
        data = bytearray(129)
        result = _xor(data)
        self.assertEqual(result[0], result[128])


class TestTypeEncoders(unittest.TestCase):
    def test_bol_true(self):
        self.assertEqual(_bol(True), "BOL|T")

    def test_bol_false(self):
        self.assertEqual(_bol(False), "BOL|F")

    def test_str_encodes_length(self):
        self.assertEqual(_str("hello"), "STR,5|hello")

    def test_str_empty(self):
        self.assertEqual(_str(""), "STR,0|")

    def test_s32_default_pos(self):
        self.assertEqual(_s32(42), "S32,0,0|42")

    def test_s32_with_pos(self):
        self.assertEqual(_s32(1, 1), "S32,1,1|1")

    def test_pwd(self):
        self.assertEqual(_pwd("secret"), "PWD,6|secret")

    def test_typ_known_index(self):
        result = _typ(0, ["ARM", "DISARM", "STAY"])
        self.assertEqual(result, "TYP,ARM|0")

    def test_typ_out_of_range(self):
        result = _typ(99, ["ARM"])
        self.assertEqual(result, "TYP,NONE,|99")

    def test_typ_empty_list(self):
        result = _typ(0, [])
        self.assertEqual(result, "TYP,NONE,|0")


class TestCreate(unittest.TestCase):
    def test_single_level(self):
        result = _create("/Root", {"key": "val"})
        self.assertEqual(result, {"Root": {"key": "val"}})

    def test_nested_path(self):
        result = _create("/Root/Host/GetZone", {"Total": None})
        self.assertEqual(result["Root"]["Host"]["GetZone"], {"Total": None})

    def test_empty_dict(self):
        result = _create("/A/B")
        self.assertEqual(result, {"A": {"B": {}}})


class TestSelect(unittest.TestCase):
    def test_nested_select(self):
        data = {"Root": {"Host": {"GetZone": {"Total": 5}}}}
        self.assertEqual(_select(data, "/Root/Host/GetZone/Total"), 5)

    def test_missing_key_returns_none(self):
        self.assertIsNone(_select({}, "/Root/Missing"))

    def test_none_input_returns_none(self):
        self.assertIsNone(_select(None, "/Root"))


class TestXmlread(unittest.TestCase):
    def test_bol_true(self):
        _, val = _xmlread("/", "key", "BOL|T")
        self.assertIs(val, True)

    def test_bol_false(self):
        _, val = _xmlread("/", "key", "BOL|F")
        self.assertIs(val, False)

    def test_s32(self):
        _, val = _xmlread("/", "key", "S32,0,0|7")
        self.assertEqual(val, 7)

    def test_str(self):
        _, val = _xmlread("/", "key", "STR,5|hello")
        self.assertEqual(val, "hello")

    def test_typ(self):
        _, val = _xmlread("/", "key", "TYP,ARM|0")
        self.assertEqual(val, 0)

    def test_err(self):
        _, val = _xmlread("/", "key", "ERR|00")
        self.assertEqual(val, 0)

    def test_non_string_passthrough(self):
        # Non-string values (e.g. already-decoded ints) must pass through unchanged.
        _, val = _xmlread("/", "key", 42)
        self.assertEqual(val, 42)

    def test_none_passthrough(self):
        _, val = _xmlread("/", "key", None)
        self.assertIsNone(val)


# ---------------------------------------------------------------------------
# MeianClient._receive() — framed TCP read (Bug #1 regression tests)
# ---------------------------------------------------------------------------

_SIMPLE_XML = b"<Root><Host><GetAlarmStatus><DevStatus>S32,0,0|0</DevStatus></GetAlarmStatus></Host></Root>"
_UNSOLICITED_XML = (
    b"<Root><Host><Alarm>"
    b"<Cid>S32,0,0|1132</Cid>"
    b"<Zone>S32,0,0|1</Zone>"
    b"</Alarm></Host></Root>"
)

# A large XML that exceeds 1024 bytes (simulates MK7 login response).
# All values use valid Meian type tags so _xmlread can decode them.
_LARGE_XML = (
    b"<Root><Pair><Client>"
    b"<Id>STR,4|user</Id>"
    b"<Pwd>PWD,4|pass</Pwd>"
    b"<Token>STR,36|" + b"a" * 36 + b"</Token>"
    b"<DevVersion>STR,5|1.0.0</DevVersion>"
    b"<DevType>STR,3|MK7</DevType>"
    + b"".join(
        b"<Zone" + str(i).encode() + b">S32,0,0|" + str(i).encode() + b"</Zone" + str(i).encode() + b">"
        for i in range(40)
    )
    + b"<Err>ERR|00</Err>"
    b"</Client></Pair></Root>"
)


def _make_client() -> MeianClient:
    client = MeianClient("192.168.1.1", 8000, "user", "pass")
    client._sock = MagicMock(spec=socket.socket)
    return client


# ---------------------------------------------------------------------------
# MeianClient._send() — four-digit sequence rollover
# ---------------------------------------------------------------------------

class TestMeianClientSend(unittest.TestCase):

    def test_send_sequence_wraps_after_9999(self):
        """The four-digit wire sequence wraps from 9999 back to 0001."""
        client = _make_client()
        client._seq = 9998
        root = _create(
            "/Root/Host/GetAlarmStatus",
            {"DevStatus": None},
        )

        client._send(root)
        client._send(root)

        first_frame = client._sock.send.call_args_list[0].args[0]
        second_frame = client._sock.send.call_args_list[1].args[0]

        self.assertEqual(first_frame[8:12], b"9999")
        self.assertEqual(first_frame[-4:], b"9999")
        self.assertEqual(second_frame[8:12], b"0001")
        self.assertEqual(second_frame[-4:], b"0001")
        self.assertEqual(client._seq, 1)

    def test_close_socket_resets_sequence(self):
        """A new authenticated TCP session starts with a fresh sequence."""
        client = _make_client()
        client._seq = 4321

        client.close_socket()

        self.assertEqual(client._seq, 0)


class TestMeianClientReceive(unittest.TestCase):

    def test_receive_forwards_xor_unsolicited_alarm_then_returns_response(self):
        """@alA is decoded, forwarded, and does not consume the command response."""
        callback = MagicMock()
        client = MeianClient(
            "192.168.1.1",
            8000,
            "user",
            "pass",
            on_unsolicited=callback,
        )
        client._sock = MagicMock(spec=socket.socket)

        event_frame = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA")
        response_frame = _build_frame(_SIMPLE_XML)
        client._sock.recv.side_effect = [
            event_frame[:16],
            event_frame[16:],
            response_frame[:16],
            response_frame[16:],
        ]

        result = client._receive()

        callback.assert_called_once_with({"Cid": 1132, "Zone": 1})
        self.assertEqual(
            result["Root"]["Host"]["GetAlarmStatus"]["DevStatus"],
            0,
        )

    def test_receive_forwards_plain_unsolicited_alarm_then_returns_response(self):
        """!lmX is decoded, forwarded, and does not consume the command response."""
        callback = MagicMock()
        client = MeianClient(
            "192.168.1.1",
            8000,
            "user",
            "pass",
            on_unsolicited=callback,
        )
        client._sock = MagicMock(spec=socket.socket)

        event_frame = _build_unsolicited_frame(_UNSOLICITED_XML, b"!lmX")
        response_frame = _build_frame(_SIMPLE_XML)
        client._sock.recv.side_effect = [
            event_frame[:16],
            event_frame[16:],
            response_frame[:16],
            response_frame[16:],
        ]

        result = client._receive()

        callback.assert_called_once_with({"Cid": 1132, "Zone": 1})
        self.assertEqual(
            result["Root"]["Host"]["GetAlarmStatus"]["DevStatus"],
            0,
        )

    def test_unsolicited_callback_error_does_not_break_command_response(self):
        """A consumer callback failure must not corrupt request/response handling."""
        callback = MagicMock(side_effect=RuntimeError("consumer failed"))
        client = MeianClient(
            "192.168.1.1",
            8000,
            "user",
            "pass",
            on_unsolicited=callback,
        )
        client._sock = MagicMock(spec=socket.socket)

        event_frame = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA")
        response_frame = _build_frame(_SIMPLE_XML)
        client._sock.recv.side_effect = [
            event_frame[:16],
            event_frame[16:],
            response_frame[:16],
            response_frame[16:],
        ]

        result = client._receive()

        callback.assert_called_once()
        self.assertEqual(
            result["Root"]["Host"]["GetAlarmStatus"]["DevStatus"],
            0,
        )

    def test_receive_small_payload_in_one_chunk(self):
        """Standard single-chunk recv still works after the fix."""
        client = _make_client()
        frame = _build_frame(_SIMPLE_XML)
        # Feed header in one recv, rest in another.
        client._sock.recv.side_effect = [frame[:16], frame[16:]]
        result = client._receive()
        self.assertIsNotNone(result)

    def test_receive_large_payload_exceeding_1024_bytes(self):
        """MK7 login response > 1024 bytes is read completely."""
        assert len(_LARGE_XML) > 1024, "test setup: XML must exceed 1024 bytes"
        client = _make_client()
        frame = _build_frame(_LARGE_XML)
        # Split as _receive() actually calls recv: 16 bytes for header, rest for body.
        header_chunk = frame[:16]
        body = frame[16:]
        body_chunks = [body[i:i + 4096] for i in range(0, len(body), 4096)]
        client._sock.recv.side_effect = [header_chunk] + body_chunks
        result = client._receive()
        self.assertIsNotNone(result)

    def test_receive_fragmented_header_delivery(self):
        """Header arriving 1 byte at a time is reassembled correctly."""
        client = _make_client()
        frame = _build_frame(_SIMPLE_XML)
        header_chunks = list(bytes([b]) for b in frame[:16])
        body = [frame[16:]]
        client._sock.recv.side_effect = header_chunks + body
        result = client._receive()
        self.assertIsNotNone(result)

    def test_receive_fragmented_body_delivery(self):
        """Body split into many small chunks is reassembled correctly."""
        client = _make_client()
        frame = _build_frame(_SIMPLE_XML)
        header = [frame[:16]]
        body_chunks = list(bytes([b]) for b in frame[16:])
        client._sock.recv.side_effect = header + body_chunks
        result = client._receive()
        self.assertIsNotNone(result)

    def test_receive_connection_closed_during_header(self):
        """Empty recv during header → IAlarmMkConnectionError."""
        client = _make_client()
        client._sock.recv.return_value = b""
        with self.assertRaises(IAlarmMkConnectionError) as ctx:
            client._receive()
        self.assertIn("header", str(ctx.exception).lower())

    def test_receive_connection_closed_during_payload(self):
        """Header OK but empty recv during body → IAlarmMkConnectionError."""
        client = _make_client()
        frame = _build_frame(_SIMPLE_XML)
        client._sock.recv.side_effect = [frame[:16], b""]
        with self.assertRaises(IAlarmMkConnectionError) as ctx:
            client._receive()
        self.assertIn("payload", str(ctx.exception).lower())

    def test_receive_socket_timeout_raises_connection_error(self):
        """socket.timeout during recv is converted to IAlarmMkConnectionError."""
        client = _make_client()
        client._sock.recv.side_effect = socket.timeout("timed out")
        with self.assertRaises(IAlarmMkConnectionError) as ctx:
            client._receive()
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_receive_os_error_raises_connection_error(self):
        """OSError during recv is converted to IAlarmMkConnectionError."""
        client = _make_client()
        client._sock.recv.side_effect = OSError("network unreachable")
        with self.assertRaises(IAlarmMkConnectionError):
            client._receive()

    def test_receive_without_socket_raises_connection_error(self):
        """Calling _receive() with no socket raises IAlarmMkConnectionError."""
        client = MeianClient("host", 8000, "u", "p")
        client._sock = None
        with self.assertRaises(IAlarmMkConnectionError):
            client._receive()


# ---------------------------------------------------------------------------
# MeianPushProtocol — TCP stream buffering
# ---------------------------------------------------------------------------

class TestMeianPushProtocolBuffering(unittest.TestCase):

    def _make_protocol(self):
        handler = MagicMock()
        on_con_lost = MagicMock()
        on_con_lost.done.return_value = False
        protocol = MeianPushProtocol("user", handler, on_con_lost)
        protocol._schedule_keepalive = MagicMock()
        return protocol, handler, on_con_lost

    def test_fragmented_alarm_frame_is_reassembled(self):
        """One alarm frame split across callbacks is delivered exactly once."""
        protocol, handler, _ = self._make_protocol()
        frame = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA")

        protocol.data_received(frame[:3])
        protocol.data_received(frame[3:11])
        protocol.data_received(frame[11:27])
        self.assertFalse(handler.called)

        protocol.data_received(frame[27:])

        handler.assert_called_once_with({"Cid": 1132, "Zone": 1})
        self.assertEqual(protocol._buffer, bytearray())

    def test_coalesced_alarm_frames_are_all_delivered(self):
        """Multiple complete frames in one TCP callback are all processed."""
        protocol, handler, _ = self._make_protocol()
        first = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA", seq=1)
        second = _build_unsolicited_frame(_UNSOLICITED_XML, b"!lmX", seq=2)

        protocol.data_received(first + second)

        self.assertEqual(handler.call_count, 2)
        self.assertEqual(
            handler.call_args_list[0].args[0],
            {"Cid": 1132, "Zone": 1},
        )
        self.assertEqual(
            handler.call_args_list[1].args[0],
            {"Cid": 1132, "Zone": 1},
        )
        self.assertEqual(protocol._buffer, bytearray())

    def test_keepalive_and_alarm_can_arrive_together(self):
        """A keepalive prefix does not hide a following framed alarm event."""
        protocol, handler, _ = self._make_protocol()
        frame = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA")

        protocol.data_received(b"%maI" + frame)

        protocol._schedule_keepalive.assert_called_once_with()
        handler.assert_called_once_with({"Cid": 1132, "Zone": 1})
        self.assertEqual(protocol._buffer, bytearray())

    def test_partial_second_frame_remains_buffered(self):
        """A complete first frame is emitted while the next fragment is retained."""
        protocol, handler, _ = self._make_protocol()
        first = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA", seq=1)
        second = _build_unsolicited_frame(_UNSOLICITED_XML, b"@alA", seq=2)

        protocol.data_received(first + second[:9])

        handler.assert_called_once()
        self.assertEqual(protocol._buffer, bytearray(second[:9]))

        protocol.data_received(second[9:])

        self.assertEqual(handler.call_count, 2)
        self.assertEqual(protocol._buffer, bytearray())


# ---------------------------------------------------------------------------
# MeianClient.login() — exception handling (Bug #2 regression tests)
# ---------------------------------------------------------------------------

def _make_mock_sock():
    """Return a socket mock that simulates a not-yet-connected socket."""
    sock = MagicMock()
    sock.fileno.return_value = 5
    # Raise OSError so is_socket_connected() returns False → login() proceeds to connect().
    sock.getpeername.side_effect = OSError("not connected")
    return sock


class TestMeianClientLogin(unittest.TestCase):

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_propagates_connection_error_from_receive(self, mock_socket_cls):
        """IAlarmMkConnectionError from _receive() propagates through login()."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.return_value = None
        mock_socket_cls.return_value = mock_sock
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with patch.object(client, "_send"), \
             patch.object(client, "_receive",
                          side_effect=IAlarmMkConnectionError("closed while reading header")):
            with self.assertRaises(IAlarmMkConnectionError) as ctx:
                client.login()
        self.assertIn("header", str(ctx.exception).lower())

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_wraps_expat_error_in_connection_error(self, mock_socket_cls):
        """XML parse failure (e.g. ExpatError from truncated response) is wrapped."""
        from xml.parsers.expat import ExpatError
        mock_sock = _make_mock_sock()
        mock_sock.connect.return_value = None
        mock_socket_cls.return_value = mock_sock
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with patch.object(client, "_send"), \
             patch.object(client, "_receive",
                          side_effect=ExpatError("syntax error: line 1, column 0")):
            with self.assertRaises(IAlarmMkConnectionError) as ctx:
                client.login()
        self.assertIn("syntax error", str(ctx.exception).lower())

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_wraps_unexpected_exception_with_detail(self, mock_socket_cls):
        """Any unexpected exception is wrapped and its message preserved."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.return_value = None
        mock_socket_cls.return_value = mock_sock
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with patch.object(client, "_send"), \
             patch.object(client, "_receive",
                          side_effect=RuntimeError("totally unexpected")):
            with self.assertRaises(IAlarmMkConnectionError) as ctx:
                client.login()
        self.assertIn("totally unexpected", str(ctx.exception))

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_connection_refused_raises_connection_error(self, mock_socket_cls):
        """ConnectionRefusedError from connect() raises IAlarmMkConnectionError."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.side_effect = ConnectionRefusedError()
        mock_socket_cls.return_value = mock_sock
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with self.assertRaises(IAlarmMkConnectionError):
            client.login()

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_socket_timeout_raises_connection_error(self, mock_socket_cls):
        """socket.timeout from connect() raises IAlarmMkConnectionError."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.side_effect = socket.timeout()
        mock_socket_cls.return_value = mock_sock
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with self.assertRaises(IAlarmMkConnectionError):
            client.login()

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_bad_credentials_raises_login_error(self, mock_socket_cls):
        """Non-zero Err in panel response raises IAlarmMkLoginError."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.return_value = None
        mock_socket_cls.return_value = mock_sock
        response = {"Root": {"Pair": {"Client": {"Err": 1}}}}
        client = MeianClient("192.168.1.1", 8000, "user", "pass")
        with patch.object(client, "_send"), \
             patch.object(client, "_receive", return_value=response):
            with self.assertRaises(IAlarmMkLoginError):
                client.login()

    @patch("open_ialarm_mk_local_api._internal.meian_client.socket.socket")
    def test_login_enables_keepalive_on_connect(self, mock_socket_cls):
        """SO_KEEPALIVE is set on the socket after a successful connect."""
        mock_sock = _make_mock_sock()
        mock_sock.connect.return_value = None
        mock_socket_cls.return_value = mock_sock
        response = {"Root": {"Pair": {"Client": {"Err": 0}}}}
        client = MeianClient("192.168.1.1", 8000, "user", "pass", keepalive_idle=30)
        with patch.object(client, "_send"), \
             patch.object(client, "_receive", return_value=response):
            client.login()
        calls = [
            args for args, _ in mock_sock.setsockopt.call_args_list
        ]
        self.assertIn((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1), calls)
