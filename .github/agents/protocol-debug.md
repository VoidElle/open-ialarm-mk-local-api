---
name: Meian Protocol Debug
description: Diagnoses iAlarm-MK connection and protocol issues. Covers TCP framing, XOR encoding, XML parsing, and panel error codes.
---

## Architecture

```
IAlarmMkClient (async)
  └── MeianClient (sync TCP)
        ├── login()         → /Root/Pair/Client
        ├── _send()         → XOR-encode XML, write frame
        ├── _receive()      → read header (16B) + payload, XOR-decode, parse XML
        └── _()             → send + receive, optional pagination
```

## Wire Frame Format

```
Send:    @ieM {len:04d} {seq:04d} 0000 {XOR(xml)} {seq:04d}
Receive: @ieM {len:04d} {seq:04d} 0000 {XOR(xml)} {seq:04d}
         ^^^^  ^^^^     ^^^^            ^^^^^^^^^^^  ^^^^
         tag   plen     seq             payload      seq
```

- Header: exactly 16 bytes
- `header[4:8]` = payload length as 4-char decimal string
- Payload: `payload_len` bytes of XOR-encoded XML
- Footer: 4-byte sequence number
- Total: `16 + payload_len + 4` bytes

## XOR Key

128-byte repeating key. Index: `i & 0x7F`. Both sides use the same key XOR is its own inverse.

## Common Issues

### Login fails with `IAlarmMkConnectionError`

1. Wrong port MK7 uses **8000**, MK2 uses **18034**
2. Panel not reachable verify with `ping` and `nc -zv <host> <port>`
3. Credentials wrong panel returns `Err != 0` in `/Root/Pair/Client` response → `IAlarmMkLoginError`

### XML truncated / `ExpatError`

Old single `recv(1024)` bug already fixed. `_receive()` now reads the full declared payload length from the header. If this appears again, check `payload_len = int(header[4:8])` is parsed correctly.

### Response field is `None`

Panel omits or sends null for a field. All callers use `field or default` pattern not `.get("Key", default)` to handle this.

### `IAlarmMkAlarmError: Alarm error: N`

Panel returned non-zero `Err` in command response. Common codes:
- `1` wrong credentials (login)
- `2` not authorized
- `10` command not supported by this firmware

### Keep-alive / idle disconnection

`SO_KEEPALIVE` is enabled after `connect()` with a configurable idle time (`keepalive_idle`, default 60s). If connections still drop, reduce `keepalive_idle` or implement reconnect logic around `IAlarmMkConnectionError`.

## Debugging Checklist

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

This exposes every `_send` / `_receive` frame length and sequence number.

## Adding Logging to a New Command

Every method should log at `DEBUG` level at entry and on result:

```python
logger.debug("my_command: sending request")
result = self._("/Root/Host/MyCommand", cmd)
logger.debug("my_command: result=%s", result)
return result
```
