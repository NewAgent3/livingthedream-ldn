"""The local collection of Living the Dream containers, on disk.

This is the whole storage half of the tool: a folder of `.ltd` Miis and `.ltdf`-family Palette
House items in ShareMii's format, plus the bookkeeping a working collection needs - deduplication
by content, name lookup, and category listing. Nothing here touches a game save: containers come
off the local-wireless exchange (or `--add`), land here as files, and go back out the same way,
which is exactly how ShareMii's own users move Miis between islands.

A `.ltd` file holds one Mii and a `.ltdf`/`.ltdc`/`.ltdg`/`.ltdi`/`.ltde`/`.ltdo`/`.ltdl` file
holds one user-created item; `pokeldn.tomodachi.ltd` owns both byte layouts.
"""
from __future__ import annotations

import hashlib
import os

from .ltd import LtdError, LtdMii, LtdUGC, UGC_EXTENSIONS, UGC_KINDS, UGC_NAMES

#: The default collection, under XDG_DATA_HOME.
DEFAULT_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                           "tomodachi", "library")

MII_EXT = ".ltd"


class LibraryError(ValueError):
    """A file this module refuses to file rather than misfile."""


def parse_file(path: str) -> LtdMii | LtdUGC:
    """One container from disk: a `.ltd` Mii or a `.ltd*` item, by ShareMii's extensions.

    An unknown extension is decoded rather than refused: try the Mii shape, then the item shape.
    """
    raw = open(path, "rb").read()
    ext = os.path.splitext(path)[1].lower()
    if ext == MII_EXT:
        return LtdMii.parse(raw)
    if ext in UGC_EXTENSIONS:
        return LtdUGC.parse(raw)
    try:
        return LtdMii.parse(raw)
    except LtdError:
        return LtdUGC.parse(raw)          # an LtdError here is the caller's to see


def _content_key(payload: bytes) -> str:
    return hashlib.sha1(bytes(payload)).hexdigest()


def _free_path(directory: str, stem: str, ext: str) -> str:
    """A path that does not collide: `Name.ltd`, then `Name (2).ltd`, ShareMii's browser habit."""
    path = os.path.join(directory, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem} ({n}){ext}")
        n += 1
    return path


class Library:
    """A folder of containers. Every method is safe to call on a directory that does not exist yet."""

    def __init__(self, directory: str = DEFAULT_DIR):
        self.directory = os.fspath(directory)

    # -- filing --------------------------------------------------------------------------------

    def add_mii(self, payload: bytes | LtdMii, name: str | None = None) -> str:
        """File one `.ltd` Mii. An identical container already in the library wins: same path back."""
        mii = payload if isinstance(payload, LtdMii) else LtdMii.parse(payload)
        raw = mii.pack()
        existing = self._find_content(_content_key(raw))
        if existing:
            return existing
        return self._write(raw, name or mii.file_stem, MII_EXT)

    def add_ugc(self, payload: bytes | LtdUGC, name: str | None = None) -> str:
        """File one Palette House item, extensioned by its kind."""
        item = payload if isinstance(payload, LtdUGC) else LtdUGC.parse(payload)
        raw = item.pack()
        existing = self._find_content(_content_key(raw))
        if existing:
            return existing
        return self._write(raw, name or item.file_stem, item.extension)

    def add_file(self, path: str) -> str:
        """File one container from disk, by its own bytes."""
        item = parse_file(path)
        if isinstance(item, LtdMii):
            return self.add_mii(item)
        return self.add_ugc(item)

    def _write(self, raw: bytes, stem: str, ext: str) -> str:
        os.makedirs(self.directory, exist_ok=True)
        path = _free_path(self.directory, stem, ext)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def _find_content(self, key: str) -> str | None:
        for path in self.paths():
            with open(path, "rb") as fh:
                if _content_key(fh.read()) == key:
                    return path
        return None

    # -- listing -------------------------------------------------------------------------------

    def paths(self) -> list[str]:
        """Every container file, sorted."""
        if not os.path.isdir(self.directory):
            return []
        keep = {MII_EXT, *UGC_EXTENSIONS}
        return [os.path.join(self.directory, name) for name in sorted(os.listdir(self.directory))
                if os.path.splitext(name)[1].lower() in keep]

    def load(self, path: str) -> LtdMii | LtdUGC:
        """One container out of the library, decoded and checked."""
        if not os.path.isfile(path):
            raise LibraryError(f"{path} is not in the library")
        return parse_file(path)

    def entries(self, kind: str | int | None = None) -> list[tuple[str, str]]:
        """(filename, display name), Miis by default, one Palette House category when kind is given.

        `kind` is a type name (`food`, `Clothing`, ...) or a 0..6 kind index.
        """
        if isinstance(kind, str):
            if kind.strip().capitalize() not in UGC_NAMES:
                raise LibraryError(f"unknown item type {kind!r}; expected one of "
                                   + ", ".join(name for name, _ in UGC_KINDS))
            want = UGC_NAMES[kind.strip().capitalize()]
        elif kind is not None:
            want = int(kind)
        else:
            want = None
        out = []
        for path in self.paths():
            is_mii = os.path.splitext(path)[1].lower() == MII_EXT
            if (want is None) != is_mii:
                continue
            try:
                item = parse_file(path)
                label = item.display_name
                if not is_mii and item.kind != want:
                    continue
            except LtdError as exc:
                label = f"<unreadable: {exc}>"
            out.append((os.path.basename(path), label))
        return out

    def find(self, name: str) -> list[str]:
        """Containers whose filename or decoded name contains the text, case-insensitively."""
        want = name.strip().lower()
        hits = []
        for path in self.paths():
            base = os.path.basename(path)
            stem = os.path.splitext(base)[0]
            try:
                label = parse_file(path).display_name
            except LtdError:
                label = ""
            if any(want in field for field in (base.lower(), stem.lower(), label.strip().lower())):
                hits.append(path)
        return hits
