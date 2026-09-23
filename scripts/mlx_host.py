"""Loopback-only host companion for starting the native MLX server from Docker."""

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = Path.home() / ".local/share/boundless/models"
HOST = "127.0.0.1"
PORT = 8091
MLX_PORT = 8088


def mlx_available() -> bool:
    return platform.system() == "Darwin" and platform.machine().casefold() in {"arm64", "aarch64"}


def installed_models(root: Path) -> list[dict[str, str]]:
    if not root.is_dir():
        return []
    models = []
    resolved_root = root.resolve()
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root) or not resolved.is_dir():
            continue
        if not (resolved / "config.json").is_file() or not any(resolved.glob("*.safetensors")):
            continue
        models.append({"id": str(resolved), "name": path.name})
    return models


def server_running() -> bool:
    try:
        with urlopen(f"http://{HOST}:{MLX_PORT}/health", timeout=0.7) as response:
            payload = json.load(response)
        return payload.get("status") == "ok"
    except (OSError, URLError, ValueError, AttributeError):
        return False


def server_command() -> str | None:
    installed = shutil.which("mlx_lm.server")
    if installed:
        return installed
    for path in (ROOT / ".uv-tools-bin/mlx_lm.server", ROOT / "backend/.venv/bin/mlx_lm.server"):
        if path.is_file():
            return str(path)
    return None


def launcher_ready() -> bool:
    try:
        with urlopen(f"http://{HOST}:{PORT}/status", timeout=0.5) as response:
            return json.load(response).get("service") == "boundless-mlx-host"
    except (OSError, URLError, ValueError, AttributeError):
        return False


class Launcher:
    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self.lock = Lock()
        self.process: subprocess.Popen | None = None
        self.selected_model: str | None = None

    def status(self) -> dict:
        running = server_running()
        with self.lock:
            starting = bool(self.process and self.process.poll() is None and not running)
            model = self.selected_model
        return {
            "service": "boundless-mlx-host",
            "server_status": "running" if running else "starting" if starting else "offline",
            "models": installed_models(self.models_dir),
            "selected_model": model,
        }

    def start(self, model: str) -> tuple[int, dict]:
        with self.lock:
            choices = {entry["id"] for entry in installed_models(self.models_dir)}
            if model not in choices:
                return 422, {"detail": "Choose an installed MLX model."}
            if server_running():
                return 200, {"server_status": "running", "detail": "MLX is already running."}
            if self.process and self.process.poll() is None:
                return 202, {"server_status": "starting", "detail": "MLX is starting."}
            executable = server_command()
            if not executable:
                return 503, {"detail": "mlx_lm.server is not installed on this Mac."}
            log_path = ROOT / ".mlx-server.log"
            with log_path.open("ab") as log:
                self.process = subprocess.Popen(
                    [executable, "--model", model, "--host", HOST, "--port", str(MLX_PORT),
                     "--chat-template-args", '{"enable_thinking":false}'],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=ROOT,
                    start_new_session=True,
                )
            self.selected_model = model
        return 202, {"server_status": "starting", "detail": "Starting the selected MLX model."}


class Handler(BaseHTTPRequestHandler):
    launcher: Launcher

    def _send(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/status":
            self._send(200, self.launcher.status())
        else:
            self._send(404, {"detail": "Not found."})

    def do_POST(self) -> None:
        if self.path != "/start":
            self._send(404, {"detail": "Not found."})
            return
        if self.headers.get("Origin") or self.headers.get("X-Boundless-Launcher") != "start":
            self._send(403, {"detail": "Only the Boundless API may start MLX."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._send(415, {"detail": "Expected JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            model = payload["model"]
            if not isinstance(model, str):
                raise TypeError("Invalid model")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._send(400, {"detail": "Choose an installed MLX model."})
            return
        status, response = self.launcher.start(model)
        self._send(status, response)

    def log_message(self, format: str, *args) -> None:
        print(f"mlx-host: {format % args}", file=sys.stderr)


def ensure_running(models_dir: Path) -> None:
    if launcher_ready():
        print("Boundless MLX host launcher is ready.")
        return
    log_path = ROOT / ".mlx-host.log"
    with log_path.open("ab") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve", "--models-dir", str(models_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
            start_new_session=True,
        )
    for _ in range(25):
        if child.poll() is not None:
            break
        if launcher_ready():
            print("Boundless MLX host launcher is ready.")
            return
        time.sleep(0.1)
    raise SystemExit(f"Could not start the MLX host launcher. See {log_path}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--ensure", action="store_true")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    args = parser.parse_args()
    if not mlx_available():
        raise SystemExit("The MLX host launcher requires an Apple Silicon Mac.")
    if args.ensure:
        ensure_running(args.models_dir)
        return
    Handler.launcher = Launcher(args.models_dir)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Boundless MLX host launcher listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
