"""The link half: joining a Living the Dream session and recording what crosses it.

The join is `ldn.transport.LiveTransport`, the same joiner every game in this repository uses
against a retail console, pointed at this title's advertisement. The one thing it cannot do yet is
filter by the game's own local communication id, because that id is still read off the air:    `bin/tomodachi_scan.py` prints it, `--comm-id` pins it, and until then the joiner falls back to
    the only joinable network in range, which is the common case for this game: the exchange screen
    is the title's only local play, so in a living room the one session on the air is the right one.

On top of the join sits the recorder: every UDP datagram on Pia's port is written to a JSONL trace
raw, then again decrypted under each candidate game key. The tag is the oracle - a key that
authenticates one datagram is the game's key, and everything it sent after that is readable.
"""
from __future__ import annotations

import json
import time

from pokeldn.ldn.transport import LiveTransport, PIA_PORT
from pokeldn.tomodachi.session import PIA_PORT as TOMO_PIA_PORT, PASSPHRASE, SessionKeys, \
    sweep_game_keys

assert PIA_PORT == TOMO_PIA_PORT

TRACE_VERSION = 1


class TomoTransport(LiveTransport):
    """A joiner pointed at a Living the Dream session.

    The passphrase is association-only (see `session.py`); the local communication id is a HINT -
    `--comm-id` from a scan pins it, and a mismatch falls back to the only joinable network.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("password", PASSPHRASE)
        super().__init__(**kwargs)


class Recorder:
    """UDP datagrams in, one JSONL line each: raw, then decrypted under every candidate key.

    A datagram that authenticates under a key names that key for the rest of the trace, which is
    how a first session fixes `session.GAME_KEY_CANDIDATES` down to one.
    """

    def __init__(self, path: str, ssid: bytes, our_ip: str, log=print):
        self.path = path
        self.ssid = bytes(ssid)
        self.our_ip = our_ip
        self.log = log
        self.keys: list[bytes] = []
        self.count = 0
        self.authenticated = 0

    def _write(self, record: dict):
        self.count += 1
        with open(self.path, "a") as fh:
            fh.write(json.dumps(record) + "\n")

    def datagram(self, payload: bytes, src_ip: str):
        record = dict(rec="udp_in", ts=round(time.time(), 3), src=src_ip,
                      hex=bytes(payload).hex())
        for key in self.keys:
            plain = SessionKeys(self.ssid, key).decrypt(payload, src_ip)
            if plain is not None:
                self.authenticated += 1
                record.update(plain_hex=plain.hex(),
                              game_key=key.hex() if not key.isascii() else key.decode())
                break
        else:
            hits = sweep_game_keys(payload, src_ip, self.ssid)
            for key in hits:
                if key not in self.keys:
                    self.keys.append(key)
                    self.log(f"[rec] game key found: {key!r} - the trace is readable from here")
            if hits:
                plain = SessionKeys(self.ssid, hits[0]).decrypt(payload, src_ip)
                self.authenticated += 1
                record.update(plain_hex=plain.hex(),
                              game_key=hits[0].hex() if not hits[0].isascii()
                              else hits[0].decode())
        self._write(record)

    def note(self, event: str, **fields):
        self._write(dict(rec=event, ts=round(time.time(), 3), **fields))

    def advert(self, transport):
        """Record what the joined session's advertisement said: the send path's constants.

        The local communication id and the application data are read off the joined network by
        the transport; the game key, if this trace found one, rides each datagram record. A trace
        that carries the advert and the datagrams carries everything a sender needs but the
        LDN passphrase, which association consumes and no one else sees.
        """
        app_data = bytes(getattr(transport, "app_data", b"") or b"")
        comm_id = getattr(transport, "LOCAL_COMMUNICATION_ID", 0)
        self.note("advert", local_communication_id=f"{comm_id:016x}",
                  app_data_hex=app_data.hex(),
                  app_data_ascii=app_data.decode("ascii", "replace").strip(chr(0)))


def open_trace(path: str) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]
