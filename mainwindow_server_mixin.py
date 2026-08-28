from __future__ import annotations

from PyQt6.QtGui import QAction

from dialogs import RemoteDiagnosticsDialog


class MainWindowServerMixin:
    def _build_server_menu(self) -> None:
        menu_bar = self.menuBar()
        server_menu = menu_bar.addMenu("Server")

        start_action = QAction("Start Server", self)
        start_action.triggered.connect(self._on_server_start)
        server_menu.addAction(start_action)

        stop_action = QAction("Stop Server", self)
        stop_action.triggered.connect(self._on_server_stop)
        server_menu.addAction(stop_action)

        restart_action = QAction("Restart Server", self)
        restart_action.triggered.connect(self._on_server_restart)
        server_menu.addAction(restart_action)

        server_menu.addSeparator()

        diagnostics_action = QAction("Diagnostics...", self)
        diagnostics_action.triggered.connect(self._on_server_diagnostics)
        server_menu.addAction(diagnostics_action)

    def _on_server_start(self) -> None:
        if self._remote_manager.is_running():
            self._status.showMessage("Remote server is already running.")
            return
        self._start_remote_server(announce=True)

    def _on_server_stop(self) -> None:
        if not self._remote_manager.is_running():
            self._status.showMessage("Remote server is not running.")
            return
        self._remote_manager.stop()
        self._status.showMessage("Remote server stopped.")

    def _on_server_restart(self) -> None:
        if self._remote_manager.restart(self._server_port):
            self._status.showMessage(f"Remote server restarted on port {self._server_port}.")
        else:
            self._status.showMessage(f"Remote server failed to restart: {self._remote_manager.last_error()}")

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
        snapshot["port"] = self._remote_manager.port()
        snapshot["last_error"] = self._remote_manager.last_error()
        return snapshot


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch app.py to start JingleAllTheDay.")
    raise SystemExit(1)
