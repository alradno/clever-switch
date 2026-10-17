"""Unit tests for setup/app_setup.py."""

from __future__ import annotations

import argparse
import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.model.context.app_context import AppContext


def _cli_args(**overrides):
    defaults = {"config": None, "verbose": False, "verbose_extra": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_setup_context_returns_app_context(mocker):
    mocker.patch("cleverswitch.setup.app_setup.check")
    mocker.patch("cleverswitch.setup.app_setup.cfg_module.load")
    mocker.patch("cleverswitch.setup.app_setup.signal.signal")

    from cleverswitch.setup.app_setup import setup_context

    ctx = setup_context(_cli_args())
    assert isinstance(ctx, AppContext)
    assert ctx.device_registry is not None
    assert ctx.topics.hid_event is not None
    assert ctx.topics.write is not None
    assert ctx.topics.device_info is not None
    assert ctx.topics.flags is not None


def test_setup_context_exits_on_config_error(mocker):
    from cleverswitch.errors.errors import ConfigError

    mocker.patch("cleverswitch.setup.app_setup.check")
    mocker.patch("cleverswitch.setup.app_setup.cfg_module.load", side_effect=ConfigError("bad"))
    mocker.patch("cleverswitch.setup.app_setup.signal.signal")

    from cleverswitch.setup.app_setup import setup_context

    with pytest.raises(SystemExit) as exc:
        setup_context(_cli_args())
    assert exc.value.code == 1


def test_setup_context_initializes_subscribers(mocker):
    mocker.patch("cleverswitch.setup.app_setup.check")
    mocker.patch("cleverswitch.setup.app_setup.cfg_module.load")
    mocker.patch("cleverswitch.setup.app_setup.signal.signal")

    mock_init = mocker.patch("cleverswitch.setup.app_setup._init_subscribers")

    from cleverswitch.setup.app_setup import setup_context

    setup_context(_cli_args())
    mock_init.assert_called_once()


@pytest.mark.parametrize("platform,native_loop", [("darwin", True), ("darwin", False), ("win32", True)])
def test_shutdown_registration_matches_the_platform_event_loop(mocker, platform, native_loop):
    from cleverswitch.setup.app_setup import _setup_shutdown

    ordinary = mocker.patch("cleverswitch.setup.app_setup.signal.signal")
    native = Mock()
    mocker.patch.object(sys, "platform", platform)
    mocker.patch.dict(sys.modules, {"PyObjCTools": SimpleNamespace(MachSignals=SimpleNamespace(signal=native))})
    event = _setup_shutdown(native_loop=native_loop)
    selected, unused = (native, ordinary) if platform == "darwin" and native_loop else (ordinary, native)
    unused.assert_not_called()
    handlers = {call.args[0]: call.args[1] for call in selected.call_args_list}
    assert signal.SIGTERM in handlers
    assert not event.is_set()
    handlers[signal.SIGTERM](signal.SIGTERM)
    assert event.is_set()
