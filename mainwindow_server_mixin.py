from __future__ import annotations

from PyQt6.QtGui import QAction

from dialogs import RemoteDiagnosticsDialog


class MainWindowServerMixin:
    def _build_server_menu(self) -> None:
        menu_bar = self.menuBar()
        server_menu = menu_bar.addMenu("Server")

        start_action = QAction("Connect", self)
        start_action.triggered.connect(self._on_server_start)
        server_menu.addAction(start_action)

        stop_action = QAction("Disconnect", self)
        stop_action.triggered.connect(self._on_server_stop)
        server_menu.addAction(stop_action)

        restart_action = QAction("Reconnect", self)
        restart_action.triggered.connect(self._on_server_restart)
        server_menu.addAction(restart_action)

        server_menu.addSeparator()

        diagnostics_action = QAction("Diagnostics...", self)
        diagnostics_action.triggered.connect(self._on_server_diagnostics)
        server_menu.addAction(diagnostics_action)

    def _on_server_start(self) -> None:
        if self._remote_manager.is_running():
            self._status.showMessage("Already connecting/connected to the remote-control server.")
            return
        self._start_remote_server(announce=True)

    def _on_server_stop(self) -> None:
        if not self._remote_manager.is_running():
            self._status.showMessage("Not connected to a remote-control server.")
            return
        self._remote_manager.stop()
        self._status.showMessage("Disconnected from remote-control server.")

    def _on_server_restart(self) -> None:
        if self._remote_manager.restart():
            self._status.showMessage(f"Reconnecting to {self._server_address}...")
        else:
            self._status.showMessage("Failed to reconnect (see Server > Diagnostics).")

    def _on_server_diagnostics(self) -> None:
        if getattr(self, "_remote_diagnostics_dialog", None) is None:
            self._remote_diagnostics_dialog = RemoteDiagnosticsDialog(
                self._remote_server_diagnostics_snapshot,
                self,
            )
        self._remote_diagnostics_dialog.show()
        self._remote_diagnostics_dialog.raise_()
        self._remote_diagnostics_dialog.activateWindow()

    def _remote_server_diagnostics_snapshot(self) -> dict:
        snapshot = self._remote_diagnostics.snapshot()
        snapshot["running"] = self._remote_manager.is_running()
        snapshot["address"] = self._server_address
        return snapshot


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
