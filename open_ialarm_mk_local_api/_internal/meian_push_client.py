import asyncio
import logging
import random
import threading
import xmltodict
from collections import OrderedDict as OD
from lxml import etree
from typing import Callable

from .meian_client import _convert_dict_to_xml, _create, _select, _str, _xmlread, _xor
from .paths import HOST_ALARM, PAIR_PUSH, PAIR_PUSH_ERR

logger = logging.getLogger(__name__)


class MeianPushProtocol(asyncio.Protocol):
    """asyncio Protocol that handles the Meian push event stream.

    After the TCP connection is established the client sends a
    ``/Root/Pair/Push`` subscription frame.  The panel then pushes alarm
    events as they occur.  A keepalive heartbeat (``%maI``) is exchanged
    every 60 seconds to keep the connection alive.
    """

    _KEEPALIVE_INTERVAL = 60

    def __init__(self, uid: str, handler: Callable[[dict], None], on_con_lost: asyncio.Future):
        if not callable(handler):
            raise AttributeError("handler is not a function")
        self._handler = handler
        self._on_con_lost = on_con_lost
        self._transport: asyncio.Transport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._timer: threading.Timer | None = None
        self._buffer = bytearray()
        # Pre-build the subscription XML so it is ready the moment the
        # connection is established.
        cmd = OD()
        cmd["Id"] = _str(uid)
        cmd["Err"] = None
        self._message = _create(PAIR_PUSH, cmd)
        logger.debug("MeianPushProtocol created for uid=%s", uid)

    def connection_made(self, transport) -> None:
        """Called by asyncio when the TCP connection is established."""
        self._transport = transport
        self._loop = asyncio.get_event_loop()
        peer = transport.get_extra_info("peername")
        logger.debug("connection_made: connected to %s", peer)
        self.handle_write()
        self._schedule_keepalive()

    def data_received(self, data: bytes) -> None:
        """Buffer TCP bytes and process every complete protocol frame.

        TCP is a byte stream: one callback may contain a fragment of a frame
        or several frames concatenated together. Keep incomplete bytes until a
        complete frame can be extracted.
        """
        if isinstance(data, str):
            data = data.encode()
        if not data:
            return

        self._buffer.extend(data)
        logger.debug(
            "data_received: buffered %d bytes (received=%d)",
            len(self._buffer),
            len(data),
        )

        while self._buffer:
            if len(self._buffer) < 4:
                return

            head = bytes(self._buffer[:4])

            if head == b"%maI":
                del self._buffer[:4]
                logger.debug("data_received: keepalive acknowledged by panel")
                self._schedule_keepalive()
                continue

            if head not in (b"@ieM", b"@alA", b"!lmX"):
                logger.warning(
                    "data_received: unrecognised frame header %s, closing",
                    head,
                )
                self._buffer.clear()
                self._close()
                return

            if len(self._buffer) < 16:
                return

            try:
                payload_len = int(bytes(self._buffer[4:8]))
            except ValueError:
                logger.warning(
                    "data_received: invalid payload length in %s frame",
                    head,
                )
                self._buffer.clear()
                self._close()
                return

            frame_len = 16 + payload_len + 4
            if len(self._buffer) < frame_len:
                return

            frame = bytes(self._buffer[:frame_len])
            del self._buffer[:frame_len]
            self._handle_frame(frame)

            if self._on_con_lost.done():
                self._buffer.clear()
                return

    def _handle_frame(self, data: bytes) -> None:
        """Decode and dispatch one complete non-keepalive frame."""
        head = data[0:4]
        logger.debug("_handle_frame: %d bytes, header=%s", len(data), head)

        if head == b"@ieM":
            # Either a pairing acknowledgement or an alarm event embedded in a
            # pairing-style frame (some firmware versions do this).
            try:
                resp = xmltodict.parse(
                    _xor(data[16:-4]).decode(),
                    xml_attribs=False,
                    dict_constructor=dict,
                    postprocessor=_xmlread,
                )
            except Exception as exc:
                logger.warning("_handle_frame: failed to parse @ieM frame: %s", exc)
                self._close()
                return
            push = _select(resp, PAIR_PUSH)
            if push:
                err = _select(resp, PAIR_PUSH_ERR)
                if err:
                    logger.error("_handle_frame: push subscription rejected (Err=%s)", err)
                    self._close()
                    return
                logger.debug("_handle_frame: push subscription confirmed")
                return
            event = _select(resp, HOST_ALARM)
            logger.debug("_handle_frame: alarm event via @ieM frame: %s", event)
            self._handler(event)
            return

        if head == b"@alA":
            try:
                resp = xmltodict.parse(
                    _xor(data[16:-4]).decode(),
                    xml_attribs=False,
                    dict_constructor=dict,
                    postprocessor=_xmlread,
                )
            except Exception as exc:
                logger.warning("_handle_frame: failed to parse @alA frame: %s", exc)
                return
            event = _select(resp, HOST_ALARM)
            logger.debug("_handle_frame: alarm event via @alA frame: %s", event)
            self._handler(event)
            return

        if head == b"!lmX":
            try:
                resp = xmltodict.parse(
                    data[16:-4],
                    xml_attribs=False,
                    dict_constructor=dict,
                    postprocessor=_xmlread,
                )
            except Exception as exc:
                logger.warning("_handle_frame: failed to parse !lmX frame: %s", exc)
                return
            event = _select(resp, HOST_ALARM)
            logger.debug("_handle_frame: alarm event via !lmX frame: %s", event)
            self._handler(event)
            return

    def connection_lost(self, exc) -> None:
        """Called by asyncio when the connection is closed or dropped."""
        if exc:
            logger.warning("connection_lost: %s", exc)
        else:
            logger.debug("connection_lost: connection closed cleanly")
        self._close()

    def handle_write(self) -> None:
        """Send the initial ``/Root/Pair/Push`` subscription frame (seq 0)."""
        if self._message is None or self._transport is None:
            return
        xml = etree.tostring(_convert_dict_to_xml(self._message), pretty_print=False)
        message = b"@ieM%04d%04d0000%s%04d" % (len(xml), 0, _xor(xml), 0)
        logger.debug("handle_write: sending Pair/Push subscription (%d bytes)", len(message))
        self._transport.write(message)
        self._message = None

    def _schedule_keepalive(self) -> None:
        """Cancel any pending keepalive timer and schedule a fresh one."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._KEEPALIVE_INTERVAL, self._keepalive)
        self._timer.name = f"meian-push-{random.randint(100, 999)}"
        self._timer.daemon = True
        self._timer.start()
        logger.debug("_schedule_keepalive: next keepalive in %ds (timer=%s)",
                     self._KEEPALIVE_INTERVAL, self._timer.name)

    def _keepalive(self) -> None:
        """Send a ``%maI`` keepalive ping to the panel."""
        if self._transport is None or self._transport.is_closing() or self._loop is None:
            logger.debug("_keepalive: transport gone, skipping")
            return
        logger.debug("_keepalive: sending %%maI ping")
        self._loop.call_soon_threadsafe(self._transport.write, b"%maI")

    def _close(self) -> None:
        """Cancel the keepalive timer and tear down the transport."""
        logger.debug("_close: cleaning up push protocol")
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._transport is not None and not self._transport.is_closing():
            self._transport.close()
        if not self._on_con_lost.done():
            self._on_con_lost.set_result(True)
        logger.debug("_close: done")

