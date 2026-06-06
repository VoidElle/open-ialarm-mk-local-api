import asyncio
import logging
import random
import threading
from collections import OrderedDict as OD
from typing import Callable

import xmltodict
from lxml import etree

from ..exceptions.alarm_error import IAlarmMkAlarmError
from .meian_client import _convert_dict_to_xml, _create, _select, _str, _xmlread, _xor

logger = logging.getLogger(__name__)


class MeianPushProtocol(asyncio.Protocol):
    _KEEPALIVE_INTERVAL = 60

    def __init__(self, uid: str, handler: Callable[[dict], None], on_con_lost: asyncio.Future):
        if not callable(handler):
            raise AttributeError("handler is not a function")
        self._handler = handler
        self._on_con_lost = on_con_lost
        self._transport: asyncio.Transport | None = None
        self._timer: threading.Timer | None = None
        cmd = OD()
        cmd["Id"] = _str(uid)
        cmd["Err"] = None
        self._message = _create("/Root/Pair/Push", cmd)

    def connection_made(self, transport) -> None:
        self._transport = transport
        self.handle_write()
        self._schedule_keepalive()

    def data_received(self, data: bytes) -> None:
        """Route incoming frames by their 4-byte magic header.

        ``%maI`` — keepalive ping from the panel; reschedule our keepalive timer.
        ``@ieM`` — pairing response or alarm event (depends on XPath content).
        ``@alA`` — alarm event (XOR-encoded XML).
        ``!lmX`` — alternate alarm event (plain XML, no XOR).
        """
        if isinstance(data, str):
            data = data.encode()
        head = data[0:4]

        if head == b"%maI":
            self._schedule_keepalive()
            return

        if head == b"@ieM":
            resp = xmltodict.parse(
                _xor(data[16:-4]).decode(),
                xml_attribs=False,
                dict_constructor=dict,
                postprocessor=_xmlread,
            )
            push = _select(resp, "/Root/Pair/Push")
            if push:
                err = _select(resp, "/Root/Pair/Push/Err")
                if err:
                    self._close()
                    raise IAlarmMkAlarmError("Push subscription error")
                return
            self._handler(_select(resp, "/Root/Host/Alarm"))
            return

        if head == b"@alA":
            resp = xmltodict.parse(
                _xor(data[16:-4]).decode(),
                xml_attribs=False,
                dict_constructor=dict,
                postprocessor=_xmlread,
            )
            self._handler(_select(resp, "/Root/Host/Alarm"))
            return

        if head == b"!lmX":
            resp = xmltodict.parse(
                data[16:-4],
                xml_attribs=False,
                dict_constructor=dict,
                postprocessor=_xmlread,
            )
            self._handler(_select(resp, "/Root/Host/Alarm"))
            return

        self._close()
        raise IAlarmMkAlarmError("Response error")

    def connection_lost(self, exc) -> None:
        self._close()

    def handle_write(self) -> None:
        """Send the initial ``/Root/Pair/Push`` subscription frame (seq 0)."""
        if self._message is None or self._transport is None:
            return
        xml = etree.tostring(_convert_dict_to_xml(self._message), pretty_print=False)
        message = b"@ieM%04d%04d0000%s%04d" % (len(xml), 0, _xor(xml), 0)
        self._transport.write(message)
        self._message = None

    def _schedule_keepalive(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._KEEPALIVE_INTERVAL, self._keepalive)
        self._timer.name = f"meian-push-{random.randint(100, 999)}"
        self._timer.daemon = True
        self._timer.start()

    def _keepalive(self) -> None:
        if self._transport is None or self._transport.is_closing():
            return
        self._transport.write(b"%maI")

    def _close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._transport is not None and not self._transport.is_closing():
            self._transport.close()
        if not self._on_con_lost.done():
            self._on_con_lost.set_result(True)
