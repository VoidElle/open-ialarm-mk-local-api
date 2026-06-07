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


# ---------------------------------------------------------------------------
# Wire-protocol type encoders
# Each function serialises a Python value into the Meian tagged-string format
# used inside the XML payload, e.g. _str("hello") -> "STR,5|hello".
# ---------------------------------------------------------------------------

def _bol(en: bool) -> str:
    """Encode a boolean as a Meian BOL field (``BOL|T`` / ``BOL|F``)."""
    if en is True:
        return "BOL|T"
    return "BOL|F"


def _dta(t) -> str:
    """Encode a ``time.struct_time`` as a Meian DTA timestamp field."""
    dta = time.strftime("%Y.%m.%d.%H.%M.%S", t)
    return "DTA,%d|%s" % (len(dta), dta)


def _pwd(text: str) -> str:
    """Encode a password string as a Meian PWD field."""
    return "PWD,%d|%s" % (len(text), text)


def _s32(val: int, pos: int = 0) -> str:
    """Encode an integer as a Meian S32 field.

    *pos* is the position/index hint the panel uses for list operations.
    """
    return "S32,%d,%d|%d" % (pos, pos, val)


def _mac(mac: str) -> str:
    """Encode a MAC address string as a Meian MAC field."""
    return "MAC,%d|%d" % (len(mac), mac)


def _ipa(ip: str) -> str:
    """Encode an IP address string as a Meian IPA field."""
    return "IPA,%d|%d" % (len(ip), ip)


def _str(text: str) -> str:
    """Encode any string as a Meian STR field (length-prefixed)."""
    text = str(text)
    return "STR,%d|%s" % (len(text), text)


def _typ(val: int, typ: list | None = None) -> str:
    """Encode an integer as a Meian TYP field using the provided label list.

    If *val* is out of range the label is ``NONE``.
    Example: ``_typ(0, ["ARM", "DISARM"])`` -> ``"TYP,ARM|0"``
    """
    values = typ or []
    try:
        return "TYP,%s|%d" % (values[val], val)
    except IndexError:
        return "TYP,NONE,|%d" % val


# ---------------------------------------------------------------------------
# XOR codec
# ---------------------------------------------------------------------------

# 128-byte repeating XOR key used for all Meian wire-protocol payloads.
# The key repeats every 128 bytes (index & 0x7F selects the key byte).
_XOR_KEY = bytearray.fromhex(
    "0c384e4e62382d620e384e4e44382d300f382b382b0c5a6234384e304e4c372b"
    "10535a0c20432d171142444e58422c421157322a204036172056446262382b5f"
    "0c384e4e62382d620e385858082e232c0f382b382b0c5a62343830304e2e362b"
    "10545a0c3e432e1711384e625824371c1157324220402c17204c444e624c2e12"
)

# ---------------------------------------------------------------------------
# XML decoder regexes, compiled once at import time for performance.
# Each pattern matches one Meian wire type tag and captures the payload value.
# ---------------------------------------------------------------------------
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
    """Build a nested dict matching the given XPath-style *path*.

    Example: ``_create("/Root/Host/GetZone", {"Total": None})``
    -> ``{"Root": {"Host": {"GetZone": {"Total": None}}}}``
    """
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
    """Traverse *mydict* by the given XPath-style *path* and return the value.

    Returns ``None`` if any intermediate key is missing.
    """
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
    Non-string values (e.g. already-decoded ints) pass through unchanged.
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
    """Recursively populate *parent* from *dictitem*.

    Lists are expanded as repeated sibling elements (same tag).
    ``None`` values produce an empty element (no text node), which is how
    the Meian protocol signals "request this field".
    """
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
    """Convert a nested dict to an lxml Element tree.

    The top-level key of *xmldict* becomes the root element tag.
    """
    root_tag = next(iter(xmldict))
    root = etree.Element(root_tag)
    _convert_dict_to_xml_recurse(root, xmldict[root_tag])
    return root


# ---------------------------------------------------------------------------
# MeianClient - synchronous TCP request/response client
# ---------------------------------------------------------------------------

class MeianClient:
    _seq: int = 0
    _timeout: float = 10.0

    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 10.0,
                 keepalive_idle: int = 60):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._keepalive_idle = keepalive_idle
        self._sock: socket.socket | None = None
        self._token: str | None = None
        logger.debug("MeianClient created for %s:%d (user=%s, timeout=%.1fs)", host, port, username, timeout)

    def _enable_keepalive(self) -> None:
        """Enable TCP keep-alive on the active socket.

        Activates ``SO_KEEPALIVE`` and, where the platform supports it, sets
        the idle time (seconds before the first probe) to ``_keepalive_idle``.
        Interval and probe-count are left at OS defaults.

        Silently skips any option the OS does not expose (e.g. ``TCP_KEEPIDLE``
        is Linux-only; macOS uses ``TCP_KEEPALIVE`` instead).
        """
        if self._sock is None:
            return
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for opt in ("TCP_KEEPIDLE", "TCP_KEEPALIVE"):  # Linux / macOS
            if hasattr(socket, opt):
                self._sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), self._keepalive_idle)
                break
        logger.debug("_enable_keepalive: SO_KEEPALIVE enabled (idle=%ds)", self._keepalive_idle)

    def login(self) -> None:
        """Open a TCP connection and authenticate with the panel.

        Sends a ``/Root/Pair/Client`` command containing the username,
        password and a freshly generated UUID token.  The panel responds
        with the same command; a non-zero ``Err`` field means the
        credentials were rejected.
        """
        logger.debug("login: checking socket state")
        if self._sock is None or self._sock.fileno() == -1:
            logger.debug("login: socket missing or closed, creating new socket")
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        if self.is_socket_connected():
            logger.debug("login: socket already connected, skipping")
            return

        assert self._sock is not None
        self._sock.settimeout(self._timeout)
        logger.debug("login: connecting to %s:%d (timeout=%.1fs)", self._host, self._port, self._timeout)
        try:
            self._sock.connect((self._host, self._port))
            self._enable_keepalive()
            logger.debug("login: TCP connection established")

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

            logger.debug("login: sending Pair/Client (token=%s)", self._token)
            self._send(_create(xpath, cmd))
            client = _select(self._receive(), xpath) or {}

            if client.get("Err"):
                logger.warning("login: panel rejected credentials (Err=%s)", client.get("Err"))
                self.close_socket()
                raise IAlarmMkLoginError("Login error")

            logger.debug("login: authenticated successfully (token=%s)", self._token)

        except IAlarmMkLoginError:
            raise
        except IAlarmMkConnectionError:
            raise
        except socket.timeout as exc:
            logger.error("login: connection timed out after %.1fs", self._timeout)
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: timeout") from exc
        except ConnectionRefusedError as exc:
            logger.error("login: connection refused by %s:%d", self._host, self._port)
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: connection refused") from exc
        except OSError as exc:
            logger.error("login: network error: %s", exc)
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error: network error") from exc
        except Exception as exc:
            logger.error("login: unexpected error: %s", exc)
            self.close_socket()
            raise IAlarmMkConnectionError(f"Connection error: unexpected error during login: {exc}") from exc

    def logout(self) -> None:
        """Close the TCP connection and reset session state."""
        logger.debug("logout: closing connection (token=%s)", self._token)
        self.close_socket()
        logger.debug("logout: done")

    def close_socket(self) -> None:
        """Safely shut down and close the socket, suppressing OS errors."""
        if self._sock is None:
            return
        logger.debug("close_socket: shutting down socket (token=%s)", self._token)
        try:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        finally:
            self._sock = None
            self._token = None
            logger.debug("close_socket: socket closed and token cleared")

    def is_socket_connected(self) -> bool:
        """Return True if the socket has an active peer connection.

        Creates a fresh socket when the existing one is invalid or closed,
        so callers can immediately attempt ``connect()`` on failure.
        """
        if self._sock is None or self._sock.fileno() == -1:
            logger.debug("is_socket_connected: socket invalid, reinitialising")
            self.close_socket()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return False
        try:
            peer = self._sock.getpeername()
            logger.debug("is_socket_connected: connected to %s", peer)
            return True
        except OSError:
            logger.debug("is_socket_connected: getpeername failed, reinitialising socket")
            self.close_socket()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return False

    # ------------------------------------------------------------------
    # High-level panel commands
    # ------------------------------------------------------------------

    def ping(self) -> None:
        """Send a ``%maI`` keepalive ping to the panel.

        This is a raw write with no response — its only purpose is to
        prevent NAT tables and the panel itself from dropping an idle
        connection.  Raises ``IAlarmMkConnectionError`` if the socket
        is gone so callers can trigger a reconnect.
        """
        if self._sock is None:
            raise IAlarmMkConnectionError("Connection error")
        logger.debug("ping: sending %%maI")
        try:
            self._sock.send(b"%maI")
        except OSError as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error") from exc

    def get_alarm_status(self) -> dict:
        """Request the current arm/disarm status from the panel.

        Returns the decoded ``/Root/Host/GetAlarmStatus`` dict;
        the ``DevStatus`` field holds the integer status code.
        """
        logger.debug("get_alarm_status: requesting panel status")
        cmd = OD()
        cmd["DevStatus"] = None
        cmd["Err"] = None
        result = self._("/Root/Host/GetAlarmStatus", cmd)
        logger.debug("get_alarm_status: DevStatus=%s", result.get("DevStatus") if result else None)
        return result

    def get_network_info(self) -> dict:
        """Request network configuration (MAC, Name, IP, gateway, DNS, etc.)."""
        logger.debug("get_network_info: requesting network info")
        cmd = OD()
        cmd["Mac"] = None
        cmd["Name"] = None
        cmd["Ip"] = None
        cmd["Gate"] = None
        cmd["Subnet"] = None
        cmd["Dns1"] = None
        cmd["Dns2"] = None
        cmd["Err"] = None
        result = self._("/Root/Host/GetNet", cmd)
        logger.debug("get_network_info: Name=%s Mac=%s Ip=%s",
                     result.get("Name") if result else None,
                     result.get("Mac") if result else None,
                     result.get("Ip") if result else None)
        return result

    def get_zones(self) -> list:
        """Fetch the complete zone list from the panel (all pages)."""
        logger.debug("get_zones: requesting zone list")
        cmd = OD()
        cmd["Total"] = None
        cmd["Offset"] = _s32(0)
        cmd["Ln"] = None
        cmd["Err"] = None
        result = self._("/Root/Host/GetZone", cmd, is_list=True)
        logger.debug("get_zones: received %d zone entries", len(result))
        return result

    def set_alarm_status(self, status: int) -> dict:
        """Send a SetAlarmStatus command to the panel.

        *status* maps to: 0=ARM, 1=DISARM, 2=STAY, 3=CLEAR, 8=PARTIAL.
        """
        _STATUS_LABELS = {0: "ARM", 1: "DISARM", 2: "STAY", 3: "CLEAR", 8: "PARTIAL"}
        label = _STATUS_LABELS.get(status, str(status))
        logger.debug("set_alarm_status: sending %s (code=%d)", label, status)
        cmd = OD()
        cmd["DevStatus"] = _typ(status, ["ARM", "DISARM", "STAY", "CLEAR", "", "", "", "", "PARTIAL"])
        cmd["Err"] = None
        result = self._("/Root/Host/SetAlarmStatus", cmd)
        logger.debug("set_alarm_status: panel acknowledged %s", label)
        return result

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

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
        logger.debug("_send: seq=%d xml_len=%d frame_len=%d", self._seq, len(xml), len(message))
        self._sock.send(message)

    def _receive(self) -> dict:
        """Read one response frame and return it as a decoded dict.

        The frame header is 16 bytes (``@ieM`` + length + seq + ``0000``);
        bytes [4:8] carry the payload length as a 4-char decimal string.
        The trailer is the 4-byte sequence number repeated at the end.
        Slice ``data[16:-4]`` extracts the XOR-encoded XML payload.

        Uses a two-phase read so that large MK7 login responses (> 1024 bytes)
        are received in full rather than truncated by a single ``recv(1024)``.
        """
        if self._sock is None:
            raise IAlarmMkConnectionError("Connection error")
        try:
            header = b""
            while len(header) < 16:
                chunk = self._sock.recv(16 - len(header))
                if not chunk:
                    raise IAlarmMkConnectionError("Connection closed while reading header")
                header += chunk

            payload_len = int(header[4:8])
            logger.debug("_receive: header=%s declared payload_len=%d", header[0:4], payload_len)

            to_read = payload_len + 4
            body = b""
            while len(body) < to_read:
                chunk = self._sock.recv(min(4096, to_read - len(body)))
                if not chunk:
                    raise IAlarmMkConnectionError("Connection closed while reading payload")
                body += chunk

            data = header + body
        except IAlarmMkConnectionError:
            raise
        except socket.timeout as exc:
            raise IAlarmMkConnectionError("Connection timed out") from exc
        except OSError as exc:
            self.close_socket()
            raise IAlarmMkConnectionError("Connection error") from exc

        logger.debug("_receive: total frame length=%d", len(data))
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
            logger.debug("_: paginating %s offset=%d accumulated=%d", xpath, offset, len(items or []))
            cmd["Offset"] = _s32(offset)
        else:
            logger.debug("_: sending command to %s (is_list=%s)", xpath, is_list)

        root = _create(xpath, cmd)
        self._send(root)
        response = self._receive()
        payload = _select(response, xpath)

        if not is_list:
            if payload and payload.get("Err"):
                logger.error("_: panel returned error for %s: %s", xpath, payload["Err"])
                raise IAlarmMkAlarmError(f"Alarm error: {payload['Err']}")
            logger.debug("_: response received for %s", xpath)
            return payload

        if items is None:
            items = []
        if payload and payload.get("Err"):
            logger.error("_: panel returned error for %s (list): %s", xpath, payload["Err"])
            raise IAlarmMkAlarmError(f"Alarm error: {payload['Err']}")

        total = _select(response, f"{xpath}/Total") or 0
        ln = _select(response, f"{xpath}/Ln") or 0
        logger.debug("_: list page total=%d ln=%d offset=%d", total, ln, offset)
        for index in range(ln):
            items.append(_select(response, f"{xpath}/L{index}"))
        offset += ln
        if total > offset:
            # More pages remain; recurse with updated offset.
            self._(xpath, cmd, is_list=True, offset=offset, items=items)
        else:
            logger.debug("_: list complete, %d items fetched", len(items))
        return items

