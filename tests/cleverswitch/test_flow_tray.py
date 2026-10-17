import sys
from unittest.mock import Mock

import pytest

from cleverswitch.tray import app


def test_windows_firewall_menu_repairs_then_reconnects_and_reports_status(mocker):
    captured = {}
    controller = Mock(context=None, paused=False, flow=Mock(enabled=False), installation={"state": "ready"})
    controller.flow.diagnostic.return_value = {"mode": "standby"}
    mocker.patch.object(app, "Controller", return_value=controller)
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(sys, "frozen", False, create=True)
    mocker.patch.object(app.startup, "is_enabled", return_value=False)
    mocker.patch("cleverswitch.tray.actions.threading.Thread.start", lambda self: self.run())
    notice = mocker.patch("cleverswitch.tray.dialogs.message")
    icon = Mock()

    def create(*args):
        captured["menu"] = args[3]
        return icon

    mocker.patch("pystray.Icon", side_effect=create)

    def exercise(**kwargs):
        menu = {item.text: item for item in captured["menu"]}
        menu["Revisar firewall de Windows…"](icon)
        controller.check_installation.assert_called_with(repair=True)
        controller.flow.reconnect_now.assert_called_once()
        menu["Ver diagnóstico…"](icon)
        assert '"installation"' in notice.call_args.args[1]
        assert '"ready"' in notice.call_args.args[1]

    icon.run.side_effect = exercise
    app.run()


@pytest.mark.parametrize("case", ["preview", "windows-new", "windows-response", "mac", "existing", "multiple-nics"])
def test_tray_pairing_menu_and_actions_never_start_hardware(case, tmp_path, mocker):
    captured = {}
    controller = Mock(context=None, paused=False, flow=Mock(enabled=False))
    controller.status.return_value = "Estado"
    controller.device_status.return_value = "Dispositivo"
    controller.flow.status.return_value = "Enlace"
    mocker.patch.object(app, "Controller", return_value=controller)
    icon = Mock()

    def create(*args):
        captured["menu"] = args[3]
        return icon

    mocker.patch("pystray.Icon", side_effect=create)
    mocker.patch.object(app.startup, "is_enabled", return_value=False)
    startup = mocker.patch.object(app.startup, "set_enabled")
    networks = mocker.patch(
        "cleverswitch.flow.identity.local_addresses", return_value=[("wifi", "192.168.1.2", "192.168.1.0/24")]
    )
    pairing = Mock()
    config = None
    if case == "windows-response":
        config = {"role": "windows"}
    elif case == "existing":
        config = {"peer": {"address": "192.168.1.3"}}
    pairing.load.return_value = config
    mocker.patch("cleverswitch.flow.identity.Pairing", return_value=pairing)
    files = mocker.patch("cleverswitch.tray.dialogs.choose_file", return_value=tmp_path / "enlace.json")
    notice = mocker.patch("cleverswitch.tray.dialogs.message")
    mocker.patch.object(sys, "platform", "darwin" if case == "mac" else "win32")
    # Deterministic dispatch here; real asynchronous behavior is covered separately.
    mocker.patch("cleverswitch.tray.actions.threading.Thread.start", lambda self: self.run())

    def exercise(**kwargs):
        menu = {item.text: item for item in captured["menu"]}
        assert "Cambio por bordes" in menu
        assert "Traer ratón y teclado aquí" in menu
        if case == "preview":
            assert not menu["Cambio por bordes"].enabled
            networks.assert_not_called()
            startup.assert_not_called()
            files.assert_not_called()
            return
        # The network choice is an actual submenu; a callable action cannot open it.
        network = menu["Red local"].submenu
        assert network is not None
        if case == "multiple-nics":
            networks.return_value *= 2
            menu["Vincular Windows 1 y Mac 2…"](icon)
            files.assert_not_called()
            assert "Selecciona" in notice.call_args.args[0]
            return
        menu["Vincular Windows 1 y Mac 2…"](icon)
        if case == "windows-new":
            pairing.create_invitation.assert_called_once_with(tmp_path / "enlace.json", "192.168.1.2", "192.168.1.0/24")
        elif case == "windows-response":
            pairing.accept_response.assert_called_once()
        elif case == "mac":
            pairing.accept_invitation.assert_called_once()
        else:
            files.assert_not_called()
        menu["Cambio por bordes"](icon)
        menu["Guardar enlace para Mac…"](icon)
        pairing.export_invitation.assert_called_once()
        controller.flow.toggle_edges.assert_called_once()
        menu["Traer ratón y teclado aquí"](icon)
        controller.flow.recover.assert_called_once()
        next(iter(network))(icon)
        pairing.load.return_value = {"role": "windows"}
        menu["Actualizar la red del enlace"](icon)
        assert pairing.save.call_args.args[0]["address"] == "192.168.1.2"
        menu["Pausar cambio automático"](icon)
        controller.toggle_pause.assert_called_once()
        menu["Iniciar al entrar en la sesión"](icon)
        startup.assert_called_once_with(True)
        menu["Salir"](icon)
        icon.stop.assert_called_once()

    icon.run.side_effect = exercise
    app.run(preview=case == "preview")


def test_controller_starts_and_stops_flow_independently(mocker):
    controller = app.Controller()
    controller.context = Mock()
    first = Mock()
    controller.flow = first
    second = Mock()
    mocker.patch("cleverswitch.flow.runtime.Runtime", return_value=second)
    controller.start_flow()
    first.close.assert_called_once()
    second.start.assert_called_once()
    controller.stop()
    second.close.assert_called_once()
    controller.context.shutdown.set.assert_called_once()


def test_shutdown_signal_wakes_passive_discovery_and_tray(mocker):
    import threading
    from types import SimpleNamespace

    from cleverswitch.policy import PairPolicy

    ctx = SimpleNamespace(policy=PairPolicy(), shutdown=threading.Event())
    entered = threading.Event()

    def discover(context):
        entered.set()
        context.policy.wait_background(context.shutdown)

    mocker.patch("cleverswitch.setup.app_setup.setup_context", return_value=ctx)
    mocker.patch("cleverswitch.discovery.discovery.discover", side_effect=discover)
    controller = app.Controller()
    mocker.patch.object(controller, "start_flow")
    controller.start()
    try:
        assert entered.wait(1)
        ctx.shutdown.set()  # The production SIGTERM handler sets this event.
        controller.worker.join(1)
        assert not controller.worker.is_alive()
        assert controller.refresh_event.wait(1)
    finally:
        controller.stop()
