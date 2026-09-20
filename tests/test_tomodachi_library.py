"""The .ltd library: filing, deduplication, listing, lookup - and nothing but files.

Every test here runs against a temp directory; nothing touches a save folder, because the library
has no save code at all. The point of the dedup tests is that a container received twice - two
sessions, a friend's re-send, a replay plus the live capture - is one file, which is what makes a
long-lived collection usable.
"""
import os

import pytest

from pokeldn.tomodachi import library as library_lib
from pokeldn.tomodachi.ltd import LtdError, LtdMii, LtdUGC
from pokeldn.tomodachi.library import Library, LibraryError, parse_file


def a_mii(name="Majima", paint=False):
    data = bytes(((i * 7 + 13) & 0xFF) for i in range(156))
    personality = bytes(((i * 3 + 1) & 0xFF) for i in range(18 * 4))
    mii = LtdMii(data=data, personality=personality,
                 name=name.encode("utf-16-le").ljust(64, b"\0"), pronounce=bytes(128))
    mii = mii.with_sexuality_bits([1, 0, 1])
    if paint:
        mii.canvas, mii.ugctex = b"\x11" * 8, b"\x22" * 8
        mii.mark_facepaint(True)
    return mii


def an_item(kind=0, name="Goku Cake"):
    return LtdUGC(kind, fields=bytes(40), name=name.encode("utf-16-le").ljust(128, b"\0"),
                  pronounce=bytes(128), canvas=b"\xa1" * 4, ugctex=b"\xb1" * 4, thumb=b"\xc1" * 4)


def test_a_mii_is_filed_under_its_decoded_name(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    path = lib.add_mii(a_mii("Kirishima"))
    assert os.path.basename(path) == "Kirishima.ltd"
    assert lib.paths() == [path]


def test_an_identical_container_is_one_file_not_two(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    first = lib.add_mii(a_mii("Twin"))
    second = lib.add_mii(a_mii("Twin"))
    assert first == second and len(lib.paths()) == 1
    # a byte-different container with the same name is a new file, "(2)"
    other = a_mii("Twin")
    other.data = bytes(156)
    third = lib.add_mii(other)
    assert third != first and os.path.basename(third) == "Twin (2).ltd"


def test_items_are_filed_under_their_sharemii_extension(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    assert lib.add_ugc(an_item(0)).endswith(".ltdf")
    assert lib.add_ugc(an_item(2, "Ralsei Ball")).endswith(".ltdg")
    assert lib.add_ugc(an_item(5, "Fence")).endswith(".ltdo")


def test_add_file_and_parse_file_round_trip_a_written_container(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    mii_path = raw_dir / "Majima.ltd"
    mii_path.write_bytes(a_mii("Majima", paint=True).pack())
    filed = lib.add_file(str(mii_path))
    again = parse_file(filed)
    assert isinstance(again, LtdMii)
    assert again.display_name == "Majima" and again.has_facepaint


def test_parse_file_refuses_junk(tmp_path):
    junk = tmp_path / "junk.ltd"
    junk.write_bytes(b"not a container" * 20)
    with pytest.raises(LtdError):
        parse_file(str(junk))


def test_entries_lists_miis_by_default_and_one_category_when_asked(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    lib.add_mii(a_mii("Majima"))
    lib.add_ugc(an_item(0, "Goku Cake"))
    lib.add_ugc(an_item(0, "Bento"))
    lib.add_ugc(an_item(5, "Fence"))
    assert [label for _name, label in lib.entries()] == ["Majima"]
    assert sorted(label for _n, label in lib.entries("food")) == ["Bento", "Goku Cake"]
    assert [label for _n, label in lib.entries(5)] == ["Fence"]
    with pytest.raises(LibraryError, match="unknown item type"):
        lib.entries("vehicles")


def test_find_matches_filename_and_decoded_name(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    lib.add_mii(a_mii("Kirishima"))
    assert lib.find("kirishima") and lib.find("KIRISHIMA.LTD")
    assert lib.find("rishi")                      # a substring of the decoded name
    assert lib.find("nobody") == []


def test_every_method_is_safe_on_a_directory_that_does_not_exist(tmp_path):
    lib = Library(str(tmp_path / "nothing"))
    assert lib.paths() == [] and lib.entries() == [] and lib.find("x") == []
    with pytest.raises(LibraryError, match="not in the library"):
        lib.load(str(tmp_path / "nothing" / "x.ltd"))


def test_the_default_library_is_under_the_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert library_lib.DEFAULT_DIR.endswith(os.path.join("tomodachi", "library"))
