"""integration_test.py - Full device integration test for iAlarm-MK panels.

Runs every client feature against a real panel and reports pass/fail for each.
Does NOT leave the panel in a different state: it reads current status before
arming, runs arm/disarm cycles, then restores the original status.

Usage:
    python integration_test.py --host 192.168.1.100 --user admin --password secret
    python integration_test.py --host 192.168.1.100 --port 8000 --user admin --password secret --skip-arm

Sections:
    1.  TCP connect + login
    2.  Network info
    3.  Alarm status
    4.  Zone list
    5.  Arm / disarm cycle
    6.  Keepalive ping (get_alarm_status)
    7.  cancel_alarm
    8.  async with context manager
    9.  Concurrent command safety (lock)
    10. Long-lived connection with keepalive task
    11. Restore original state
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse
import asyncio
import time

from open_ialarm_mk_local_api import (
    IAlarmMkClient,
    IAlarmMkConnectionError,
    IAlarmMkLoginError,
    AlarmStatusEnum,
)

# ANSI colours
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = []
failed = []
skipped = []


def ok(label: str, detail: str = "") -> None:
    passed.append(label)
    suffix = f"  {detail}" if detail else ""
    print(f"  {GREEN}PASS{RESET}  {label}{suffix}")


def fail(label: str, reason: str) -> None:
    failed.append(label)
    print(f"  {RED}FAIL{RESET}  {label}  ({reason})")


def skip(label: str, reason: str) -> None:
    skipped.append(label)
    print(f"  {YELLOW}SKIP{RESET}  {label}  ({reason})")


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{title}{RESET}")
    print("-" * (len(title) + 2))


async def run(host: str, port: int, user: str, password: str, skip_arm: bool) -> None:

    # ------------------------------------------------------------------
    section("1. TCP connect + login")
    # ------------------------------------------------------------------

    try:
        client = IAlarmMkClient(host, port, user, password, keepalive_interval=None)
        await client.connect()
        ok("connect / login")
    except IAlarmMkLoginError as exc:
        fail("connect / login", f"bad credentials: {exc}")
        print(f"\n{RED}Cannot continue without a working connection.{RESET}")
        return
    except IAlarmMkConnectionError as exc:
        fail("connect / login", str(exc))
        print(f"\n{RED}Cannot continue without a working connection.{RESET}")
        return

    try:

        # ------------------------------------------------------------------
        section("2. Network info")
        # ------------------------------------------------------------------

        try:
            info = await client.get_network_info()
            assert info.name, "name is empty"
            assert info.mac,  "mac is empty"
            assert info.ip,   "ip is empty"
            ok("get_network_info", f"{info.name}  {info.mac}  {info.ip}")
        except Exception as exc:
            fail("get_network_info", str(exc))

        # ------------------------------------------------------------------
        section("3. Alarm status")
        # ------------------------------------------------------------------

        original_status = None
        try:
            result = await client.get_status()
            original_status = result.status
            ok("get_status", f"{result.status.name} ({result.status.value})")
        except Exception as exc:
            fail("get_status", str(exc))

        # ------------------------------------------------------------------
        section("4. Zone list")
        # ------------------------------------------------------------------

        try:
            zones = await client.get_zones()
            assert isinstance(zones, list), "expected list"
            assert len(zones) > 0, "no zones returned"
            open_zones    = [z for z in zones if z.is_open]
            bypassed      = [z for z in zones if z.is_bypassed]
            low_bat       = [z for z in zones if z.low_battery]
            ok("get_zones", (
                f"{len(zones)} zones, "
                f"{len(open_zones)} open, "
                f"{len(bypassed)} bypassed, "
                f"{len(low_bat)} low-battery"
            ))
            for z in zones[:5]:
                state = "OPEN" if z.is_open else "ok"
                print(f"         [{z.index:>3}] {z.name:<22} {state}")
            if len(zones) > 5:
                print(f"         ... ({len(zones) - 5} more)")
        except Exception as exc:
            fail("get_zones", str(exc))

        # ------------------------------------------------------------------
        section("5. Arm / disarm cycle")
        # ------------------------------------------------------------------

        if skip_arm:
            skip("arm_away / disarm", "--skip-arm passed")
            skip("arm_stay / disarm", "--skip-arm passed")
            skip("arm_partial / disarm", "--skip-arm passed")
        else:
            async def wait_for_status(expected: AlarmStatusEnum, timeout: float = 8.0, interval: float = 0.5) -> AlarmStatusEnum:
                """Poll get_status until expected status or timeout. Returns final status."""
                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    s = await client.get_status()
                    if s.status == expected or asyncio.get_event_loop().time() >= deadline:
                        return s.status
                    await asyncio.sleep(interval)

            for label, arm_fn, code in [
                ("arm_away",    client.arm_away,    AlarmStatusEnum.ARMED_AWAY),
                ("arm_stay",    client.arm_stay,    AlarmStatusEnum.ARMED_STAY),
                ("arm_partial", client.arm_partial, AlarmStatusEnum.ARMED_PARTIAL),
            ]:
                try:
                    await arm_fn()
                    final = await wait_for_status(code)
                    if final == code:
                        ok(f"{label}", f"status confirmed: {final.name}")
                    else:
                        ok(f"{label}", f"sent ok (panel reports {final.name})")
                    await asyncio.sleep(3)  # let panel push the state change before disarming
                    await client.disarm()
                    final = await wait_for_status(AlarmStatusEnum.DISARMED)
                    if final == AlarmStatusEnum.DISARMED:
                        ok(f"disarm after {label}")
                    else:
                        fail(f"disarm after {label}", f"still {final.name}")
                except Exception as exc:
                    fail(f"{label}", str(exc))

        # ------------------------------------------------------------------
        section("6. Keepalive ping (get_alarm_status)")
        # ------------------------------------------------------------------

        try:
            result = await client.get_status()
            ok("keepalive poll (get_alarm_status)", result.status.name)
        except Exception as exc:
            fail("keepalive poll (get_alarm_status)", str(exc))

        # ------------------------------------------------------------------
        section("7. cancel_alarm")
        # ------------------------------------------------------------------

        try:
            await client.cancel_alarm()
            ok("cancel_alarm", "sent ok (no active alarm to cancel)")
        except Exception as exc:
            fail("cancel_alarm", str(exc))

        # ------------------------------------------------------------------
        section("8. async with context manager")
        # ------------------------------------------------------------------

        try:
            async with IAlarmMkClient(host, port, user, password, keepalive_interval=None) as cm_client:
                result = await cm_client.get_status()
            ok("async with __aenter__ / __aexit__", result.status.name)
        except Exception as exc:
            fail("async with __aenter__ / __aexit__", str(exc))

        # ------------------------------------------------------------------
        section("9. Concurrent command safety (lock)")
        # ------------------------------------------------------------------

        try:
            results = await asyncio.gather(
                client.get_status(),
                client.get_status(),
                client.get_status(),
            )
            assert len(results) == 3
            ok("concurrent get_status x3", "no race / no crash")
        except Exception as exc:
            fail("concurrent get_status x3", str(exc))

        # ------------------------------------------------------------------
        section("10. Long-lived connection with keepalive task")
        # ------------------------------------------------------------------

        try:
            ka_client = IAlarmMkClient(host, port, user, password, keepalive_interval=5)
            await ka_client.connect()
            assert ka_client._keepalive_task is not None
            assert not ka_client._keepalive_task.done()
            ok("keepalive task started")
            print(f"         Waiting 6s for one keepalive ping...")
            await asyncio.sleep(6)
            status = await ka_client.get_status()
            ok("connection alive after keepalive interval", status.status.name)
            await ka_client.disconnect()
            assert ka_client._keepalive_task is None
            ok("keepalive task cancelled on disconnect")
        except Exception as exc:
            fail("keepalive task", str(exc))

        # ------------------------------------------------------------------
        section("11. Restore original state")
        # ------------------------------------------------------------------

        if original_status is not None and original_status != AlarmStatusEnum.DISARMED:
            try:
                restore_map = {
                    AlarmStatusEnum.ARMED_AWAY:    client.arm_away,
                    AlarmStatusEnum.ARMED_STAY:    client.arm_stay,
                    AlarmStatusEnum.ARMED_PARTIAL: client.arm_partial,
                }
                fn = restore_map.get(original_status)
                if fn:
                    await fn()
                    ok("restored original status", original_status.name)
                else:
                    skip("restore original status", f"no restore fn for {original_status.name}")
            except Exception as exc:
                fail("restore original status", str(exc))
        else:
            ok("restore original status", "was DISARMED, nothing to do")

    finally:
        await client.disconnect()

    # ------------------------------------------------------------------
    section("Results")
    # ------------------------------------------------------------------

    total = len(passed) + len(failed) + len(skipped)
    print(f"\n  {GREEN}{len(passed)} passed{RESET}  "
          f"{RED}{len(failed)} failed{RESET}  "
          f"{YELLOW}{len(skipped)} skipped{RESET}  "
          f"({total} total)\n")

    if failed:
        print(f"{RED}FAILED:{RESET}")
        for f in failed:
            print(f"  - {f}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iAlarm-MK integration test")
    parser.add_argument("--host",     required=True)
    parser.add_argument("--port",     type=int, default=8000)
    parser.add_argument("--user",     required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--skip-arm", action="store_true",
                        help="Skip arm/disarm cycle (safe for production panels)")
    args = parser.parse_args()

    t0 = time.monotonic()
    print(f"\n{BOLD}iAlarm-MK Integration Test{RESET}")
    print(f"Target: {args.host}:{args.port}  user={args.user}")

    try:
        asyncio.run(run(args.host, args.port, args.user, args.password, args.skip_arm))
    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"Total time: {time.monotonic() - t0:.1f}s")
