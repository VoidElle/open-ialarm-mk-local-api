"""Tests for the low-level Meian wire-protocol helpers in _internal/."""

import unittest

from open_ialarmmk_local_api._internal.meian_client import (
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
