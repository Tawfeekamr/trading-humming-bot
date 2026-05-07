"""Minimal health check HTTP endpoint for Docker/Railway liveness probes."""
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_bot_state = {"status": "starting", "grid_state": "UNKNOWN", "last_tick": None}


def update_health(grid_state: str = "ACTIVE") -> None:
    _bot_state["status"] = "ok"
    _bot_state["grid_state"] = grid_state
    _bot_state["last_tick"] = datetime.now(timezone.utc).isoformat()


def set_halted(reason: str = "circuit_breaker") -> None:
    _bot_state["status"] = "halted"
    _bot_state["grid_state"] = reason


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if _bot_state["status"] == "ok" else 503
        body = json.dumps(_bot_state).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress access logs


def start_health_server(port: int = 8080) -> None:
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check endpoint running on :{port}/")
