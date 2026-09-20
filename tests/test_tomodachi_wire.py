"""The wire protocol and the session constants: framing, reassembly, retransmission, acks.

The session tests hold `pokeldn.tomodachi.session` to what is actually known: nothing is asserted
about the game key except that the tag oracle separates a right key from a wrong one, using a
packet encrypted by `crypto.PiaCrypto` with a known key - the same derivation the module runs.
"""
import pytest

from pokeldn.ldn.crypto import PiaCrypto, PiaHeader
from pokeldn.tomodachi import ltd, wire
from pokeldn.tomodachi.session import (GAME_KEY_CANDIDATES, PIA_HEADER_SIZE, PIA_PORT, PIA_VERSION,
                                       PASSPHRASE, SessionKeys, TITLE_ID, sweep_game_keys)

GAME_KEY = b"p1frXqxmeCZWFv0X"
SSID = bytes(range(16))


# --- framing ---------------------------------------------------------------------------------


def test_one_small_payload_is_a_single_fragment_and_round_trips():
    payload = b"a Mii block"
    (msg,) = wire.build(wire.KIND_MII, payload)
    parsed = wire.parse(msg)
    assert parsed["index"] == 0 and parsed["count"] == 1
    assert parsed["kind"] == wire.KIND_MII
    rag = wire.Reassembler()
    assert rag.feed(parsed) == payload


def test_a_large_payload_fragments_and_reassembles_in_order():
    payload = bytes(((i * 7) & 0xFF) for i in range(wire.FRAGMENT_SIZE * 2 + 100))
    messages = wire.build(wire.KIND_MII, payload)
    assert len(messages) == 3
    rag = wire.Reassembler()
    assert None is rag.feed(wire.parse(messages[0]))
    assert None is rag.feed(wire.parse(messages[1]))
    assert rag.feed(wire.parse(messages[2])) == payload


def test_a_flipped_byte_is_caught_by_the_fragment_crc():
    (msg,) = wire.build(wire.KIND_MII, b"hello mii")
    bad = bytearray(msg)
    bad[-1] ^= 1
    with pytest.raises(wire.WireError, match="fragment failed its CRC"):
        wire.parse(bytes(bad))


def test_a_truncated_or_foreign_message_is_refused():
    with pytest.raises(wire.WireError, match="at least"):
        wire.parse(b"TOMO")
    with pytest.raises(wire.WireError, match="magic"):
        wire.parse(b"\0" * 40)
    (msg,) = wire.build(wire.KIND_MII, b"abc")
    with pytest.raises(wire.WireError, match="carries"):
        wire.parse(msg[:-1])


def test_a_duplicate_fragment_is_ignored_not_duplicated():
    payload = b"x" * (wire.FRAGMENT_SIZE + 5)
    messages = wire.build(wire.KIND_UGC, payload)
    rag = wire.Reassembler()
    rag.feed(wire.parse(messages[0]))
    rag.feed(wire.parse(messages[0]))          # the retransmit
    assert rag.feed(wire.parse(messages[1])) == payload


def test_a_contradictory_fragment_is_refused():
    (a,) = wire.build(wire.KIND_MII, b"one")
    (b,) = wire.build(wire.KIND_UGC, b"two")
    rag = wire.Reassembler()
    rag.feed(wire.parse(a))
    with pytest.raises(wire.WireError, match="contradicts"):
        rag.feed(wire.parse(b))


def test_acks_name_the_crcs_they_acknowledge():
    (msg,) = wire.build(wire.KIND_MII, b"ack me")
    parsed = wire.parse(msg)
    ack = wire.parse(wire.ack_for(msg))
    assert ack["is_ack"]
    assert ack["kind"] == wire.KIND_MII
    assert ack["whole_crc"] == parsed["whole_crc"]
    assert ack["acked_fragment_crc"] == parsed["fragment_crc"]


def test_the_sender_retransmits_until_acked_and_then_falls_silent():
    payload = b"y" * (wire.FRAGMENT_SIZE + 3)
    sender = wire.Sender(wire.KIND_MII, payload, now=0.0)
    out = sender.poll(1.0)
    assert len(out) == 2
    assert sender.poll(1.1) == []              # inside the retransmit interval
    for msg in out:
        sender.on_ack(wire.parse(wire.ack_for(msg)))
    assert sender.done
    assert sender.poll(9.0) == []


def test_an_ack_for_another_transfer_does_not_advance_the_window():
    payload = b"z" * (wire.FRAGMENT_SIZE + 3)
    sender = wire.Sender(wire.KIND_MII, payload, now=0.0)
    for msg in sender.poll(1.0):
        sender.on_ack(wire.parse(wire.build_ack(wire.KIND_UGC, 123, 456)))
    assert not sender.done


def test_mii_and_ugc_containers_round_trip_through_the_wire_helpers():
    mii = ltd.LtdMii(data=bytes(156))
    mii.set_name("Round Trip")
    (msg,) = wire.build(wire.KIND_MII, wire.pack_mii(mii))
    back = wire.unpack_mii(wire.Reassembler().feed(wire.parse(msg)))
    assert back.display_name == "Round Trip"
    item = ltd.LtdUGC(2)
    (msg,) = wire.build(wire.KIND_UGC, wire.pack_ugc(item))
    back = wire.unpack_ugc(wire.Reassembler().feed(wire.parse(msg)))
    assert back.kind == 2
    with pytest.raises(wire.WireError, match="not the kind"):
        wire.unpack_ugc(item.pack(), kind=0)


# --- the offline decode path -----------------------------------------------------------------


def test_a_jsonl_capture_of_two_transfers_decodes_into_containers():
    mii = ltd.LtdMii(data=bytes(range(156)))
    mii.set_name("Majima")
    item = ltd.LtdUGC(0)
    records = []
    for kind, payload in ((wire.KIND_MII, wire.pack_mii(mii)),
                          (wire.KIND_UGC, wire.pack_ugc(item))):
        for msg in wire.build(kind, payload, fragment_size=64):
            records.append({"rec": "rx", "tomodachi_frame": msg.hex()})
    rag = {}
    containers = []
    for rec in records:
        msg = wire.parse(bytes.fromhex(rec["tomodachi_frame"]))
        key = msg["whole_crc"]
        rag.setdefault(key, wire.Reassembler())
        payload = rag[key].feed(msg)
        if payload is not None:
            containers.append((msg["kind"], payload))
    assert [(k, len(p)) for k, p in containers] == [(wire.KIND_MII, ltd.FULL_SIZE),
                                                    (wire.KIND_UGC, len(item.pack()))]
    assert ltd.LtdMii.parse(containers[0][1]).display_name == "Majima"


# --- the session constants -------------------------------------------------------------------


def test_the_title_id_is_the_one_the_store_and_checkpoint_agree_on():
    assert TITLE_ID == 0x010051F0207B2000


def test_the_expected_pia_header_band_is_version_11():
    assert PIA_VERSION == 11
    assert PIA_HEADER_SIZE == 0x1C


def test_the_pia_port_is_the_family_port_bdsp_measured():
    assert PIA_PORT == 12345


def test_the_passphrase_is_association_only_placeholder_not_a_finding():
    assert len(PASSPHRASE) == 64


def test_the_tag_oracle_separates_a_right_game_key_from_a_wrong_one():
    header = PiaHeader()
    datagram = PiaCrypto(SSID, game_key=GAME_KEY).encrypt(b"\xff" * 16, "10.0.0.9", header)
    assert SessionKeys(SSID, GAME_KEY).decrypt(datagram, "10.0.0.9") is not None
    wrong = SessionKeys(SSID, b"0123456789abcdef").decrypt(datagram, "10.0.0.9")
    assert wrong is None


def test_the_sweep_finds_the_key_that_encrypted_a_capture():
    header = PiaHeader()
    datagram = PiaCrypto(SSID, game_key=GAME_KEY).encrypt(b"\xab" * 16, "192.168.1.2", header)
    assert sweep_game_keys(datagram, "192.168.1.2", SSID) == [GAME_KEY]


def test_a_wrong_ssid_is_just_as_silent_as_a_wrong_key():
    header = PiaHeader()
    datagram = PiaCrypto(SSID, game_key=GAME_KEY).encrypt(b"\xff" * 16, "10.0.0.9", header)
    assert SessionKeys(bytes(range(1, 17)), GAME_KEY).decrypt(datagram, "10.0.0.9") is None


def test_the_candidate_list_carries_the_shared_pia_key():
    assert GAME_KEY_CANDIDATES[0] == b"p1frXqxmeCZWFv0X"
