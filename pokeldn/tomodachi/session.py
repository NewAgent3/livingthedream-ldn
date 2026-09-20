"""Everything the Living the Dream link is keyed on, as far as it is currently known.

The picture, and what is measured against what:

* The console path is local wireless, full stop. The game ships no online play and no QR codes, so
  the tool meets it the way another console does: LDN association, then Pia, then whatever the
  exchange puts on the session. This module holds the constants that bring-up needs.
* The title is `0x010051F0207B2000` (the US store page and the Checkpoint folder name in the
  ShareMii guide agree). Its local communication id and local communication version are still
  UNGUESSED: both are read off the air by `bin/tomodachi_scan.py`, like every other field this
  module carries.
* The Pia header is expected to be the 6.16-6.30 band, version 11, the band Legends Arceus speaks
  (`pokeldn.ldn.pia6`): Living the Dream is a 2026 title on the same SDK generation, and the
  header shape and crypto envelope are the ones `crypto.PiaCrypto` already runs. Every packet the
  console sends refutes or confirms this; nothing here is trusted further than the first capture.
* The session key is AES-128-ECB(game_key, ssid) at this band, and the network id is crc32 of the
  SSID's tail. What is NOT known for this title is its Pia game key: the module ships with the
  known family candidates and derives nothing it cannot check, because a wrong key fails the GCM
  tag and is thereby refuted, while a guessed key that happens to authenticate is thereby proven.
* The LDN passphrase below is the one every audited title shares, carried for association only -
  the joiner cannot see the radio's passphrase check, so it fixes nothing. It is a placeholder
  until the game's own is read out of its binary, and the scan tool runs fine without it.

What a session needs, and where each part comes from:

    LDN passphrase      association only; the radio asks for it, the game never sees it
    Pia game key        rodata; unknown here. A capture + `sweep_game_keys` settles it
    session key         derived from the SSID the advertisement carries, once the game key is known
    GCM IV              network id ^ source IP, then the header's own nonce
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pokeldn.ldn.crypto import PiaCrypto

# The title id, from the eShop page and the Checkpoint path in the ShareMii guide.
TITLE_ID = 0x010051F0207B2000

# The expected Pia header band: version 11, 0x1C bytes, an 8-byte tag. Confirmed per packet by the
# version byte; a version byte outside the 6.x bands is itself a finding.
PIA_VERSION = 11
PIA_HEADER_SIZE = 0x1C
PIA_TAG_SIZE = 8

# Every Pia station listens on the same port; this is not game-specific, and BDSP measured it.
PIA_PORT = 12345

# The shared LDN passphrase, association only. See the docstring: a placeholder, not a finding.
PASSPHRASE = b"W3GoSMEn7RIIUQ89rzqBHGhGferRNb7K18ZBq2aNuj8Us9RO9Q9JYyGOZlLy8MYL"
assert len(PASSPHRASE) == 64

# The candidates a capture is swept against until the game key is read out of the binary. The
# first is the key Sword/Shield, Scarlet/Violet and Legends Arceus share; a 2026 title on the same
# SDK generation is the likeliest next user of it. The tag decides, not the prior.
GAME_KEY_CANDIDATES = [
    b"p1frXqxmeCZWFv0X",
    bytes.fromhex("83ca7fab734c34633b10183526c1e85b"),   # the GBA app's key
]


class SessionKeys:
    """The crypto half of a Living the Dream session, parameterised by the Pia game key.

    `for_ssid()` builds the object; `decrypt()` answers None for a datagram that does not
    authenticate, which is the only oracle this module needs: a wrong key, SSID, network id or
    source address all fail the tag, and the derivation is `crypto.PiaCrypto`'s, the same one
    Legends Arceus runs at this band.
    """

    def __init__(self, ssid: bytes, game_key: bytes):
        if len(game_key) != 16:
            raise ValueError("a Pia game key is sixteen bytes")
        self.ssid = bytes(ssid)
        self.game_key = bytes(game_key)
        self.crypto = PiaCrypto(self.ssid, game_key=self.game_key)

    @classmethod
    def for_ssid(cls, ssid: bytes, game_key: bytes) -> "SessionKeys":
        return cls(ssid, game_key)

    def decrypt(self, datagram: bytes, src_ip: str):
        """-> plaintext, or None when the tag does not verify."""
        return self.crypto.decrypt(datagram, src_ip)

    def encrypt(self, plaintext: bytes, src_ip: str, header) -> bytes:
        return self.crypto.encrypt(plaintext, src_ip, header)


def sweep_game_keys(datagram: bytes, src_ip: str, ssid: bytes) -> list[bytes]:
    """-> every candidate key that authenticates one captured datagram.

    The tag is the oracle; one authenticated packet fixes the key for the whole session.
    """
    hits = []
    for key in GAME_KEY_CANDIDATES:
        keys = SessionKeys(ssid, key)
        if keys.decrypt(datagram, src_ip) is not None:
            hits.append(key)
    return hits


@dataclass
class Advert:
    """One Living the Dream session as a scan saw it."""
    local_communication_id: int
    scene_id: int
    app_version: int
    channel: int
    band: int
    ssid: bytes
    security_mode: int
    accept_policy: int
    num_participants: int
    max_participants: int
    application_data: bytes
    host_ip: str = ""
    ldn_protocol: int = 0
    advertisement_format: int = 0
    participants: list = field(default_factory=list)

    @property
    def title_id_like(self) -> bool:
        """Whether the local communication id matches this title's own high bits."""
        return (self.local_communication_id & ~0xFFFFF) == (TITLE_ID & ~0xFFFFF)
