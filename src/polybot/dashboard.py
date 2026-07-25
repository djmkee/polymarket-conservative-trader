import json
import threading
import webbrowser
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .config import Settings
from .engine import Engine
from .market_maker import PaperMarketMaker
from .store import AuditStore


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Settings):
        super().__init__(address, DashboardHandler)
        self.settings = settings
        self.stream_running = False
        store = AuditStore(settings.db_path)
        try:
            store.init_paper_account(str(settings.initial_equity))
        finally:
            store.close()


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        if self.path == "/":
            content = Path(__file__).with_name("dashboard.html").read_bytes()
            self._send(content, "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            store = AuditStore(self.server.settings.db_path)
            try:
                state = store.dashboard_state()
            finally:
                store.close()
            self._json(state)
            return
        if self.path == "/api/health":
            self._json(
                {
                    "mode": self.server.settings.mode,
                    "stream_running": self.server.stream_running,
                }
            )
            return
        self._json({"detail": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        prefix, suffix = "/api/positions/", "/close"
        if not self.path.startswith(prefix) or not self.path.endswith(suffix):
            self._json({"detail": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        token_id = unquote(self.path[len(prefix) : -len(suffix)])
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"detail": "Invalid JSON request."}, HTTPStatus.BAD_REQUEST)
            return
        if request.get("confirm") is not True:
            self._json(
                {"detail": "Explicit confirmation is required."},
                HTTPStatus.BAD_REQUEST,
            )
            return
        shares: Decimal | None = None
        if request.get("shares") is not None:
            try:
                shares = Decimal(str(request["shares"]))
            except (InvalidOperation, TypeError, ValueError):
                self._json({"detail": "Invalid share amount."}, HTTPStatus.BAD_REQUEST)
                return
        store = AuditStore(self.server.settings.db_path)
        try:
            result = PaperMarketMaker(
                self.server.settings, store
            ).manual_close(token_id, shares)
        except ValueError as exc:
            self._json({"detail": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        finally:
            store.close()
        self._json({"ok": True, "result": result})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(
        self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self._send(
            json.dumps(payload, default=str).encode(),
            "application/json; charset=utf-8",
            status,
        )

    def _send(
        self,
        content: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(content)


async def run_dashboard(
    settings: Settings,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = DashboardServer((host, port), settings)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    engine = Engine(settings)
    server.stream_running = True
    try:
        await engine.stream()
    finally:
        server.stream_running = False
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        await engine.close()
