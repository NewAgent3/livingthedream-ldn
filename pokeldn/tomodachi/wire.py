"""The byte format that moves Miis and items over the link.

The game's own local-wireless exchange is a two-way file push: each side names a slot, sends the
full container, the other side stores it into its save exactly as ShareMii would. The exchange is
therefore a plain framed container, with no negotiation beyond the game's Pia session underneath.

Every payload is a message:

    0x00  4  magic b"TOMO"
    0x04  1  format version (1)
    0x05  1  kind: 0x01 Mii (.ltd), 0x02 UGC (.ltdf family)
    0x06  2  payload length, big-endian
    0x08  4  fragment index, big-endian
    0x0C  4  fragment count, big-endian
    0x10  4  CRC32 of the WHOLE payload, big-endian (every fragment carries it)
    0x14  4  CRC32 of this fragment's bytes, big-endian
    0x18     fragment bytes

Receivers reply with an ack message, same header, kind | 0x80, body = the two CRCs of the message
being acknowledged, whole then fragment. The sender's window does not advance on silence: it
retransmits, and the receiver drops a duplicate by CRC. A mismatched whole-CRC names a truncated
transfer; nothing is written to a save until the whole payload checks.

Containers ride whole and uncompressed when they fit one fragment. The chunk size follows
`broadcast4.CHUNK_SIZE` so the framing behaves like the layer above it on this radio.
"""
from __future__ import annotations

import struct
import zlib

from .ltd import LtdMii, LtdUGC

MAGIC = b"TOMO"
FORMAT_VERSION = 1

KIND_MII = 0x01
KIND_UGC = 0x02
KIND_ACK = 0x80                 # ORed into the kind of the message being acknowledged

KIND_NAMES = {KIND_MII: "mii", KIND_UGC: "ugc"}

HEADER_SIZE = 0x18
FRAGMENT_SIZE = 1404            # broadcast4.CHUNK_SIZE; a sender's choice, kept for symmetry


class WireError(ValueError):
    """A payload refused at the framing layer rather than misread."""


def chunk(data: bytes, size: int = FRAGMENT_SIZE) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)] or [b""]


def build(kind: int, payload: bytes, fragment_size: int = FRAGMENT_SIZE) -> list[bytes]:
    """-> the messages carrying one payload, in order."""
    if kind not in (KIND_MII, KIND_UGC):
        raise WireError(f"kind must be {KIND_MII:#x} or {KIND_UGC:#x}, got {kind:#x}")
    whole_crc = zlib.crc32(payload) & 0xFFFFFFFF
    parts = chunk(payload, fragment_size)
    out = []
    for index, part in enumerate(parts):
        header = (MAGIC + bytes([FORMAT_VERSION, kind])
                  + struct.pack(">HIII", len(part), index, len(parts), whole_crc)
                  + struct.pack(">I", zlib.crc32(part) & 0xFFFFFFFF))
        out.append(header + part)
    return out


def build_ack(kind: int, whole_crc: int, fragment_crc: int) -> bytes:
    body = struct.pack(">II", whole_crc, fragment_crc)
    return (MAGIC + bytes([FORMAT_VERSION, kind | KIND_ACK])
            + struct.pack(">HIII", len(body), 0, 1, whole_crc)
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF) + body)


def parse(message: bytes) -> dict:
    """-> one message as fields. Raises WireError on anything malformed.

    `whole_crc` and `fragment_crc` are checked before the caller sees the payload, so a corrupted
    fragment never reaches a save.
    """
    message = bytes(message)
    if len(message) < HEADER_SIZE:
        raise WireError(f"a message is at least {HEADER_SIZE} bytes, got {len(message)}")
    if message[:4] != MAGIC:
        raise WireError(f"not a Mii-exchange message: magic {message[:4]!r}")
    version, kind = message[4], message[5]
    if version != FORMAT_VERSION:
        raise WireError(f"format version {version} is not {FORMAT_VERSION}")
    size, index, count, whole_crc = struct.unpack_from(">HIII", message, 6)
    fragment_crc = struct.unpack_from(">I", message, 0x14)[0]
    body = message[HEADER_SIZE:]
    if len(body) != size:
        raise WireError(f"the header says {size} bytes, the message carries {len(body)}")
    if kind & KIND_ACK:
        if len(body) != 8:
            raise WireError("an ack body is eight bytes")
        return dict(is_ack=True, kind=kind & ~KIND_ACK, whole_crc=whole_crc,
                    fragment_crc=struct.unpack(">I", body[:4])[0],
                    acked_fragment_crc=struct.unpack(">I", body[4:])[0])
    if zlib.crc32(body) & 0xFFFFFFFF != fragment_crc:
        raise WireError("the fragment failed its CRC; the link dropped or changed a byte")
    return dict(is_ack=False, kind=kind, index=index, count=count, whole_crc=whole_crc,
                fragment_crc=fragment_crc, body=body)


class Reassembler:
    """Fragments back into one payload, with duplicate and gap handling."""

    def __init__(self):
        self.parts: dict[int, bytes] = {}
        self.count = None
        self.whole_crc = None
        self.kind = None

    def feed(self, message: dict) -> bytes | None:
        """-> the whole payload when complete, else None. Raises on a contradiction."""
        if message["is_ack"]:
            raise WireError("an ack is not a fragment")
        if self.kind is None:
            self.kind, self.count, self.whole_crc = message["kind"], message["count"], message["whole_crc"]
        elif (message["kind"], message["count"], message["whole_crc"]) \
                != (self.kind, self.count, self.whole_crc):
            raise WireError("a fragment contradicts the transfer it joined")
        self.parts.setdefault(message["index"], message["body"])
        if len(self.parts) < self.count:
            return None
        payload = b"".join(self.parts[i] for i in range(self.count))
        if zlib.crc32(payload) & 0xFFFFFFFF != self.whole_crc:
            raise WireError("the transfer failed its whole-payload CRC")
        return payload


class Sender:
    """One payload, retransmitted until every fragment is acknowledged."""

    def __init__(self, kind: int, payload: bytes, now: float, fragment_size: int = FRAGMENT_SIZE):
        self.messages = build(kind, payload, fragment_size)
        self.pending: dict[int, tuple[bytes, int]] = {
            i: (msg, zlib.crc32(msg[HEADER_SIZE:]) & 0xFFFFFFFF)
            for i, msg in enumerate(self.messages)}
        self.whole_crc = zlib.crc32(payload) & 0xFFFFFFFF
        self.next_tx = now
        self.done = False

    def poll(self, now: float, interval: float = 0.5) -> list[bytes]:
        """-> the messages to put on the wire now."""
        if self.done or not self.pending:
            return []
        if now < self.next_tx:
            return []
        self.next_tx = now + interval
        return [msg for msg, _crc in self.pending.values()]

    def on_ack(self, ack: dict) -> bool:
        """-> True when the transfer is complete."""
        if ack.get("whole_crc") != self.whole_crc:
            return False
        self.pending.pop(next((i for i, (_m, crc) in self.pending.items()
                               if crc == ack.get("acked_fragment_crc")), None), None)
        if not self.pending:
            self.done = True
        return self.done


def ack_for(message: bytes) -> bytes:
    """The ack a correct receiver answers one message with."""
    parsed = parse(message)
    return build_ack(parsed["kind"], parsed["whole_crc"], parsed["fragment_crc"])


# --- containers ------------------------------------------------------------------------------


def pack_mii(mii: LtdMii) -> bytes:
    return mii.pack()


def unpack_mii(payload: bytes) -> LtdMii:
    return LtdMii.parse(payload)


def pack_ugc(item: LtdUGC) -> bytes:
    return item.pack()


def unpack_ugc(payload: bytes, kind: int | None = None) -> LtdUGC:
    item = LtdUGC.parse(payload)
    if kind is not None and item.kind != kind:
        raise WireError(f"the payload is a {item.type_name}, not the kind requested")
    return item
