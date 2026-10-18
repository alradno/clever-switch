"""A local cancellation must finish cleanup after its native lock is released."""

import select
import threading

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_click_sequence import physical_takeover
from .test_input_end_to_end import enter, key
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("action", ["key", "button", "wheel", "motion"])
@pytest.mark.parametrize("native_peers", [False], indirect=True, ids=["reliable"])
def test_failed_release_is_not_parked_when_idle_tick_races_the_native_callback(native_peers, source, action):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    until(lambda: receiver.session.buttons == {0})
    cancelled, unlock, checked = threading.Event(), threading.Event(), threading.Event()
    cancel, tick, inject = receiver.session.cancel, receiver.channel.on_tick, receiver.backend.inject
    failures, callback_errors = [], []

    def refuse_once(packet):
        if packet.kind == Kind.BUTTON and packet.values == (0, 0) and not failures:
            failures.append(True)
            raise OSError("Simulated release refusal")
        return inject(packet)

    def pause_return(reason):
        cancel(reason)
        cancelled.set()
        assert unlock.wait(2)

    def observe_tick():
        tick()
        if (
            cancelled.is_set() and sender.session.phase == "local"
            and not receiver.channel.wirebox.pending()
            and not select.select([receiver.channel.wake_reader], [], [], 0)[0]
        ):
            # This tick consumed the wake sent inside cancel, but could not
            # inspect cleanup because the native callback still owns its lock.
            checked.set()

    receiver.backend.inject = refuse_once
    receiver.session.cancel = pause_return
    receiver.channel.on_tick = observe_tick

    def native_callback():
        try:
            key(receiver, 4, 1) if action == "key" else physical_takeover(receiver, action)
        except BaseException as error:
            callback_errors.append(error)

    worker = threading.Thread(target=native_callback)
    worker.start()
    try:
        assert cancelled.wait(1)
        until(lambda: sender.session.phase == "local")
        receiver.channel._wake()
        assert checked.wait(1)
    finally:
        unlock.set()
        worker.join(1)
    assert not worker.is_alive() and not callback_errors
    until(lambda: all(peer.channel.finished.is_set() for peer in peers), timeout=0.5)
    assert receiver.channel.failure == "cleanup"
    assert failures and receiver.session.unreleased == {(Kind.BUTTON, 0)}
    assert receiver.session.retry_releases()
    assert not receiver.session.unreleased
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
