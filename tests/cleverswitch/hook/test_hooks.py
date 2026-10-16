"""The tray fork has no executable hooks, even through its legacy interface."""

import pytest

from cleverswitch.hook import hooks


@pytest.mark.parametrize("method", ["fire", "fire_switch", "fire_connect", "fire_disconnect", "_run"])
def test_all_legacy_hook_routes_fail_closed(method):
    with pytest.raises(RuntimeError, match="deshabilitados"):
        getattr(hooks, method)("arbitrary command", {})
