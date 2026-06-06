import logging
import re
import socket
import time
import uuid
from collections import OrderedDict as OD

from lxml import etree
import xmltodict

from ..exceptions.alarm_error import IAlarmMkAlarmError
from ..exceptions.connection_error import IAlarmMkConnectionError
from ..exceptions.login_error import IAlarmMkLoginError

logger = logging.getLogger(__name__)


def _bol(en: bool) -> str:
    if en is True:
        return "BOL|T"
    return "BOL|F"


def _dta(t) -> str:
    dta = time.strftime("%Y.%m.%d.%H.%M.%S", t)
    return "DTA,%d|%s" % (len(dta), dta)


def _pwd(text: str) -> str:
    return "PWD,%d|%s" % (len(text), text)


def _s32(val: int, pos: int = 0) -> str:
    return "S32,%d,%d|%d" % (pos, pos, val)


def _mac(mac: str) -> str:
    return "MAC,%d|%d" % (len(mac), mac)


def _ipa(ip: str) -> str:
    return "IPA,%d|%d" % (len(ip), ip)


def _str(text: str) -> str:
    text = str(text)
    return "STR,%d|%s" % (len(text), text)


def _typ(val: int, typ: list | None = None) -> str:
    values = typ or []
    try:
        return "TYP,%s|%d" % (values[val], val)
    except IndexError:
        return "TYP,NONE,|%d" % val


# 128-byte repeating XOR key used for all Meian wire-protocol payloads.
_XOR_KEY = bytearray.fromhex(
    "0c384e4e62382d620e384e4e44382d300f382b382b0c5a6234384e304e4c372b"
    "10535a0c20432d171142444e58422c421157322a204036172056446262382b5f"
    "0c384e4e62382d620e385858082e232c0f382b382b0c5a62343830304e2e362b"
    "10545a0c3e432e1711384e625824371c1157324220402c17204c444e624c2e12"
)

_BOL_RE = re.compile(r"BOL\|([FT])")
_DTA_RE = re.compile(r"DTA(,\d+)*\|(\d{4}\.\d{2}.\d{2}.\d{2}.\d{2}.\d{2})")
_ERR_RE = re.compile(r"ERR\|(\d{2})")
_GBA_RE = re.compile(r"GBA,(\d+)\|([0-9A-F]*)")
_HMA_RE = re.compile(r"HMA,(\d+)\|(\d{2}:\d{2})")
_IPA_RE = re.compile(r"IPA,(\d+)\|(([0-2]?\d{0,2}\.){3}([0-2]?\d{0,2}))")
_MAC_RE = re.compile(r"MAC,(\d+)\|(([0-9A-F]{2}[:-]){5}([0-9A-F]{2}))")
_NEA_RE = re.compile(r"NEA,(\d+)\|([0-9A-F]+)")
_NUM_RE = re.compile(r"NUM,(\d+),(\d+)\|(\d*)")
_PWD_RE = re.compile(r"PWD,(\d+)\|(.*)")
_S32_RE = re.compile(r"S32,(\d+),(\d+)\|(\d*)")
_STR_RE = re.compile(r"STR,(\d+)\|(.*)")
_TYP_RE = re.compile(r"TYP,(\w+)\|(\d+)")


def _xor(data: bytes | bytearray) -> bytearray:
    """XOR every byte with the repeating 128-byte key (index mod 128)."""
    buf = bytearray(data)
    for index in range(len(buf)):
        key_index = index & 0x7F
        buf[index] ^= _XOR_KEY[key_index]
    return buf


def _create(path: str, mydict: dict | None = None) -> dict:
    root: dict = {}
    elem = root
    payload = mydict or {}
    try:
        parts = path.strip("/").split("/")
        last = len(parts) - 1
        for index, part in enumerate(parts):
            elem[part] = {}
            if index == last:
                elem[part] = payload
            elem = elem.get(part)
    except Exception:
        pass
    return root


def _select(mydict: dict | list | None, path: str):
    elem = mydict
    try:
        for part in path.strip("/").split("/"):
            try:
                part = int(part)
                elem = elem[part]
            except ValueError:
                elem = elem.get(part)
    except Exception:
        pass
    return elem


def _xmlread(path, key, value):
    """xmltodict postprocessor: decode Meian typed values into Python natives.

    Wire values carry an explicit type tag, e.g. ``STR,5|hello``,
    ``S32,0,0|42``, ``BOL|T``.  Each branch strips the tag and returns
    the natural Python type so callers never see raw Meian strings.
    """
    try:
        input_value = value
        if _BOL_RE.match(input_value):
            bol = _BOL_RE.search(input_value).groups()[0]
            if bol == "T":
                value = True
            if bol == "F":
                value = False
        elif _DTA_RE.match(input_value):
            dta = _DTA_RE.search(input_value).groups()[1]
            value = time.strptime(dta, "%Y.%m.%d.%H.%M.%S")
        elif _ERR_RE.match(input_value):
            value = int(_ERR_RE.search(input_value).groups()[0])
        elif _GBA_RE.match(input_value):
            value = bytearray.fromhex(_GBA_RE.search(input_value).groups()[1]).decode()
        elif _HMA_RE.match(input_value):
            hma = _HMA_RE.search(input_value).groups()[1]
            value = time.strptime(hma, "%H:%M")
        elif _IPA_RE.match(input_value):
            value = str(_IPA_RE.search(input_value).groups()[1])
        elif _MAC_RE.match(input_value):
            value = str(_MAC_RE.search(input_value).groups()[1])
        elif _NEA_RE.match(input_value):
            value = str(_NEA_RE.search(input_value).groups()[1])
        elif _NUM_RE.match(input_value):
            value = str(_NUM_RE.search(input_value).groups()[2])
        elif _PWD_RE.match(input_value):
            value = str(_PWD_RE.search(input_value).groups()[1])
        elif _S32_RE.match(input_value):
            value = int(_S32_RE.search(input_value).groups()[2])
        elif _STR_RE.match(input_value):
            value = str(_STR_RE.search(input_value).groups()[1])
        elif _TYP_RE.match(input_value):
            value = int(_TYP_RE.search(input_value).groups()[1])
        else:
            raise IAlarmMkAlarmError(f"Unknown data type {input_value!r}")
        return key, value
    except (TypeError, ValueError):
        return key, value


def _convert_dict_to_xml_recurse(parent: etree.Element, dictitem: dict) -> None:
    assert not isinstance(dictitem, list)

    if isinstance(dictitem, dict):
        for tag, child in dictitem.items():
            if isinstance(child, list):
                for list_child in child:
                    elem = etree.Element(tag)
                    parent.append(elem)
                    _convert_dict_to_xml_recurse(elem, list_child)
            else:
                elem = etree.Element(tag)
                parent.append(elem)
                _convert_dict_to_xml_recurse(elem, child)
    elif dictitem is not None:
        parent.text = str(dictitem)


def _convert_dict_to_xml(xmldict: dict) -> etree.Element:
    root_tag = next(iter(xmldict))
    root = etree.Element(root_tag)
    _convert_dict_to_xml_recurse(root, xmldict[root_tag])
    return root


class MeianClient:
    _seq: int = 0
    _timeout: float = 10.0

    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 10.0):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._token: str | None = None

    def login(self) -> None:
        if self._sock is None or self._sock.fileno() == -1:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        if self.is_socket_connected():
            return

        assert self._sock is not None
        self._sock.settimeout(self._timeout)
        try:
            self._sock.connect((self._host, self._port))
            cmd = OD()
            cmd["Id"] = _str(self._username)
            cmd["Pwd"] = _pwd(self._password)
            cmd["Type"] = "TYP,ANDROID|0"
            self._token = str(uuid.uuid4())
            cmd["Token"] = _str(self._token)
            cmd["Action"] = "TYP,IN|0"
            cmd["PemNum"] = "STR,5|26"
            cmd["DevVersion"] = None
            cmd["DevType"] = None
            cmd["Err"] = None
            xpath = "/Root/Pair/Client"
            self._send(_create(xpath, cmd))
            client = _select(self._receive(), xpath) or {}
            if client.get("Err"):
                self.close_socket()
                raise IAlarmMkLoginError("Login error")
        except IAlarmMkLoginError:
            raise
        except socket.timeout as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: timeout") from exc
        except ConnectionRefusedError as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: connection refused") from exc
        except OSError as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: network error") from exc

    def logout(self) -> None:
        self.close_socket()

    def close_socket(self) -> None:
        if self._sock is None:
            return
        try:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        finally:
            self._sock = None
            self._token = None

    def is_socket_connected(self) -> bool:
        if self._sock is None or self._sock.fileno() == -1:
            self.close_socket()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return False
        try:
            self._sock.getpeername()
        except OSError:
            self.close_socket()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return False
        return True

    def get_alarm_status(self) -> dict:
        cmd = OD()
        cmd["DevStatus"] = None
        cmd["Err"] = None
        return self._("/Root/Host/GetAlarmStatus", cmd)

    def get_network_info(self) -> dict:
        cmd = OD()
        cmd["Mac"] = None
        cmd["Name"] = None
        cmd["Ip"] = None
        cmd["Gate"] = None
        cmd["Subnet"] = None
        cmd["Dns1"] = None
        cmd["Dns2"] = None
        cmd["Err"] = None
        return self._("/Root/Host/GetNet", cmd)

    def get_zones(self) -> list:
        cmd = OD()
        cmd["Total"] = None
        cmd["Offset"] = _s32(0)
        cmd["Ln"] = None
        cmd["Err"] = None
        return self._("/Root/Host/GetZone", cmd, is_list=True)

    def set_alarm_status(self, status: int) -> dict:
        cmd = OD()
        cmd["DevStatus"] = _typ(status, ["ARM", "DISARM", "STAY", "CLEAR", "", "", "", "", "PARTIAL"])
        cmd["Err"] = None
        return self._("/Root/Host/SetAlarmStatus", cmd)

    def _send(self, root: dict) -> None:
        """Serialize *root* to XOR-encoded XML and write it to the socket.

        Frame layout: ``@ieM<len:4><seq:4>0000<xor_payload><seq:4>``
        All length/sequence fields are zero-padded decimal ASCII.
        """
        if self._sock is None:
            raise IAlarmMkConnectionError("Connection error")
        xml = etree.tostring(_convert_dict_to_xml(root), pretty_print=False)
        self._seq += 1
        message = b"@ieM%04d%04d0000%s%04d" % (len(xml), self._seq, _xor(xml), self._seq)
        self._sock.send(message)

    def _receive(self) -> dict:
        """Read one response frame and return it as a decoded dict.

        The frame header is 16 bytes (``@ieM`` + length + seq + ``0000``);
        the trailer is the 4-byte sequence repeated at the end.
        Slice ``data[16:-4]`` extracts the XOR-encoded XML payload.
        """
        if self._sock is None:
            raise IAlarmMkConnectionError("Connection error")
        try:
            data = self._sock.recv(1024)
        except socket.timeout as exc:
            raise IAlarmMkConnectionError("Connection timed out") from exc
        except OSError as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error") from exc

        return xmltodict.parse(
            _xor(data[16:-4]).decode(),
            xml_attribs=False,
            dict_constructor=dict,
            postprocessor=_xmlread,
        )

    def _(self, xpath: str, cmd: OD, is_list: bool = False, offset: int = 0, items: list | None = None):
        """Send *cmd* to *xpath* and return the decoded response.

        When *is_list* is True the panel paginates results via
        ``Total`` / ``Offset`` / ``Ln`` / ``L0``…``Ln`` fields.
        This method recurses until all pages are fetched and returns
        a flat list.
        """
        if offset > 0:
            cmd["Offset"] = _s32(offset)
        root = _create(xpath, cmd)
        self._send(root)
        response = self._receive()
        payload = _select(response, xpath)
        if not is_list:
            if payload and payload.get("Err"):
                raise IAlarmMkAlarmError(f"Alarm error: {payload['Err']}")
            return payload

        if items is None:
            items = []
        if payload and payload.get("Err"):
            raise IAlarmMkAlarmError(f"Alarm error: {payload['Err']}")

        total = _select(response, f"{xpath}/Total") or 0
        ln = _select(response, f"{xpath}/Ln") or 0
        for index in range(ln):
            items.append(_select(response, f"{xpath}/L{index}"))
        offset += ln
        if total > offset:
            self._(xpath, cmd, is_list=True, offset=offset, items=items)
        return items
