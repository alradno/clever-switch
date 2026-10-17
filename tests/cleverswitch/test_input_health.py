import threading

import pytest

from cleverswitch.flow.input_health import Readiness


@pytest.mark.parametrize("active,limit", [(False, 151), (True, 601)])
def test_background_permission_checks_slow_down_locally_without_extending_trust(active, limit):
    now, checks = [0.0], []
    health = Readiness(lambda: checks.append(now[0]) or True, clock=lambda: now[0])
    assert health.ready()
    health.set_active(active)

    class ClockWait:
        def wait(self, delay):
            assert 0 < delay <= 0.4
            now[0] += delay + 1e-9
            if now[0] >= 60:
                health.stop.set()

        def clear(self):
            pass

    health.wake = ClockWait()
    health._run()
    assert limit - 2 <= len(checks) <= limit
    assert health.max_age == 0.5


def test_idle_readiness_demand_refreshes_early_and_never_blocks_the_input_thread():
    now = [1.0]
    entered, release = threading.Event(), threading.Event()
    health = Readiness(lambda: True, clock=lambda: now[0])
    assert health.ready()
    health.start()

    def slow():
        entered.set()
        release.wait(1)
        return True

    try:
        health.check = slow
        now[0] += 0.11
        assert health.ready()
        assert entered.wait(0.2)
        now[0] += 0.51
        assert not health.ready()
    finally:
        release.set()
        health.close()


def test_background_permission_exception_cannot_reuse_an_old_grant():
    health = Readiness(lambda: True)
    assert health.ready()

    def denied():
        raise PermissionError

    health.check = denied
    health._refresh()
    assert not health.ready()
