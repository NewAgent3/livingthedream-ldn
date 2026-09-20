#!/usr/bin/env python3
"""List, add and hand out Living the Dream Miis and Palette House items as .ltd files.

This is the collection half of the Tomodachi tools: a plain folder of ShareMii-format containers.
It reads and writes `.ltd` files and nothing else - no save folders, no console. Containers come
in from the local-wireless exchange (`tomodachi_exchange.py`), from files a friend sent, or from a
ShareMii export, and go back out to any of those places.

    tomodachi_library.py [DIR] list
    tomodachi_library.py [DIR] list-ugc food
    tomodachi_library.py [DIR] add FILE.ltd ...
    tomodachi_library.py [DIR] get NAME DIR          # copy out by name
    tomodachi_library.py [DIR] show FILE.ltd

DIR defaults to the XDG data collection (~/.local/share/tomodachi/library).
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pokeldn.tomodachi import ltd as ltd_lib
from pokeldn.tomodachi import library as library_lib
from pokeldn.tomodachi.library import Library, LibraryError, parse_file


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", default=library_lib.DEFAULT_DIR,
                    help="the collection folder (default: %(default)s)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the Miis")
    p = sub.add_parser("list-ugc", help="list one Palette House category")
    p.add_argument("kind", help="food, clothing, treasure, interior, exterior, objects, landscaping")
    p = sub.add_parser("add", help="add containers to the collection")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("get", help="copy containers out by name")
    p.add_argument("name")
    p.add_argument("directory_out")
    p = sub.add_parser("show", help="print what one container holds")
    p.add_argument("file")
    args = ap.parse_args(argv)

    lib = Library(args.directory)
    try:
        if args.command == "list":
            rows = lib.entries()
            if not rows:
                print(f"no Miis in {lib.directory} yet; add some with 'add' or run the exchange")
                return 0
            for name, label in rows:
                print(f"{name}  {label}")
            return 0

        if args.command == "list-ugc":
            rows = lib.entries(args.kind)
            if not rows:
                print(f"no {args.kind} in {lib.directory} yet")
                return 0
            for name, label in rows:
                print(f"{name}  {label}")
            return 0

        if args.command == "add":
            for path in args.files:
                try:
                    item = parse_file(path)
                except OSError as exc:
                    print(f"  ! {path}: {exc}")
                    continue
                except ltd_lib.LtdError as exc:
                    print(f"  ! {path}: not a Living the Dream container: {exc}")
                    continue
                if isinstance(item, ltd_lib.LtdMii):
                    at = lib.add_mii(item)
                    print(f"  mii  {item.display_name or 'Unnamed'} -> {at}")
                else:
                    at = lib.add_ugc(item)
                    print(f"  {item.type_name.lower()}  {item.display_name or item.type_name}"
                          f" -> {at}")
            return 0

        if args.command == "get":
            hits = lib.find(args.name)
            if not hits:
                sys.exit(f"error: nothing named {args.name!r} in {lib.directory}")
            os.makedirs(args.directory_out, exist_ok=True)
            for path in hits:
                dest = os.path.join(args.directory_out, os.path.basename(path))
                shutil.copyfile(path, dest)
                print(f"  {path} -> {dest}")
            return 0

        if args.command == "show":
            item = parse_file(args.file)
            if isinstance(item, ltd_lib.LtdMii):
                paint = "yes" if item.has_facepaint else "no"
                print(f"mii          {item.display_name or 'Unnamed'}")
                print(f"container    v{item.version}, {len(item.pack())} bytes")
                print(f"facepaint    {paint}")
                print(f"sexuality    {item.sexuality_bits}")
                print(f"personality  {'present' if item.personality else 'none (draft)'}")
            else:
                print(f"ugc          {item.display_name or item.type_name}")
                print(f"kind         {item.kind} ({item.type_name}, {item.extension})")
                print(f"textures      canvas {len(item.canvas)} B, ugctex {len(item.ugctex)} B,"
                      f" thumb {len(item.thumb)} B")
            return 0
    except LibraryError as exc:
        sys.exit(f"error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
