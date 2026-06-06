"""subscribe_events.py - Subscribe to live push events from an iAlarm-MK panel.

The panel sends an event whenever the alarm status or a zone changes.
Press Ctrl-C to stop.

Usage:
    python subscribe_events.py --host 192.168.1.100 --user admin
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse
import asyncio
import signal
from datetime import datetime

from open_ialarm_mk_local_api import IAlarmMkPushClient, IAlarmMkConnectionError

# Contact-ID event codes to human-readable descriptions.
_CID_LABELS = {
    "1100": "Personal ambulance",
    "1101": "Emergency",
    "1110": "Fire",
    "1120": "Emergency",
    "1131": "Perimeter",
    "1132": "Burglary",
    "1133": "24-hour zone",
    "1134": "Delay zone",
    "1137": "Dismantled",
    "1301": "AC fault",
    "1302": "Battery failure",
    "1306": "Programming change",
    "1350": "Communication failure",
    "1381": "Detector lost",
    "1384": "Low battery detector",
    "1401": "Disarmed",
    "1406": "Alarm cancelled",
    "1570": "Bypass report",
    "3301": "AC recovery",
    "3302": "Battery recovery",
    "3350": "Communication restored",
    "3381": "Detector loss recovery",
    "3401": "Armed away",
    "3441": "Armed stay",
    "3456": "Armed partial",
    "3570": "Bypass recovery",
}


def on_event(event: dict) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    cid = str(event.get("Cid", ""))
    label = _CID_LABELS.get(cid, f"CID {cid}")
    zone = event.get("ZoneName") or event.get("Zone") or ""
    status = event.get("Status", "")
    err = event.get("Err")

    parts = [f"[{ts}]", label]
    if zone:
        parts.append(f"zone={zone}")
    if status:
        parts.append(f"status={status}")
    if err:
        parts.append(f"err={err}")

    print("  ".join(parts))


async def main(host: str, port: int, user: str) -> None:
    client = IAlarmMkPushClient(host, port, user, on_event)

    loop = asyncio.get_running_loop()
    # Graceful shutdown on Ctrl-C or SIGTERM.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, client.cancel)

    print(f"Subscribed to {host}:{port}. Waiting for events (Ctrl-C to stop)...")
    try:
        await client.subscribe()
    except asyncio.CancelledError:
        pass
    print("Stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream iAlarm-MK push events")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.user))
    except IAlarmMkConnectionError as exc:
        print(f"Connection failed: {exc}")
