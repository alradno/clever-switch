import pytest

from cleverswitch.flow.cursor import Edge, Sample


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("nearby", ["arrival", "jitter", "outside", "unsafe-interior"])
def test_arrival_cannot_rearm_edge_without_safe_inward_movement(side, nearby):
    now = [0.0]
    edge = Edge(side, clock=lambda: now[0])

    def sample(distance, safe=True):
        x = 100 + distance if side == "left" else 1099 - distance
        return Sample(x, 400, 100, 100, 1000, 800, safe)

    edge.cooldown()
    distance = {"arrival": 24, "jitter": 2, "outside": -5, "unsafe-interior": 100}[nearby]
    assert edge.poll(sample(distance, safe=nearby != "unsafe-interior")) is None
    now[0] = 3.0
    assert edge.poll(sample(0)) is None
    now[0] = 3.5
    assert edge.poll(sample(0)) is None  # Time alone cannot rearm a parked cursor.
    assert edge.poll(sample(100)) is None
    assert edge.poll(sample(0)) is None
    now[0] = 4.0
    assert edge.poll(sample(0)) == pytest.approx(300 / 800)


@pytest.mark.parametrize("side", ["left", "right"])
def test_completed_edge_trigger_requires_inward_movement_before_next_trigger(side):
    now = [0.0]
    edge = Edge(side, clock=lambda: now[0])

    def sample(distance):
        return Sample(distance if side == "left" else 999 - distance, 400, 0, 0, 1000, 800)

    assert edge.poll(sample(0)) is None
    now[0] = 0.5
    assert edge.poll(sample(0)) == 0.5
    assert edge.poll(sample(2)) is None
    assert edge.poll(sample(0)) is None
    now[0] = 1.0
    assert edge.poll(sample(0)) is None
    assert edge.poll(sample(100)) is None
    assert edge.poll(sample(0)) is None
    now[0] = 1.5
    assert edge.poll(sample(0)) == 0.5
