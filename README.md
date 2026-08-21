# 🚨 Open iAlarm-MK Local API

> *Asynchronous Python library for iAlarm-MK alarm panels via the local Meian protocol*

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/github/v/release/VoidElle/open-ialarm-mk-local-api?label=version)](https://github.com/VoidElle/open-ialarm-mk-local-api/releases)
[![Tests](https://github.com/VoidElle/open-ialarm-mk-local-api/actions/workflows/tests.yml/badge.svg)](https://github.com/VoidElle/open-ialarm-mk-local-api/actions/workflows/tests.yml)

**[Features](#features) • [Installation](#installation) • [Quick Start](#quick-start) • [Documentation](#documentation) • [Push Events](#real-time-push-events) • [Examples](#examples) • [Testing](#testing)**

---

## Firmware Compatibility 🔧

| Firmware | Status | Notes |
|---|---|---|
| `V1.0.9F_SIA_TCP` (04/09/2025) | ✅ Confirmed compatible | |
| `V1.05F_SIA_TCP` | ❌ Not compatible | Does not expose a port |
| `V1.13F` (12/08/2026) | ❌ Not compatible | Exposes port 6668 open, but does not answer to Meian commands |

> [!TIP]
> General rule: if your firmware exposes port **8000** as open, it should work.

## Features

### Performance
- Built with **asyncio** for non-blocking operations
- Efficient TCP communication over the Meian binary protocol
- TCP keep-alive to prevent idle connection drops

### Reliability
- Framed two-phase read: large MK7 responses (>1024 bytes) always received in full
- Graceful error propagation with full detail preserved
- Auto-close socket on connection failure
- Auto-reconnect: each command retries once on connection drop
- Concurrent-safe: internal `asyncio.Lock` serializes all commands (Home Assistant coordinator safe)
- Application-level keepalive: polls status every 30 s to prevent panel idle timeouts

### Developer Friendly
- Type-safe dataclasses: `AlarmStatusModel`, `ZoneModel`, `NetworkInfoModel`
- Async context manager support
- Comprehensive logging at every step

### Full Control
- Arm Away, Arm Stay, Arm Partial, Disarm, Cancel Alarm
- Read all zones with status (open, bypassed, low battery, signal loss)
- Read network info (name, MAC, IP)

---

## Installation

```bash
pip install open-ialarm-mk-local-api
```

---

## Quick Start

```python
import asyncio
from open_ialarm_mk_local_api import IAlarmMkClient

async def main():
    async with IAlarmMkClient("192.168.1.100", 8000, "admin", "secret") as client:
        status = await client.get_status()
        print(f"Status: {status.status.name}")

        zones = await client.get_zones()
        for zone in zones:
            print(f"  [{zone.index:3d}] {zone.name} - {'OPEN' if zone.is_open else 'ok'}")

asyncio.run(main())
```

---

## Panel Port

| Model | Default TCP port |
|-------|:----------------:|
| iAlarm **MK7** | **8000** |
| iAlarm **MK2** | **18034** |

---

## Documentation

- [Configuration](#configuration)
- [Connection Management](#connection-management)
- [Alarm Control](#alarm-control)
- [Data Models](#data-models)
- [Error Handling](#error-handling)

---

## Configuration

### Constructor Parameters: `IAlarmMkClient`

| Parameter | Type | Default | Description |
|-----------|:----:|:-------:|-------------|
| **`host`** | `str` | *required* | IP address of the panel |
| **`port`** | `int` | *required* | TCP port (8000 for MK7, 18034 for MK2) |
| **`username`** | `str` | *required* | Login username |
| **`password`** | `str` | *required* | Login password |
| `timeout` | `float` | `10.0` | Socket timeout in seconds |
| `keepalive_interval` | `int \| None` | `30` | Seconds between keepalive polls; `None` to disable |

---

## Connection Management

### Connect / Disconnect

```python
await client.connect()
await client.disconnect()
```

### Context Manager (recommended)

```python
async with IAlarmMkClient("192.168.1.100", 8000, "admin", "pass") as client:
    status = await client.get_status()
```

---

## Alarm Control

```python
await client.arm_away()      # Arm all zones
await client.arm_stay()      # Perimeter zones only
await client.arm_partial()   # Partial arm
await client.disarm()        # Disarm
await client.cancel_alarm()  # Cancel active alarm
```

---

## Data Models

### `AlarmStatusModel`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `AlarmStatusEnum` | Current panel status |

### `AlarmStatusEnum`

| Value | Name | Description |
|:-----:|------|-------------|
| `0` | `ARMED_AWAY` | Armed, all zones |
| `1` | `DISARMED` | Disarmed |
| `2` | `ARMED_STAY` | Armed, stay mode |
| `3` | `CANCEL` | Alarm cancelled |
| `4` | `TRIGGERED` | Alarm triggered |
| `5` | `ALARM_ARMING` | Arming in progress |
| `6` | `UNAVAILABLE` | Status unknown |
| `8` | `ARMED_PARTIAL` | Armed, partial |

### `ZoneModel`

| Field / Property | Type | Description |
|------------------|------|-------------|
| `index` | `int` | Zone index |
| `name` | `str` | Zone name |
| `zone_type` | `int` | Zone type code |
| `status` | `ZoneStatusEnum` | Raw status bitmask |
| `is_open` | `bool` | Zone is faulted / open |
| `is_bypassed` | `bool` | Zone is bypassed |
| `low_battery` | `bool` | Low battery detected |
| `signal_loss` | `bool` | Wireless signal lost |

> **Note:** `is_open` reflects the physical open/close state only when **"Check magnets"** (zone monitoring) is enabled for that zone in the iAlarm app. When disabled, the panel does not report fault status and `is_open` will always return `False` regardless of the physical state of the zone.

### `NetworkInfoModel`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Panel device name |
| `mac` | `str` | MAC address |
| `ip` | `str` | IP address |

---

## Error Handling

| Exception | When raised |
|-----------|-------------|
| `IAlarmMkConnectionError` | TCP failure, timeout, malformed panel response, or unexpected error during login |
| `IAlarmMkLoginError` | Panel rejected credentials |
| `IAlarmMkAlarmError` | Panel returned a non-zero error code for a command |

```python
from open_ialarm_mk_local_api import IAlarmMkConnectionError, IAlarmMkLoginError

try:
    async with IAlarmMkClient("192.168.1.100", 8000, "admin", "pass") as client:
        await client.arm_away()
except IAlarmMkLoginError:
    print("Wrong credentials")
except IAlarmMkConnectionError as e:
    print(f"Connection failed: {e}")
```

---

## Real-Time Push Events

For real-time alarm events (triggered, armed, disarmed) use `IAlarmMkPushClient`. It opens a dedicated TCP connection to the panel and invokes a callback for every event received. The connection is re-established automatically if it drops.

```python
import asyncio
from open_ialarm_mk_local_api import IAlarmMkPushClient

def on_event(event: dict):
    print("Panel event:", event)

async def main():
    client = IAlarmMkPushClient("192.168.1.100", 8000, "admin", on_event)
    await client.subscribe()  # blocks until client.cancel() is called

asyncio.run(main())
```

### Constructor Parameters: `IAlarmMkPushClient`

| Parameter | Type | Description |
|-----------|:----:|-------------|
| **`host`** | `str` | IP address of the panel |
| **`port`** | `int` | TCP port (same as `IAlarmMkClient`) |
| **`username`** | `str` | Login username |
| **`on_event`** | `Callable[[dict], None]` | Callback invoked for each push event received |

### Methods & Properties

| Name | Description |
|------|-------------|
| `await subscribe()` | Connect and listen for events. Reconnects automatically. Blocks until `cancel()` is called. |
| `cancel()` | Stop the subscription loop and close the connection. |
| `connected` | `bool` — `True` when the push TCP connection is currently open. |

> **Note:** Use `IAlarmMkPushClient` alongside `IAlarmMkClient` — they can both connect to the panel on the same port simultaneously. The command connection handles polling and control; the push client delivers real-time events reliably without batching delays.

---

## Examples

Run any example directly from the repo root (no install needed):

```bash
python3 examples/get_status.py --host 192.168.1.100 --user admin --password password
python3 examples/arm_away.py   --host 192.168.1.100 --user admin --password password arm-partial
python3 examples/subscribe_events.py --host 192.168.1.100 --user admin
```

### Integration test against a real panel

```bash
python3 examples/integration_test.py --host 192.168.1.100 --user admin --password password
# skip arm/disarm on a live production panel:
python3 examples/integration_test.py --host 192.168.1.100 --user admin --password password --skip-arm
```

---

## Testing

```bash
python -m pytest tests/
```

