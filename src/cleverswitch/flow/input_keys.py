"""Physical key positions: USB HID usages to public macOS/Windows key codes.

No character translation or text is performed. Each destination keeps its own
keyboard layout, dead-key composition and application shortcuts. Mac values are
the kVK constants in HIToolbox/Events.h; Windows values are set-1 scan codes.
"""

MAC = dict(
    zip(
        range(4, 30),
        [0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6],
        strict=True,
    )
)
MAC.update(zip(range(30, 40), [18, 19, 20, 21, 23, 22, 26, 28, 25, 29], strict=True))
MAC.update(
    {
        40: 36,
        41: 53,
        42: 51,
        43: 48,
        44: 49,
        45: 27,
        46: 24,
        47: 33,
        48: 30,
        49: 42,
        51: 41,
        52: 39,
        53: 50,
        54: 43,
        55: 47,
        56: 44,
        57: 57,
        73: 114,
        74: 115,
        75: 116,
        76: 117,
        77: 119,
        78: 121,
        79: 124,
        80: 123,
        81: 125,
        82: 126,
        83: 71,
        84: 75,
        85: 67,
        86: 78,
        87: 69,
        88: 76,
        89: 83,
        90: 84,
        91: 85,
        92: 86,
        93: 87,
        94: 88,
        95: 89,
        96: 91,
        97: 92,
        98: 82,
        99: 65,
        100: 10,
        101: 110,
        103: 81,
    }
)
MAC.update(zip(range(58, 70), [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111], strict=True))
MAC.update(zip(range(104, 112), [105, 107, 113, 106, 64, 79, 80, 90], strict=True))
MAC.update(zip(range(0xE0, 0xE8), [59, 56, 58, 55, 62, 60, 61, 54], strict=True))
MAC_REVERSE = {native: usage for usage, native in MAC.items()}
# Device-specific flags distinguish releasing one Shift/Ctrl while the other is
# still held. The combined modifier bit alone cannot express this.
MAC_DEVICE_FLAGS = dict(zip(range(0xE0, 0xE8), [1, 2, 32, 8, 8192, 4, 64, 16], strict=True))
MAC_MODIFIER_FLAGS = dict(zip(range(0xE0, 0xE8), [1 << 18, 1 << 17, 1 << 19, 1 << 20] * 2, strict=True))

WINDOWS = dict(
    zip(
        range(4, 30),
        [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44],
        strict=True,
    )
)
WINDOWS.update(zip(range(30, 40), range(2, 12), strict=True))
WINDOWS.update(
    {
        40: 28,
        41: 1,
        42: 14,
        43: 15,
        44: 57,
        45: 12,
        46: 13,
        47: 26,
        48: 27,
        49: 43,
        51: 39,
        52: 40,
        53: 41,
        54: 51,
        55: 52,
        56: 53,
        57: 58,
        68: 87,
        69: 88,
        70: 0x137,
        71: 70,
        73: 0x152,
        74: 0x147,
        75: 0x149,
        76: 0x153,
        77: 0x14F,
        78: 0x151,
        79: 0x14D,
        80: 0x14B,
        81: 0x150,
        82: 0x148,
        83: 0x145,
        84: 0x135,
        85: 55,
        86: 74,
        87: 78,
        88: 0x11C,
        89: 79,
        90: 80,
        91: 81,
        92: 75,
        93: 76,
        94: 77,
        95: 71,
        96: 72,
        97: 73,
        98: 82,
        99: 83,
        100: 86,
        101: 0x15D,
        103: 89,
    }
)
WINDOWS.update(zip(range(58, 68), range(59, 69), strict=True))
WINDOWS.update(zip(range(104, 116), range(100, 112), strict=True))
WINDOWS[115] = 0x76  # F24 is not contiguous with the F13-F23 set-1 scan codes.
WINDOWS.update(zip(range(0xE0, 0xE8), [29, 42, 56, 0x15B, 0x11D, 54, 0x138, 0x15C], strict=True))
WINDOWS_REVERSE = {native: usage for usage, native in WINDOWS.items()}

# A marker on our public OS events prevents the capture path from retransmitting
# them. It is loop prevention, not an attempt to hide synthetic input from OS/EDR.
EVENT_TAG = 0x4353464C
