"""The share containers: .ltd Miis and .ltdf items, byte-compatible with ShareMii 3.2.3.

Every layout fact asserted here is ShareMii.py / ShareUGC.py / app.js read as bytes, so a file the
other tools wrote opens here and a file this module writes opens there.
"""
import pytest

from pokeldn.tomodachi import ltd


def a_mii(name="Majima", paint=False):
    data = bytes(((i * 7 + 13) & 0xFF) for i in range(ltd.MII_DATA_SIZE))
    personality = bytes(((i * 3 + 1) & 0xFF) for i in range(ltd.PERSONALITY_SIZE))
    name_bytes = name.encode("utf-16-le").ljust(ltd.NAME_SIZE, b"\0")
    pronounce = bytes(ltd.PRONOUNCE_SIZE)
    mii = ltd.LtdMii(data=data, personality=personality, name=name_bytes, pronounce=pronounce)
    mii = mii.with_sexuality_bits([1, 0, 1])
    if paint:
        mii.canvas = b"\x11\x22\x33\x44"
        mii.ugctex = b"\x55\x66\x77\x88"
        mii.mark_facepaint(True)
    return mii


def test_a_container_round_trips_byte_for_byte():
    mii = a_mii()
    again = ltd.LtdMii.parse(mii.pack())
    assert again.pack() == mii.pack()
    assert again.version == 3
    assert again.display_name == "Majima"
    assert again.sexuality_bits == [1, 0, 1]


def test_the_layout_matches_sharemiis_offsets():
    mii = a_mii()
    raw = mii.pack()
    assert raw[0] == 3
    assert raw[1] == 0 and raw[2] == 0
    assert raw[ltd.MII_DATA_OFF:ltd.MII_DATA_OFF + ltd.MII_DATA_SIZE] == mii.data
    assert raw[ltd.PERSONALITY_OFF:ltd.NAME_OFF] == mii.personality
    assert raw[ltd.NAME_OFF:ltd.PRONOUNCE_OFF] == mii.name
    assert raw[ltd.SEXUALITY_OFF:ltd.SEXUALITY_OFF + 4] == mii.sexuality
    assert len(raw) == ltd.FULL_SIZE


def test_a_facepaint_ride_along_round_trips_with_its_markers():
    mii = a_mii(paint=True)
    raw = mii.pack()
    assert raw[1] == 1 and raw[2] == 1
    assert raw[ltd.TEXTURES_OFF:ltd.TEXTURES_OFF + 4] == ltd.CANVAS_MARKER
    again = ltd.LtdMii.parse(raw)
    assert again.canvas == mii.canvas and again.ugctex == mii.ugctex
    assert again.has_facepaint
    # the Mii-data byte ShareMii sets is container byte 47
    assert again.data[ltd.MII_HAS_FACEPAINT - ltd.MII_DATA_OFF] == 1


def test_paint_flags_without_markers_are_refused():
    raw = bytearray(a_mii().pack())
    raw[1] = 1
    with pytest.raises(ltd.LtdError, match="markers"):
        ltd.LtdMii.parse(bytes(raw))


def test_a_truncated_file_is_refused():
    with pytest.raises(ltd.LtdError, match="at least"):
        ltd.LtdMii.parse(a_mii().pack()[:100])


def test_a_wrong_version_is_refused():
    raw = bytearray(a_mii().pack())
    raw[0] = 7
    with pytest.raises(ltd.LtdError, match="version"):
        ltd.LtdMii.parse(bytes(raw))


def test_names_decode_and_survive_odd_bytes():
    raw = "Mii テスト".encode("utf-16-le") + b"\0" * 40
    assert ltd.decode_name(raw) == "Mii テスト"
    raw_odd = b"\x41\x00\x00"          # 'A' then the 000000 boundary, odd byte included
    assert ltd.decode_name(raw_odd) == "A"


def test_sexuality_values_are_one_byte_each_plus_a_zero():
    mii = a_mii().with_sexuality_bits([0, 1, 0])
    assert mii.sexuality == b"\x00\x01\x00\x00"
    assert mii.sexuality_bits == [0, 1, 0]


def test_a_draft_container_is_v1_and_carries_the_appearance_only():
    mii = a_mii()
    mii.version = 1
    raw = mii.pack()
    assert len(raw) == ltd.DRAFT_SIZE == 160
    again = ltd.LtdMii.parse(raw)
    assert again.version == 1 and again.data == mii.data
    assert again.personality == b""          # nothing to apply to a draft


# --- .ltdf -----------------------------------------------------------------------------------


def an_item(kind=0, name="Goku Cake"):
    fields = bytes(((i + 2) & 0xFF) for i in range(4 * 10))
    name_bytes = name.encode("utf-16-le").ljust(128, b"\0")
    pronounce = bytes(128)
    canvas, ugctex, thumb = b"\xa1" * 5, b"\xb1" * 5, b"\xc1" * 5
    return ltd.LtdUGC(kind, fields=fields, name=name_bytes, pronounce=pronounce,
                      canvas=canvas, ugctex=ugctex, thumb=thumb)


def test_an_ugc_round_trips_byte_for_byte():
    item = an_item()
    again = ltd.LtdUGC.parse(item.pack())
    assert again.pack() == item.pack()
    assert again.kind == 0 and again.display_name == "Goku Cake"
    assert again.canvas == item.canvas and again.ugctex == item.ugctex
    assert again.thumb == item.thumb


def test_ugc_kinds_carry_their_sharemii_names_and_extensions():
    assert ltd.UGC_KINDS[0] == ("Food", ".ltdf")
    assert ltd.UGC_KINDS[1][1] == ".ltdc"
    assert ltd.UGC_KINDS[2] == ("Treasure", ".ltdg")
    assert ltd.detect_kind("Ralsei.ltdo") == 5
    assert ltd.detect_kind("pet.ltdg") == 2
    assert ltd.detect_kind("mii.ltd") is None


def test_a_goods_container_carries_its_text_blocks():
    item = an_item(kind=2)
    item.extra = b"t" * 64 + b"p" * 128
    again = ltd.LtdUGC.parse(item.pack())
    assert again.extra == item.extra


def test_an_unknown_ugc_kind_is_refused():
    with pytest.raises(ltd.LtdError, match="outside"):
        ltd.LtdUGC.parse(b"\x07" + bytes(600))


def test_a_truncated_ugc_is_refused():
    with pytest.raises(ltd.LtdError, match="markers"):
        ltd.LtdUGC.parse(an_item().pack()[:40])
