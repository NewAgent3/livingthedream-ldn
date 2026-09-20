#!/usr/bin/env python3
"""Exchange Living the Dream Miis and items with a retail console over local wireless.

The console needs nothing installed on it: this joins the game's own LDN session exactly as the
Pokémon tools join theirs, records every Pia datagram, and fixes the game's Pia key the moment one
datagram authenticates. Two things ride the link:

* the game's own exchange traffic, raw - what the first real session captures, and what
  `pokeldn.tomodachi` will be held to;
* the tool's own container channel ("TOMO" frames, `pokeldn.tomodachi.wire`): whole `.ltd` /
  `.ltdf` containers, fragmented, CRC'd and acked, between two machines on the session - which is
  how a library moves between collectors with no console involved.

Everything received lands in a ShareMii-compatible `.ltd` library (`--library`), or as bare files
(`--collect`). The game save is never touched; containers go back into a console the way the
player moves them in game - the exchange screen - and this tool's job is to have them on hand.

    # live: sit in on the exchange screen, record, fix the key, collect what arrives
    sudo -E ./.venv/bin/python bin/tomodachi_exchange.py --capture trace.jsonl --library LIB

    # offer containers to another collector on the session, and receive theirs
    sudo -E ./.venv/bin/python bin/tomodachi_exchange.py --library LIB --send a.ltd --send b.ltdf

    # offline: decode a trace into the library, no radio
    ./.venv/bin/python bin/tomodachi_exchange.py --replay trace.jsonl --library LIB
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUNDLED_LDN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "vendor", "LDN")
if os.path.isdir(BUNDLED_LDN):
    sys.path.insert(0, BUNDLED_LDN)

from pokeldn.tomodachi import library as library_lib
from pokeldn.tomodachi import wire as wire_lib
from pokeldn.tomodachi.library import Library
from pokeldn.tomodachi.ltd import LtdError
from pokeldn.tomodachi.session import TITLE_ID


def _library(args):
    lib = Library(args.library) if args.library else Library(library_lib.DEFAULT_DIR)
    if args.collect:
        lib = Library(args.collect)
    return lib


def _file_payload(path):
    from pokeldn.tomodachi.library import parse_file
    item = parse_file(path)
    return (wire_lib.KIND_MII if isinstance(item, wire_lib.LtdMii) else wire_lib.KIND_UGC,
            item.pack())


def _store(kind, payload, lib, log):
    """One whole container into the library."""
    try:
        if kind == wire_lib.KIND_MII:
            mii = wire_lib.unpack_mii(payload)
            path = lib.add_mii(mii)
            log(f"  mii {mii.display_name or 'Unnamed'} -> {path}")
        else:
            item = wire_lib.unpack_ugc(payload)
            path = lib.add_ugc(item)
            log(f"  {item.type_name.lower()} {item.display_name or item.type_name} -> {path}")
    except (wire_lib.WireError, LtdError) as exc:
        log(f"  ! refused a container: {exc}")


def _exchange_frame(message: bytes, sources: dict, lib, log):
    """One TOMO frame: reassemble data fragments, hand acks to the senders we run.

    -> the ack to put back on the link, or None for an ack (or a refused frame).
    """
    try:
        msg = wire_lib.parse(message)
    except wire_lib.WireError as exc:
        log(f"  ! frame refused: {exc}")
        return None
    if msg["is_ack"]:
        for sender in list(sources.values()):
            sender.on_ack(msg)
        return None
    rag = sources.setdefault(msg["whole_crc"], wire_lib.Reassembler())
    try:
        payload = rag.feed(msg)
    except wire_lib.WireError as exc:
        # No ack: the sender keeps retransmitting, which is the honest answer to a fragment
        # that contradicts the transfer it joined.
        log(f"  ! transfer failed: {exc}")
        sources.pop(msg["whole_crc"], None)
        return None
    ack = wire_lib.ack_for(message)
    if payload is not None:
        label = "ugc" if msg["kind"] == wire_lib.KIND_UGC else "mii"
        log(f"  {label} transfer complete: {len(payload)} bytes")
        _store(msg["kind"], payload, lib, log)
        sources.pop(msg["whole_crc"], None)
    return ack


def run_live(args, lib, log):
    """Join the session, record everything, run the container channel."""
    from pokeldn.ldn import pia6
    from pokeldn.tomodachi.link import Recorder, TomoTransport
    from pokeldn.tomodachi.session import sweep_game_keys

    t = TomoTransport(local_comm_id=int(args.comm_id, 16) if args.comm_id else None,
                      phyname=args.phy, keys_path=args.keys, log=log).start()
    log(f"[live] joined: ssid={t.ssid.hex()} us={t.our_ip} host={t.host_ip} "
        f"(title id {TITLE_ID:016x} is the guess; the advertisement above is the answer)")
    log(f"[live] expected Pia header version {pia6.VERSION}; the first authenticated datagram "
        "settles the game key. Sit the console on the exchange screen.")
    rec = Recorder(args.capture, t.ssid, t.our_ip, log=log) if args.capture else None
    live_keys: list[bytes] = []
    if rec:
        rec.note("session", ssid=t.ssid.hex(), our_ip=t.our_ip, host_ip=t.host_ip)
        rec.advert(t)
        log(f"[live] trace -> {args.capture} (advert + datagrams: everything a sender needs)")
    sources: dict = {}
    senders: list = []
    for path in args.send:
        kind, payload = _file_payload(path)
        senders.append(wire_lib.Sender(kind, payload, now=time.monotonic()))
        log(f"[live] offering {path}")
    stop_at = time.monotonic() + args.seconds
    try:
        while time.monotonic() < stop_at:
            for payload, src in t.recv():
                if rec:
                    rec.datagram(payload, src)
                else:
                    for key in sweep_game_keys(payload, src, t.ssid):
                        if key not in live_keys:
                            live_keys.append(key)
                            log(f"[live] game key found: {key!r} - the session is readable")
                if payload[:4] == wire_lib.MAGIC:
                    ack = _exchange_frame(payload, sources, lib, log)
                    if ack:
                        t.send(ack, src)
            now = time.monotonic()
            for sender in senders:
                for msg in sender.poll(now):
                    t.send(msg, t.broadcast)
            senders = [s for s in senders if not s.done]
            time.sleep(0.05)
    except KeyboardInterrupt:
        log("\n[live] stopping on interrupt")
    finally:
        t.stop()
    if rec:
        log(f"[live] {rec.count} datagram(s) recorded, {rec.authenticated} authenticated; "
            f"keys: {[k.hex() if not k.isascii() else k.decode() for k in rec.keys] or 'none yet'}")
    elif live_keys:
        log(f"[live] keys found: {[k.hex() if not k.isascii() else k.decode() for k in live_keys]} "
            "- run again with --capture to keep a trace")
    log(f"[live] library at {lib.directory}")
    return 0


def run_replay(args, lib, log):
    """Decode a trace offline: decrypt what was not, reassemble, file what arrives."""
    from pokeldn.tomodachi.link import open_trace
    from pokeldn.tomodachi.session import SessionKeys, sweep_game_keys

    records = open_trace(args.replay)
    log(f"[replay] {len(records)} record(s) from {args.replay}")
    ssid = next((bytes.fromhex(r["ssid"]) for r in records if r.get("ssid")), b"")
    keys: list[bytes] = []
    sources: dict = {}
    count = 0
    frames = 0
    for rec in records:
        if not rec.get("hex"):
            continue
        datagram = bytes.fromhex(rec["hex"])
        src = rec.get("src", "")
        plain = None
        if rec.get("plain_hex"):                    # already readable in the trace itself
            plain = bytes.fromhex(rec["plain_hex"])
        else:
            if rec.get("game_key"):
                named = rec["game_key"]
                key = bytes.fromhex(named) if len(named) == 32 else named.encode()
                if key not in keys:
                    keys.append(key)
            for key in keys:
                plain = SessionKeys(ssid, key).decrypt(datagram, src) if src else None
                if plain is not None:
                    break
            if plain is None and src:
                hits = sweep_game_keys(datagram, src, ssid)
                keys.extend(k for k in hits if k not in keys)
                plain = SessionKeys(ssid, hits[0]).decrypt(datagram, src) if hits else None
        if plain is None:
            # Collector-to-collector TOMO frames ride the link unencrypted; the raw datagram
            # is the frame, and the replay must file what a listener on the session saw.
            if datagram[:4] == wire_lib.MAGIC:
                frames += 1
                _exchange_frame(datagram, sources, lib, log)
            continue
        count += 1
        if plain[:4] == wire_lib.MAGIC:
            frames += 1
            _exchange_frame(plain, sources, lib, log)
    log(f"[replay] {count} datagram(s) decrypted under "
        f"{[k.hex() if not k.isascii() else k.decode() for k in keys] or 'no key'}, "
        f"{frames} container frame(s) processed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", help="the .ltd collection to fill (default: the XDG library)")
    ap.add_argument("--collect", help="an output folder: containers land there as bare files "
                                       "(a library without dedup)")
    ap.add_argument("--capture", help="write the protocol trace here as JSONL (live mode)")
    ap.add_argument("--replay", help="decode this JSONL trace offline instead of joining")
    ap.add_argument("--send", action="append", default=[],
                    help="a .ltd / .ltdf file to offer on the container channel (repeatable)")
    ap.add_argument("--comm-id", help="the local communication id a scan printed (16 hex digits)")
    ap.add_argument("--keys", default="~/.switch/prod.keys")
    ap.add_argument("--phy", default="phy0")
    ap.add_argument("--seconds", type=float, default=600.0,
                    help="how long to hold the live session (default: %(default)s)")
    args = ap.parse_args(argv)

    if not any((args.capture, args.replay, args.send, args.library, args.collect)):
        ap.error("give --capture for a live session, --replay for a trace, "
                 "or --send/--library/--collect for the container channel")

    log = print
    lib = _library(args)
    if args.replay:
        return run_replay(args, lib, log)
    if os.geteuid() != 0:
        ap.error("live mode needs the raw radio; re-run with sudo -E")
    return run_live(args, lib, log)


if __name__ == "__main__":
    sys.exit(main())
