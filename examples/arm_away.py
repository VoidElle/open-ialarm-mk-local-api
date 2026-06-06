"""arm_away.py - Arm, disarm or change mode on an iAlarm-MK panel.

Usage:
    python arm_away.py --host 192.168.1.100 --user admin --password secret arm-away
    python arm_away.py --host 192.168.1.100 --user admin --password secret disarm
    python arm_away.py --host 192.168.1.100 --user admin --password secret arm-stay
    python arm_away.py --host 192.168.1.100 --user admin --password secret arm-partial
    python arm_away.py --host 192.168.1.100 --user admin --password secret cancel
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse
import asyncio

from open_ialarm_mk_local_api import (
    IAlarmMkClient,
    IAlarmMkConnectionError,
    IAlarmMkLoginError,
    IAlarmMkAlarmError,
)

_COMMANDS = {
    "arm-away":    ("arm_away",    "Armed away"),
    "disarm":      ("disarm",      "Disarmed"),
    "arm-stay":    ("arm_stay",    "Armed stay"),
    "arm-partial": ("arm_partial", "Armed partial"),
    "cancel":      ("cancel_alarm","Alarm cancelled"),
}


async def main(host: str, port: int, user: str, password: str, command: str) -> None:
    method_name, label = _COMMANDS[command]
    async with IAlarmMkClient(host, port, user, password) as client:
        await getattr(client, method_name)()
        status = await client.get_status()
        print(f"{label}. Panel now reports: {status.status.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Change iAlarm-MK arm mode")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("command", choices=list(_COMMANDS))
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.user, args.password, args.command))
    except IAlarmMkConnectionError as exc:
        print(f"Connection failed: {exc}")
    except IAlarmMkLoginError as exc:
        print(f"Login failed: {exc}")
    except IAlarmMkAlarmError as exc:
        print(f"Panel error: {exc}")
