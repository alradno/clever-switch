import json
import sys
from unittest.mock import Mock

import pytest

from cleverswitch.flow import input_check
from cleverswitch.tray import app


@pytest.mark.parametrize(
    "listen,post,secure,session,ready",
    [
        (True, True, False, True, True),
        (False, True, False, True, False),
        (True, False, False, True, False),
        (True, True, True, True, False),
        (True, True, False, False, False),
    ],
)
def test_mac_check_reports_each_requirement_without_starting_capture(listen, post, secure, session, ready, mocker):
    backend = Mock()
    backend.q.CGPreflightListenEventAccess.return_value = listen
    backend.q.CGPreflightPostEventAccess.return_value = post
    backend.secure_input.return_value = secure
    backend._ready.return_value = session
    mocker.patch.object(sys, "platform", "darwin")
    factory = mocker.patch.object(input_check, "native_input", return_value=backend)
    report = input_check.inspect()
    assert report["ready"] is ready
    assert report["checks"] == {
        "listen_permission": listen,
        "post_permission": post,
        "session_and_accessibility": session,
        "secure_input_inactive": not secure,
    }
    factory.assert_called_once_with()
    assert not backend.start.called and not backend.inject.called and not backend.capture.called
    assert not backend.q.CGRequestListenEventAccess.called and not backend.q.CGRequestPostEventAccess.called
    assert "CGEvent" not in input_check.message(report)


@pytest.mark.parametrize("ready", [True, False])
def test_windows_check_reports_session_without_hooks_or_elevation(ready, mocker):
    backend = Mock()
    backend.ready.return_value = ready
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(input_check, "native_input", return_value=backend)
    report = input_check.inspect()
    assert report["ready"] is ready
    assert report["checks"] == {"interactive_session": ready}
    backend.start.assert_not_called()
    backend.inject.assert_not_called()
    assert "Windows" in input_check.message(report) or ready


def test_failed_check_does_not_leak_exception_details(mocker):
    mocker.patch.object(input_check, "native_input", side_effect=OSError("private details"))
    report = input_check.inspect()
    assert report["ready"] is False and report["error"] == "OSError"
    assert "private details" not in json.dumps(report)


def test_packaged_check_writes_report_without_starting_app_or_reading_pairing(tmp_path, mocker):
    destination = tmp_path / "input-check.json"
    mocker.patch.object(sys, "argv", ["CleverSwitchTray", "--input-check", str(destination)])
    report = {"ready": True, "checks": {"interactive_session": True}}
    mocker.patch.object(input_check, "inspect", return_value=report)
    run = mocker.patch.object(app, "run")
    instance = mocker.patch.object(app, "single_instance")
    pairing = mocker.patch("cleverswitch.flow.identity.Pairing")
    app.main()
    assert json.loads(destination.read_text()) == report
    run.assert_not_called()
    instance.assert_not_called()
    pairing.assert_not_called()


def test_input_check_and_preview_cannot_be_combined(tmp_path, mocker):
    mocker.patch.object(sys, "argv", ["CleverSwitchTray", "--preview", "--input-check", str(tmp_path / "check")])
    inspect = mocker.patch.object(input_check, "inspect")
    with pytest.raises(SystemExit):
        app.main()
    inspect.assert_not_called()
