"""Desktop geometry independent of native display APIs and display pixel scale."""

import dataclasses
import math


@dataclasses.dataclass(frozen=True)
class Display:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        values = (self.x, self.y, self.width, self.height)
        if not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 100000 for v in values):
            raise ValueError("Pantalla inválida")
        if not 32 <= self.width <= 100000 or not 32 <= self.height <= 100000:
            raise ValueError("Pantalla inválida")

    @property
    def right(self):
        return self.x + self.width - 1

    @property
    def bottom(self):
        return self.y + self.height - 1

    def contains(self, x, y):
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def clamp(self, x, y):
        return max(self.x, min(self.right, x)), max(self.y, min(self.bottom, y))


class Desktop:
    def __init__(self, displays):
        self.displays = tuple(displays)
        if not 1 <= len(self.displays) <= 16 or not all(isinstance(d, Display) for d in self.displays):
            raise ValueError("Escritorio inválido")
        self.left = min(d.x for d in self.displays)
        self.right = max(d.right for d in self.displays)
        self.top = min(d.y for d in self.displays)
        self.bottom = max(d.bottom for d in self.displays)

    def wire(self):
        return [dataclasses.asdict(d) for d in self.displays]

    @classmethod
    def from_wire(cls, value):
        if type(value) is not list or not 1 <= len(value) <= 16:
            raise ValueError("Escritorio inválido")
        if any(type(d) is not dict or set(d) != {"x", "y", "width", "height"} for d in value):
            raise ValueError("Escritorio inválido")
        try:
            return cls(Display(**d) for d in value)
        except (TypeError, OverflowError):
            raise ValueError("Escritorio inválido") from None

    def clamp(self, x, y):
        if not all(type(v) in (float, int) and math.isfinite(v) for v in (x, y)):
            raise ValueError("Posición inválida")
        # A bounding rectangle can include gaps. Project into an actual display.
        return min((d.clamp(x, y) for d in self.displays), key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)

    def entry(self, side, fraction):
        if side not in ("left", "right") or type(fraction) not in (int, float) or not 0 <= fraction <= 1:
            raise ValueError("Borde inválido")
        boundary = self.left if side == "left" else self.right
        candidates = [d for d in self.displays if (d.x if side == "left" else d.right) == boundary]
        y = self.top + fraction * (self.bottom - self.top)
        display = min(candidates, key=lambda d: abs(d.clamp(boundary, y)[1] - y))
        return display.clamp(boundary + (1 if side == "left" else -1), y)

    def crossing(self, side, x, y, dx):
        if side not in ("left", "right"):
            raise ValueError("Borde inválido")
        if (side == "left" and dx >= 0) or (side == "right" and dx <= 0):
            return None
        boundary = self.left if side == "left" else self.right
        at_edge = x <= boundary if side == "left" else x >= boundary
        if not at_edge or not any(
            d.y <= y <= d.bottom for d in self.displays if (d.x if side == "left" else d.right) == boundary
        ):
            return None
        return max(0.0, min(1.0, (y - self.top) / max(1, self.bottom - self.top)))

    def crossing_motion(self, side, x, y, dx, dy):
        """Locate a segment's edge crossing and preserve motion beyond it.

        Captured input has an unclamped destination. Its final y can lie outside
        a display even when the trajectory crossed a real edge earlier (or the
        opposite, across a gap). Use the intersection, not that final y.
        """
        if side not in ("left", "right"):
            raise ValueError("Borde inválido")
        if not dx:
            return None
        boundary = self.left if side == "left" else self.right
        portion = (boundary - x) / dx
        if not 0 <= portion <= 1:
            return None
        fraction = self.crossing(side, boundary, y + dy * portion, dx)
        if fraction is None:
            return None
        return fraction, dx * (1 - portion), dy * (1 - portion)
