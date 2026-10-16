"""Script execution is removed from the pair-only tray edition."""


def fire(*args, **kwargs):
    raise RuntimeError("Scripts y comandos deshabilitados en CleverSwitch Tray")


fire_switch = fire_connect = fire_disconnect = _run = fire
