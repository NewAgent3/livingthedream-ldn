#!/usr/bin/env python3
"""See Living the Dream's local-wireless sessions on a retail Switch, unmodded.

The game advertises its Mii-exchange session over LDN like every Switch title. This tool lists
what is on the air right now and decodes each advertisement's application data, which is where
the exchange's own details (a room name, a slot index, a stage of the handshake) will show up.

    sudo -E ./.venv/bin/python bin/tomodachi_scan.py --seconds 30

Every field is printed raw. The first capture from a real console fixes the constants in
`pokeldn/tomodachi/session.py`; nothing there is trusted further than that.
"""
import argparse
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
BUNDLED_LDN = os.path.join(PROJECT_ROOT, "vendor", "LDN")
if os.path.isdir(BUNDLED_LDN):
    sys.path.insert(0, BUNDLED_LDN)

import trio

import ldn
from pokeldn.ldn.transport import find_ap_phy, list_phys
from pokeldn.host_support import resolve_keys
from pokeldn.tomodachi.session import TITLE_ID

STALE_VIFS = ["ldn", "ldn-mon", "ldn-tap"]
ACCEPT = {ldn.ACCEPT_ALL: "ALL", ldn.ACCEPT_NONE: "NONE",
          ldn.ACCEPT_BLACKLIST: "BLACKLIST", ldn.ACCEPT_WHITELIST: "WHITELIST"}


def _cleanup_stale():
    for name in STALE_VIFS:
        subprocess.run(["iw", "dev", name, "del"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phy", default="auto", help="wifi phy to scan on ('auto' = first AP-capable)")
    ap.add_argument("--keys", default="~/.switch/prod.keys")
    ap.add_argument("--channels", default="1,6,11,36,40,44,48")
    ap.add_argument("--dwell", type=float, default=0.5, help="seconds per channel")
    ap.add_argument("--seconds", type=float, default=0, help="rescan until this many seconds pass")
    args = ap.parse_args()

    if os.geteuid() != 0:
        ap.error("must run as root (monitor mode needs the raw radio); re-run with sudo -E")
    phy = find_ap_phy(log=print) if args.phy == "auto" else args.phy
    if phy is None:
        print(f"[scan] no AP-capable phy found. Present phys: {', '.join(list_phys()) or 'none'}")
        return 1
    keys_path = resolve_keys(args.keys)
    if not os.path.exists(keys_path):
        print(f"[scan] prod.keys not found at {keys_path!r}")
        return 2
    channels = [int(c) for c in args.channels.split(",") if c.strip()]
    _cleanup_stale()
    keys = ldn.load_keys(keys_path)
    print(f"[scan] phy={phy} channels={channels} dwell={args.dwell}s "
          f"(title id {TITLE_ID:016x})")

    import time
    deadline = time.monotonic() + args.seconds if args.seconds else None
    seen = {}

    async def one_pass():
        return await ldn.scan(keys, phyname=phy, channels=channels, dwell_time=args.dwell)

    while True:
        nets = trio.run(one_pass)
        for n in nets:
            key = (n.local_communication_id, n.ssid.hex())
            if key in seen:
                continue
            seen[key] = n
            print(f"\n--- session {len(seen)} ---")
            print(f"  local_communication_id : {n.local_communication_id:016x}"
                  + ("   <-- this title?" if (n.local_communication_id & ~0xFFFFF)
                     == (TITLE_ID & ~0xFFFFF) else ""))
            print(f"  ldn protocol           : {getattr(n, 'protocol', '?')}")
            print(f"  scene_id               : {n.scene_id}")
            print(f"  app_version            : {n.app_version}")
            print(f"  security_mode          : {n.security_mode}")
            print(f"  channel                : {n.channel}   band {getattr(n, 'band', '?')}")
            print(f"  participants           : {n.num_participants}/{n.max_participants}")
            print(f"  ssid                   : {n.ssid.hex()}")
            print(f"  application data       : ({len(n.application_data)} B)")
            print(f"    {n.application_data.hex()}")
            if n.application_data:
                print(f"    ascii guess: "
                      f"{n.application_data.decode('ascii', 'replace').strip(chr(0))!r}")
        if not nets:
            print("[scan] nothing seen yet. Is the console sitting on the exchange screen?")
        if deadline is None or time.monotonic() >= deadline:
            break
        print("[scan] rescanning...")
    print(f"\n[scan] {len(seen)} distinct session(s)")
    print("next: feed the application data and the Pia capture to pokeldn.tomodachi.session;")
    print("the local communication id above is the value session.py needs confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
