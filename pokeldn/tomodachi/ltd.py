"""The Living the Dream share containers, byte-compatible with ShareMii 3.2.3.

A `.ltd` file is what Star-F0rce's ShareMii and Azkun's web port both write, so every Mii moved
through either tool opens here unchanged and every Mii we write opens there. A `.ltdf` family file
is ShareUGC.py's container for one Palette House creation.

    .ltd    [0]     version byte, 1..3            (3 for a real islander, 1 for the draft slot)
            [1]     has canvas texture flag       (a facepaint's UgcFacePaintNN.canvas.zs)
            [2]     has ugctex flag               (the same paint's UgcFacePaintNN.ugctex.zs)
            [3]     unused
            [4:160] the Mii's raw 156 bytes, copied whole from Mii.sav
            [160:232] 18 x u32 personality/voice/profile values
            [232:296] the name, 64 bytes of UTF-16LE
            [296:424] the pronunciation, 128 bytes
            [424:428] the three gender-of-interest values, one byte each, then a zero
            then, only when the paint flags are set, starting at TEXTURES_OFF:
            A3A3A3A3 <canvas.zs bytes> A4A4A4A4 <ugctex.zs bytes>

The web port (app.js) writes the same thing; it labels the first byte of [4:160] `ltd[47]` from a
different index because it slices the Mii data at 4. ShareMii.py's `mii[47] = 1` is the byte at
absolute offset 47, and so is the web port's `ltd[47]`; both write the same cell of the
container. The Mii's own facepaint-present byte is at absolute offset 47, i.e. Mii data byte 43.
"""
from __future__ import annotations

import re

# Section markers, the boundaries of the texture payloads.
CANVAS_MARKER = b"\xa3\xa3\xa3\xa3"
UGC_MARKER = b"\xa4\xa4\xa4\xa4"
THUMB_MARKER = b"\xa5\xa5\xa5\xa5"          # .ltdf only
NAME_MARKER = b"\xa2\xa2\xa2\xa2"            # .ltdf only

MII_DATA_SIZE = 156
PERSONALITY_SIZE = 18 * 4
NAME_SIZE = 64
PRONOUNCE_SIZE = 128

MII_DATA_OFF = 4
PERSONALITY_OFF = MII_DATA_OFF + MII_DATA_SIZE          # 160
NAME_OFF = PERSONALITY_OFF + PERSONALITY_SIZE           # 232
PRONOUNCE_OFF = NAME_OFF + NAME_SIZE                    # 296
# The container carries the three gender-of-interest values as BYTES (each 0 or 1) plus one zero
# byte: ShareMii.py appends a 0 to a three-element list and writes `bytearray(sexuality)`, four
# bytes, and a v2 file needed one byte spliced in at 427 to become v3. The A3/A4 markers are
# located by search from TEXTURES_OFF, never by offset.
SEXUALITY_OFF = PRONOUNCE_OFF + PRONOUNCE_SIZE          # 424
SEXUALITY_SIZE = 4                                      # three value bytes + the zero
FULL_SIZE = SEXUALITY_OFF + SEXUALITY_SIZE              # 428: a v2/v3 container's fixed part
DRAFT_SIZE = MII_DATA_OFF + MII_DATA_SIZE               # 160: a v1 (draft) container, data only
TEXTURES_OFF = FULL_SIZE                                # where the paint markers start, v2/v3

# The Mii-data byte ShareMii sets when a facepaint rides along: container byte 47.
MII_HAS_FACEPAINT = 47

SEXUALITY_BLOCK_SIZE = 27                # Mii.sav holds 216 bits: 70 Miis x 3, rounded to bytes


class LtdError(ValueError):
    """A container this module refuses to read rather than misread."""


def sanitize_name(name: str) -> str:
    """A filename-safe rendering of a Mii or UGC name, ShareMii's regex."""
    return re.sub(r"[^\w.-]", "_", name) or "Unnamed"


def decode_name(raw: bytes) -> str:
    """UTF-16LE up to the first 000000 boundary, ShareMii's own cut.

    `name[:name.find(b'\\0\\0\\0')]`: a `find` of -1 therefore keeps everything but the last
    byte, which is also how an odd-length tail survives the UTF-16 rounding below.
    """
    end = bytes(raw).find(b"\0\0\0")
    if end == -1:
        end = max(len(raw) - 1, 0)
    chunk = bytes(raw[:end])
    if len(chunk) % 2:
        chunk += b"\0"
    return chunk.decode("utf-16-le", "replace")


# --- .ltd: Miis -------------------------------------------------------------------------------


class LtdMii:
    """One .ltd Mii container. `data` is the raw Mii block, exactly as it sits in Mii.sav."""

    def __init__(self, data=b"", personality=bytes(PERSONALITY_SIZE), name=b"",
                 pronounce=bytes(PRONOUNCE_SIZE), sexuality=bytes(SEXUALITY_SIZE),
                 canvas=b"", ugctex=b"", version=3):
        self.version = int(version)
        self.data = bytes(data)
        self.personality = bytes(personality)
        self.name = bytes(name)
        self.pronounce = bytes(pronounce)
        self.sexuality = bytes(sexuality)
        self.canvas = bytes(canvas)
        self.ugctex = bytes(ugctex)

    # -- containers ---------------------------------------------------------------------------

    @classmethod
    def parse(cls, raw):
        raw = bytes(raw)
        if len(raw) < DRAFT_SIZE:
            raise LtdError(f"a .ltd is at least {DRAFT_SIZE} bytes, got {len(raw)}")
        version = raw[0]
        if version not in (1, 2, 3):
            raise LtdError(f".ltd version must be 1-3, got {version}")
        has_canvas, has_ugctex = bool(raw[1]), bool(raw[2])
        mii = cls(data=raw[MII_DATA_OFF:MII_DATA_OFF + MII_DATA_SIZE], version=version,
                  personality=b"", name=b"", pronounce=b"", sexuality=b"")
        if len(raw) >= FULL_SIZE:
            # A v1 (draft) container stops at 160: from v2 on the personality block rides along.
            mii.personality = raw[PERSONALITY_OFF:NAME_OFF] if version >= 2 else b""
            mii.name = raw[NAME_OFF:PRONOUNCE_OFF]
            mii.pronounce = raw[PRONOUNCE_OFF:SEXUALITY_OFF]
            mii.sexuality = raw[SEXUALITY_OFF:SEXUALITY_OFF + SEXUALITY_SIZE]
        if not has_canvas and not has_ugctex:
            return mii
        tail = raw[FULL_SIZE:]
        try:
            canvas_at = tail.index(CANVAS_MARKER) + 4
            ugctex_at = tail.index(UGC_MARKER, canvas_at) + 4
        except ValueError as exc:
            raise LtdError(f"paint flags set but the texture markers are missing: {exc}") from None
        mii.canvas = tail[canvas_at:ugctex_at - 4]
        mii.ugctex = tail[ugctex_at:]
        if has_canvas and not mii.canvas:
            raise LtdError("the canvas flag is set but the canvas section is empty")
        if has_ugctex and not mii.ugctex:
            raise LtdError("the ugctex flag is set but the ugctex section is empty")
        return mii

    def pack(self) -> bytes:
        if len(self.data) != MII_DATA_SIZE:
            raise LtdError(f"the Mii block is {MII_DATA_SIZE} bytes, got {len(self.data)}")
        out = bytearray([self.version & 0xFF, 1 if self.canvas else 0, 1 if self.ugctex else 0, 0])
        out += self.data
        if self.version >= 2:
            out += self.personality.ljust(PERSONALITY_SIZE, b"\0")[:PERSONALITY_SIZE]
            out += self.name.ljust(NAME_SIZE, b"\0")[:NAME_SIZE]
            out += self.pronounce.ljust(PRONOUNCE_SIZE, b"\0")[:PRONOUNCE_SIZE]
            out += self.sexuality.ljust(SEXUALITY_SIZE, b"\0")[:SEXUALITY_SIZE]
        if self.canvas or self.ugctex:
            out += CANVAS_MARKER + self.canvas
            out += UGC_MARKER + self.ugctex
        return bytes(out)

    # -- conveniences -------------------------------------------------------------------------

    @property
    def display_name(self) -> str:
        return decode_name(self.name)

    @property
    def file_stem(self) -> str:
        return sanitize_name(self.display_name) if self.display_name else "Unnamed"

    @property
    def has_facepaint(self) -> bool:
        return bool(self.canvas or self.ugctex)

    def set_name(self, text: str):
        encoded = text.encode("utf-16-le")[:NAME_SIZE]
        encoded += b"\0" * (NAME_SIZE - len(encoded))
        self.name = encoded

    def mark_facepaint(self, on: bool):
        """Set or clear the Mii block's own facepaint-present byte: container byte 47, data byte 43."""
        data = bytearray(self.data)
        data[MII_HAS_FACEPAINT - MII_DATA_OFF] = 1 if on else 0
        self.data = bytes(data)

    @property
    def sexuality_bits(self) -> list[int]:
        """The three gender-of-interest values, each 0 or 1, one per byte.

        ShareMii stores one Mii's row of Mii.sav's packed 27-byte block as `bytearray(bits)`:
        a byte per bit, then a zero. `with_sexuality_bits` is the inverse.
        """
        return [1 if b else 0 for b in self.sexuality[:3]]

    def with_sexuality_bits(self, bits: list[int]) -> "LtdMii":
        """A copy carrying these three 0/1 values, ShareMii's `list + append(0)` shape."""
        padded = list(bits[:3]) + [0] * (3 - len(bits))
        return LtdMii(self.data, self.personality, self.name, self.pronounce,
                      bytes([1 if b else 0 for b in padded] + [0]),
                      self.canvas, self.ugctex, self.version)


# --- .ltdf: UGC ------------------------------------------------------------------------------


# kind -> (ShareMii type name, file extension). ShareUGC.py's two tables, joined.
UGC_KINDS = [
    ("Food", ".ltdf"),
    ("Clothing", ".ltdc"),
    ("Treasure", ".ltdg"),
    ("Interior", ".ltdi"),
    ("Exterior", ".ltde"),
    ("Objects", ".ltdo"),
    ("Landscaping", ".ltdl"),
]
UGC_EXTENSIONS = {ext: kind for kind, (name, ext) in enumerate(UGC_KINDS)}
UGC_NAMES = {name: kind for kind, (name, ext) in enumerate(UGC_KINDS)}


class LtdUGC:
    """One Palette House creation: ShareUGC.py's output, byte for byte.

        [0]     kind, 0..6 (Food..MapFloor)
        [1:4]   zero
        [4:..]  the kind's personality values, 4 bytes each, in ShareUGC's field order
        then, before the name section: the kind's vector blocks (BaseColor 12 bytes,
        MaterialTexScale/Scale 8 bytes) when the kind has them
        A2A2A2A2 <name 128> <pronounce 128> [Goods: <text 64> <text pronounce 128>]
        A3A3A3A3 <canvas.zs> A4A4A4A4 <ugctex.zs> A5A5A5A5 <thumb.zs>
    """

    def __init__(self, kind, fields=b"", name=b"", pronounce=b"", extra=b"",
                 vector=b"", vector2=b"", canvas=b"", ugctex=b"", thumb=b""):
        self.kind = int(kind)
        self.fields = bytes(fields)
        self.name = bytes(name)
        self.pronounce = bytes(pronounce)
        self.extra = bytes(extra)          # Goods: text 64 + text pronounce 128
        self.vector = bytes(vector)        # BaseColor (Goods/Exterior 12, MapObject Scale 12)
        self.vector2 = bytes(vector2)      # Exterior MaterialTexScale 8, MapObject MaterialTexScale 8
        self.canvas = bytes(canvas)
        self.ugctex = bytes(ugctex)
        self.thumb = bytes(thumb)

    @classmethod
    def parse(cls, raw):
        raw = bytes(raw)
        if not raw:
            raise LtdError("the file is empty")
        kind = raw[0]
        if kind >= len(UGC_KINDS):
            raise LtdError(f"UGC kind {kind} is outside 0..{len(UGC_KINDS) - 1}")
        try:
            name_at = raw.index(NAME_MARKER) + 4
            canvas_at = raw.index(CANVAS_MARKER) + 4
            ugctex_at = raw.index(UGC_MARKER, canvas_at) + 4
            thumb_at = raw.index(THUMB_MARKER, ugctex_at) + 4
        except ValueError as exc:
            raise LtdError(f"the section markers are incomplete: {exc}") from None
        item = cls(kind)
        # Vector blocks ride immediately before the A2 marker when the kind has them.
        before = raw[4:name_at - 4]
        if kind in (2, 4, 5):
            item.vector, before = before[-20:-8], before[:-20]
            item.vector2, before = before[-8:], before[:-8]
        item.fields = before
        item.name = raw[name_at:name_at + 128]
        item.pronounce = raw[name_at + 128:name_at + 256]
        if kind == 2:
            item.extra = raw[name_at + 256:canvas_at - 4]
        item.canvas = raw[canvas_at:ugctex_at - 4]
        item.ugctex = raw[ugctex_at:thumb_at - 4]
        item.thumb = raw[thumb_at:]
        return item

    def pack(self) -> bytes:
        out = bytearray([self.kind & 0xFF, 0, 0, 0])
        out += self.fields
        out += self.vector + self.vector2
        out += NAME_MARKER + self.name.ljust(128, b"\0")[:128]
        out += self.pronounce.ljust(128, b"\0")[:128]
        if self.kind == 2:
            out += self.extra
        out += CANVAS_MARKER + self.canvas + UGC_MARKER + self.ugctex + THUMB_MARKER + self.thumb
        return bytes(out)

    @property
    def type_name(self) -> str:
        return UGC_KINDS[self.kind][0]

    @property
    def extension(self) -> str:
        return UGC_KINDS[self.kind][1]

    @property
    def display_name(self) -> str:
        return decode_name(self.name)

    @property
    def file_stem(self) -> str:
        return sanitize_name(self.display_name) if self.display_name else self.type_name


def detect_kind(filename: str) -> int | None:
    """The UGC kind a filename claims, by extension."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return UGC_EXTENSIONS.get(ext)
