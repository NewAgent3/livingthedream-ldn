---
title: Tomodachi Life: Living the Dream
nav_order: 10
---

# Tomodachi Life: Living the Dream

Tomodachi Life: Living the Dream (2026, title id `010051f0207b2000`) is a native Switch title with
no online play and no QR codes: the only way a Mii leaves a console is the game's own local
wireless exchange. That makes it a pokeldn target in the strict sense - a Linux machine joins the
game's own LDN session the way it joins a Pokémon trade, with nothing installed on the console.

This module is a bring-up in progress. The storage half is finished and byte-compatible with the
community's `ShareMii` tool; the link half is wired through the shared LDN and Pia layers and
waits on the first capture from a real console to fix its constants.

## The storage half: `.ltd` files

A Mii or user-created item on disk is a ShareMii-format container, so files move between this
tool and ShareMii unchanged:

| extension | contents |
|---|---|
| `.ltd` | one Mii: version byte, paint flags, the raw 156-byte Mii block, 18 personality/voice u32s, name and pronunciation in UTF-16LE, three gender-of-interest bytes, then the facepaint textures behind `A3A3A3A3`/`A4A4A4A4` markers |
| `.ltdf` | one Palette House creation (food), `.ltdc` clothing, `.ltdg` treasure, `.ltdi` interior, `.ltde` exterior, `.ltdo` objects, `.ltdl` landscaping |

`pokeldn.tomodachi.ltd` owns the byte layouts and `pokeldn.tomodachi.library` owns the collection:
a folder of containers with dedup-by-content, name lookup and category listing. The library never
touches a game save. The save formats are deliberately out of scope: they only exist behind
homebrew save managers (Checkpoint/JKSV) or an emulator, and the tool is built to work with a
retail console instead.

The collection is filled three ways:

* the local-wireless exchange (below), which is the point of the module;
* `bin/tomodachi_library.py add FILE.ltd ...`, for files a friend sent or a ShareMii export;
* the tool-to-tool container channel, for library-to-library transfer with no console in the room.

## The link half: the exchange over LDN

What is known, and how:

| | value | source |
|---|---|---|
| title id | `0x010051F0207B2000` | the eShop page and the Checkpoint folder name in the ShareMii guide |
| Pia header band | expected version 11 (Pia 6.16-6.30), 0x1C-byte header, 8-byte tag | the 2026 SDK generation; refuted or confirmed by the first packet |
| Pia game key | **unknown**; candidates swept per datagram | the GCM tag is the oracle - one authenticated datagram fixes it |
| LDN local communication id | **unknown**; read off the air by the scan tool | like every other title before its first capture |
| LDN passphrase | the shared family passphrase, association only | a placeholder until the binary is read; the joiner cannot see the radio's passphrase check |

The session key at this band is AES-128-ECB(game key, SSID) and the network id is crc32 of the
SSID's tail - `pokeldn.ldn.pia6`'s derivation, already proven on Legends Arceus hardware.

The tooling:

| tool | what it does |
|---|---|
| `bin/tomodachi_scan.py` | lists the game's sessions on the air and prints each advertisement raw; the first run names the local communication id |
| `bin/tomodachi_exchange.py` | joins a session, records every Pia datagram to a JSONL trace, sweeps candidate game keys against each until one authenticates, and files received containers into the library; `--replay` decodes a trace offline |
| `bin/tomodachi_library.py` | lists, adds, finds and shows the `.ltd`/`.ltd*` collection |

## Bring-up runbook

1. `sudo -E python3 bin/tomodachi_scan.py --seconds 30` with a console sitting on the exchange
   screen. The session's `local_communication_id` is the value `session.py` needs confirmed; the
   application data is decoded raw.
2. `sudo -E python3 bin/tomodachi_exchange.py --capture trace.jsonl --library LIB` on the same
   screen. Every datagram lands in the trace raw and, once one authenticates under a swept
   candidate, decrypted under the fixed key; the trace also carries the joined session's
   advertisement (local communication id, application data) - between the advert and the
   datagrams, a trace holds everything a future sender needs but the LDN passphrase.
3. Read the trace: the Pia header's version byte confirms or refutes the version-11 band, and the
   game's own exchange protocol appears in the authenticated plaintexts.
4. Feed the format findings back into `pokeldn/tomodachi/wire.py`, which already defines the
   tool-to-tool container channel (fragmented, CRC'd, acked) that two machines on the session can
   use with no console involved.

The wire protocol between two collectors is the module's own, not the game's: a "TOMO" frame
carries whole containers fragmented at 1404 bytes, each fragment CRC'd, the whole payload CRC'd,
every fragment acked, and retransmission until silence. A mismatched whole-CRC names a truncated
transfer, and nothing reaches the library until the whole payload checks.

## What the game does and does not allow

The game's own exchange is console-to-console: a player sends a Mii from their island to a nearby
console, and the receiving game imports it as a resident. Miis made in game cannot leave it - not
to Mii Maker, not to other software - and the game speaks no QR codes, no StreetPass and no
online gallery. A facepaint Mii carries its painting with it; the paint rides along in the
container the same way.

This module does not put anything *into* a console's save. A Mii goes back to an island the way
the player does it: the exchange screen. What the module adds is a place for Miis to live
between islands, and a way for two collectors to trade them without a console in the room.
