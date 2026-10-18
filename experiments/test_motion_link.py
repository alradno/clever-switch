"""Negotiation and bounded UDP routing, without native input or user pairing."""

from unittest.mock import Mock

import pytest

from experiments.motion_lane import Motion, Probe, lab_codecs
from experiments.motion_link import CAPABILITY, DatagramMux, Negotiation, supports_motion


def agreements():
    client, server = (Negotiation(role, "lab-link") for role in ("windows", "mac"))
    client.finish(server.accept(client.offer({CAPABILITY: 1})))
    return client, server


@pytest.mark.parametrize("capabilities", [None, {}, [], {CAPABILITY: True}, {CAPABILITY: 1.0}, {CAPABILITY: 2}])
def test_legacy_capability_needs_no_entropy_or_keys(capabilities, monkeypatch):
    entropy = Mock(side_effect=AssertionError("No entropy needed for old peers"))
    monkeypatch.setattr("cleverswitch.flow.input_motion_link.secrets.token_hex", entropy)
    client = Negotiation("windows", "lab-link")
    assert not supports_motion(capabilities)
    assert client.offer(capabilities) is None
    assert client.state == "reliable" and client.codec is None
    server = Negotiation("mac", "lab-link")
    assert server.accept(None) is None
    assert server.state == "reliable" and server.codec is None
    entropy.assert_not_called()


def test_directional_keys_are_retired_with_the_connection():
    client, server = agreements()
    codecs = [client.codec, server.codec]
    for source in (0, 1):
        packet = codecs[source].seal(Motion(1, 1, 0, (10, 20)))
        assert codecs[source].open(packet) is None
        assert codecs[1 - source].open(packet) == Motion(1, 1, 0, (10, 20))
    assert all(item.challenge is None and item.state == "ready" for item in (client, server))
    for agreement in (client, server):
        agreement.close()
        agreement.close()
        assert agreement.state == "closed" and agreement.codec is None
    for codec in codecs:
        with pytest.raises(ConnectionError):
            codec.seal(Motion(1, 2, 0, (30, 40)))
        assert codec.open(packet) is None


def test_new_connection_rejects_old_ciphertext_for_same_pairing_and_focus():
    first, second = agreements(), agreements()
    raw = first[0].codec.seal(Motion(1, 1, 0, (10, 20)))
    assert first[0].codec.session_id != second[0].codec.session_id
    assert second[1].codec.open(raw) is None
    assert second[1].codec.open(second[0].codec.seal(Motion(1, 1, 0, (30, 40)))).position == (30, 40)


def test_reply_replayed_across_upgrades_cannot_be_retried():
    first, second = (Negotiation("windows", "lab-link") for _ in range(2))
    server = Negotiation("mac", "lab-link")
    reply = server.accept(first.offer({CAPABILITY: 1}))
    second.offer({CAPABILITY: 1})
    with pytest.raises(ValueError, match="Invalid motion negotiation"):
        second.finish(reply)
    assert second.state == "closed" and second.codec is None
    with pytest.raises(RuntimeError):
        second.finish(reply)


def test_other_pairing_cannot_decrypt_reused_handshake_data():
    client, server = Negotiation("windows", "another-link"), Negotiation("mac", "lab-link")
    client.finish(server.accept(client.offer({CAPABILITY: 1})))
    assert server.codec.open(client.codec.seal(Probe(False, b"x" * 16))) is None


def test_peer_can_decline_without_leaving_ephemeral_state():
    client = Negotiation("windows", "lab-link")
    client.offer({CAPABILITY: 1})
    client.finish(None)
    assert client.state == "reliable" and client.challenge is client.codec is None


@pytest.mark.parametrize("role,identity", [("linux", "link"), ("mac", None), ("mac", ""), ("mac", "x" * 129)])
def test_invalid_identity(role, identity):
    with pytest.raises(ValueError):
        Negotiation(role, identity)


def test_roles_and_state_cannot_be_reused():
    client, server = agreements()
    for agreement, method, value in (
        (client, "offer", {CAPABILITY: 1}), (server, "accept", None),
        (client, "accept", None), (server, "finish", None),
    ):
        with pytest.raises(RuntimeError):
            getattr(agreement, method)(value)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("challenge", 0), ("challenge", "z" * 32),
    ("challenge", "a" * 30), ("challenge", "A" * 32), ("challenge", " " * 32), ("extra", 1),
])
def test_malformed_offer_retires_state_without_echoing_payload(field, value):
    server = Negotiation("mac", "lab-link")
    offer = {"version": 1, "challenge": "a" * 32, field: value}
    with pytest.raises(ValueError, match="^Invalid motion negotiation$"):
        server.accept(offer)
    assert server.state == "closed" and server.codec is None


@pytest.mark.parametrize("field,value", [
    ("version", False), ("version", 2), ("challenge", "b" * 32), ("secret", None),
    ("secret", "x" * 64), ("secret", "0" * 62), ("salt", "F" * 32), ("extra", 1),
])
def test_malformed_response_retires_state_without_echoing_payload(field, value):
    client, server = Negotiation("windows", "lab-link"), Negotiation("mac", "lab-link")
    reply = server.accept(client.offer({CAPABILITY: 1}))
    reply[field] = value
    with pytest.raises(ValueError, match="^Invalid motion negotiation$"):
        client.finish(reply)
    assert client.state == "closed" and client.codec is None


@pytest.mark.parametrize("payload", [[], "x", {}])
def test_non_object_negotiation(payload):
    client = Negotiation("windows", "lab-link")
    client.offer({CAPABILITY: 1})
    with pytest.raises(ValueError):
        client.finish(payload)
    with pytest.raises(ValueError):
        Negotiation("mac", "lab-link").accept(payload)


@pytest.fixture
def mux():
    discovery = Mock(return_value="discovery")
    router = DatagramMux(discovery)
    yield router, discovery
    router.close()


def route(router, codec, wake=None):
    return router.register(codec, ("192.168.1.34", 47655), "192.168.1.0/24", wake or Mock())


def test_one_bounded_inbox_and_one_wakeup_per_burst(mux):
    router, discovery = mux
    sender, receiver = lab_codecs()
    wake = Mock()
    inbox = route(router, receiver, wake)
    packets = [sender.seal(Motion(1, i, 0, (i, 10))) for i in range(1, 1001)]
    for raw in packets:
        assert router.receive(raw, inbox.peer)
    assert inbox.dropped == 936 and len(inbox.queue) == 64 and wake.call_count == 1
    batch = inbox.take()
    assert batch == tuple(packets[-64:]) and not inbox.take()
    assert receiver.received == 0  # The UDP listener never decrypts or injects.
    assert receiver.open(batch[-1]).position == (1000, 10)
    assert router.receive(sender.seal(Motion(1, 1001, 0, (1001, 10))), inbox.peer)
    assert wake.call_count == 2
    discovery.assert_not_called()


def test_exact_peer_port_session_and_header_before_waking(mux):
    router, discovery = mux
    sender, receiver = lab_codecs()
    wake = Mock()
    inbox = route(router, receiver, wake)
    raw = sender.seal(Motion(1, 1, 0, (10, 20)))
    for peer in (("192.168.1.35", 47655), ("192.168.1.34", 47656), ("8.8.8.8", 47655)):
        assert not router.receive(raw, peer)
    foreign, _ = lab_codecs()
    assert not router.receive(foreign.seal(Motion(1, 1, 0, (10, 20))), inbox.peer)
    for modified in (raw[:4] + bytes([2]) + raw[5:], raw[:21] + bytes(8) + raw[29:], raw[:-1], raw + b"x"):
        assert not router.receive(modified, inbox.peer)
    assert not inbox.put(b"BAD!" + raw[4:], inbox.peer)
    assert not inbox.put(bytearray(raw), inbox.peer)
    assert not router.receive(b"CSML" + b"x" * 1024, inbox.peer)
    assert not router.receive(None, inbox.peer)
    wake.assert_not_called()
    discovery.assert_not_called()


def test_bad_tag_does_not_change_owner_replay_state(mux):
    router, _ = mux
    sender, receiver = lab_codecs()
    inbox = route(router, receiver)
    raw = sender.seal(Motion(1, 1, 0, (10, 20)))
    assert router.receive(raw[:-1] + bytes([raw[-1] ^ 1]), inbox.peer)
    assert receiver.open(inbox.take()[0]) is None and receiver.received == 0
    assert router.receive(raw, inbox.peer)
    assert receiver.open(inbox.take()[0]).position == (10, 20)


def test_old_close_cannot_retire_replacement_and_pending_input_is_cleared(mux):
    router, _ = mux
    first, first_rx = lab_codecs()
    old = route(router, first_rx)
    assert router.receive(first.seal(Motion(1, 1, 0, (10, 20))), old.peer)
    sender, receiver = lab_codecs()
    current = route(router, receiver)
    assert old.closed and not old.take()
    router.unregister(old)
    assert router.current is current
    assert not old.put(first.seal(Motion(1, 2, 0, (20, 20))), old.peer)
    assert not router.receive(first.seal(Motion(1, 3, 0, (30, 20))), old.peer)
    assert router.receive(sender.seal(Motion(1, 1, 0, (40, 20))), current.peer)
    router.unregister(current)
    assert current.closed and not current.take() and router.current is None
    assert not router.receive(sender.seal(Motion(1, 2, 0, (50, 20))), current.peer)
    route(router, receiver)
    router.close()
    assert not router.receive(b"{}", current.peer)
    with pytest.raises(ConnectionError):
        route(router, receiver)


@pytest.mark.parametrize("peer,network", [
    (("8.8.8.8", 47655), "8.8.8.0/24"),
    (("192.168.2.34", 47655), "192.168.1.0/24"),
    (("192.168.1.34", 47655), "0.0.0.0/0"),
    (("192.168.1.34", 0), "192.168.1.0/24"),
    (("192.168.1.34", True), "192.168.1.0/24"),
])
def test_invalid_route_cannot_replace_current_connection(mux, peer, network):
    router, _ = mux
    _, codec = lab_codecs()
    current = route(router, codec)
    with pytest.raises(ValueError):
        router.register(codec, peer, network, Mock())
    assert router.current is current and not current.closed


def test_closed_codec_cannot_register(mux):
    router, _ = mux
    _, codec = lab_codecs()
    codec.close()
    with pytest.raises(ValueError):
        route(router, codec)


def test_discovery_retains_own_authentication_and_ephemeral_sender_port(mux):
    router, discovery = mux
    assert router.receive(b'{"notice":"synthetic"}', ("192.168.1.34", 20000)) == "discovery"
    discovery.assert_called_once_with(b'{"notice":"synthetic"}', "192.168.1.34")


def test_wakeup_outside_lock_can_take_immediately(mux):
    router, _ = mux
    sender, receiver = lab_codecs()
    taken = []
    inbox = route(router, receiver, lambda: taken.extend(inbox.take()))
    raw = sender.seal(Motion(1, 1, 0, (10, 20)))
    assert router.receive(raw, inbox.peer)
    assert taken == [raw] and not inbox.take()
