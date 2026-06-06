"""get_status.py - Print panel status, network info and zone list.

Usage:
    python get_status.py --host 192.168.1.100 --user admin --password secret
    python get_status.py --host 192.168.1.100 --port 18034 --user admin --password secret
"""

import argparse
import asyncio

from open_ialarmmk_local_api import (
    IAlarmMkClient,
    IAlarmMkConnectionError,
    IAlarmMkLoginError,
    ZoneStatusEnum,
)


def _zone_detail(zone) -> str:
    tags = []
    if zone.is_open:
        tags.append("OPEN")
    if zone.is_bypassed:
        tags.append("bypassed")
    if zone.low_battery:
        tags.append("low-battery")
    if zone.signal_loss:
        tags.append("signal-loss")
    if zone.status & ZoneStatusEnum.ALARM:
        tags.append("ALARM")
    return ", ".join(tags) if tags else "ok"


async def main(host: str, port: int, user: str, password: str) -> None:
    async with IAlarmMkClient(host, port, user, password) as client:
        info = await client.get_network_info()
        print(f"Device : {info.name}")
        print(f"MAC    : {info.mac}")
        print(f"IP     : {info.ip}")

        status = await client.get_status()
        print(f"Status : {status.status.name} ({status.status.value})")

        zones = await client.get_zones()
        if not zones:
            print("Zones  : (none reported)")
        else:
            print(f"Zones  : {len(zones)} total")
            for zone in zones:
                print(f"  [{zone.index:>3}] {zone.name:<20}  {_zone_detail(zone)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read iAlarm-MK status")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=18034)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.user, args.password))
    except IAlarmMkConnectionError as exc:
        print(f"Connection failed: {exc}")
    except IAlarmMkLoginError as exc:
        print(f"Login failed: {exc}")
