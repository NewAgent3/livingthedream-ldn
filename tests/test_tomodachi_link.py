"""The link half: the joiner's constants and the decrypting recorder.

The recorder is the whole bring-up story in one object: raw datagrams in, a JSONL trace out, and
the game key fixed by the first datagram a candidate authenticates. These tests run the recorder
against packets encrypted with the known candidate key, so the oracle path - sweep, hit, lock-in,
every later packet decrypted under the key it found - is exercised end to end without a console.
"""
import json

from pokeldn.ldn.crypto import PiaCrypto, PiaHeader
from pokeldn.tomodachi.link import Recorder, TomoTransport, open_trace
from pokeldn.tomodachi.session import GAME_KEY_CANDIDATES, PASSPHRASE, TITLE_ID

GAME_KEY = GAME_KEY_CANDIDATES[0]
SSID = bytes(range(16))


def a_datagram(payload=b"\x01\x02\x03\x04", src="10.0.0.9", key=GAME_KEY):
    return PiaCrypto(SSID, game_key=key).encrypt(payload, src, PiaHeader()), src


def test_the_recorder_fixes_the_key_on_the_first_authenticated_datagram(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    datagram, src = a_datagram()
    rec.datagram(datagram, src)
    assert rec.keys == [GAME_KEY] and rec.authenticated == 1
    records = open_trace(trace)
    assert records[0]["rec"] == "udp_in"
    assert GAME_KEY.decode() in (records[0].get("game_key"), )
    assert records[0]["plain_hex"]


def test_every_later_datagram_is_decrypted_under_the_locked_key(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    for i in range(5):
        rec.datagram(*a_datagram(payload=bytes([i] * 8)))
    assert rec.authenticated == 5 and len(rec.keys) == 1
    records = open_trace(trace)
    assert sum(1 for r in records if r.get("plain_hex")) == 5


def test_an_unauthenticated_datagram_is_recorded_raw_and_silent(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    rec.datagram(b"\xde\xad\xbe\xef" * 8, "10.0.0.9")
    assert rec.keys == [] and rec.authenticated == 0
    record = open_trace(trace)[0]
    assert record["hex"] and "plain_hex" not in record


def test_the_advert_record_carries_the_send_paths_constants(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    t = TomoTransport(log=lambda *_: None)
    t.app_data = b"PIA-HEADER-BLOCK" + b"\x00" * 10
    t.LOCAL_COMMUNICATION_ID = 0x010051F0207B2000
    rec.advert(t)
    record = open_trace(trace)[0]
    assert record["rec"] == "advert"
    assert record["local_communication_id"] == "010051f0207b2000"
    assert record["app_data_hex"] == t.app_data.hex()


def test_a_trace_holds_the_advert_and_the_datagrams(tmp_path):
    """Together these are everything a future sender needs but the LDN passphrase."""
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    t = TomoTransport(log=lambda *_: None)
    t.app_data = b"\x01" * 4
    rec.advert(t)
    rec.datagram(*a_datagram())
    kinds = [r["rec"] for r in open_trace(trace)]
    assert kinds == ["advert", "udp_in"]
    assert "local_communication_id" in open_trace(trace)[0]
    assert "plain_hex" in open_trace(trace)[1]


def test_a_wrong_key_never_authenticates(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    wrong = b"\x00" * 16
    assert wrong not in GAME_KEY_CANDIDATES
    rec.datagram(*a_datagram(key=wrong))
    assert rec.keys == []


def test_notes_and_udp_records_share_one_jsonl(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    rec.note("session", ssid=SSID.hex(), our_ip="169.254.21.2")
    rec.datagram(*a_datagram())
    records = open_trace(trace)
    assert [r["rec"] for r in records] == ["session", "udp_in"]
    assert json.dumps(records[0])  # it is all JSONL


def test_the_joiner_defaults_to_the_title_passphrase_and_keeps_the_transport_api():
    kwargs = {}
    kwargs.setdefault("password", PASSPHRASE)
    t = TomoTransport(local_comm_id=None, log=lambda *_: None, **kwargs)
    assert t.password == PASSPHRASE
    assert TomoTransport.LOCAL_COMMUNICATION_ID          # inherited; a --comm-id can pin it
    assert (TITLE_ID & ~0xFFFFF)


def test_the_trace_is_openable_by_the_replay_path(tmp_path):
    trace = str(tmp_path / "t.jsonl")
    rec = Recorder(trace, SSID, "169.254.21.2")
    rec.datagram(*a_datagram())
    rec.note("session", ssid=SSID.hex())
    lines = [json.loads(line) for line in open(trace)]
    assert len(lines) == 2 and open_trace(trace) == lines
