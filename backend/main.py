# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vasudev Siddh and vasu-devs

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

# ── Startup trace log ────────────────────────────────────────────────────────
# Written before any heavy imports so we can pinpoint exactly which import
# causes the sidecar to crash in the PyInstaller bundle.
def _trace(msg: str) -> None:
    """Write a timestamped trace line to both stderr and a log file."""
    line = f"[JHM-TRACE] {msg}\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        _log_dir = os.environ.get("JHM_APP_DATA_DIR") or os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "JustHireMe"
        )
        os.makedirs(_log_dir, exist_ok=True)
        with open(os.path.join(_log_dir, "startup_trace.log"), "a", encoding="utf-8") as _f:
            _f.write(line)
    except Exception:
        pass


_trace("main.py started")
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import WebSocket

_trace("fastapi imported")

from api.app import create_app

_trace("api.app imported")

from api.auth import create_api_token, require_ws_token

_trace("api.auth imported")

from api.scheduler import create_ghost_tick, create_followup_tick, create_lifespan, create_scheduler

_trace("api.scheduler imported")

from api.websocket import ConnectionManager, agent_event_action as _agent_event_action  # noqa: F401

_trace("api.websocket imported")

from core.logging import get_logger

_trace("core.logging imported")

_log = get_logger(__name__)


def _reserve_socket(preferred: int = 0) -> socket.socket:
    """Bind and KEEP OPEN a listening socket so the port can't be stolen.

    The old flow picked a port (bind+close) then let uvicorn re-bind it later,
    leaving a window where another process could grab the port — after we'd
    already announced it to the UI. Holding the open socket and handing it to
    uvicorn eliminates that TOCTOU race: the port is ours from announce to serve.
    """
    # No SO_REUSEADDR: we hand this exact socket to uvicorn (never re-bind), and
    # on Windows SO_REUSEADDR would let another process bind the same port,
    # defeating the whole point of reserving it. Keep the bind exclusive.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", preferred))
    return s


_trace("creating module-level singletons")
_UP = time.monotonic()
_sched = create_scheduler()
_trace("scheduler created")
_API_TOKEN: str = create_api_token()
_trace("api token created")
cm = ConnectionManager()
_trace("ConnectionManager created")


async def _require_ws_token(ws: WebSocket) -> bool:
    return await require_ws_token(ws, lambda: _API_TOKEN)


def build_gateway_app():
    _trace("build_gateway_app: start")
    ghost_tick    = create_ghost_tick(cm)
    _trace("build_gateway_app: ghost_tick created")
    followup_tick = create_followup_tick(cm)
    _trace("build_gateway_app: followup_tick created")
    lifespan = create_lifespan(_sched, ghost_tick, _log, followup_tick=followup_tick)
    _trace("build_gateway_app: lifespan created")
    app = create_app(
        lifespan=lifespan,
        token_getter=lambda: _API_TOKEN,
        started_at=_UP,
        scheduler=_sched,
        ghost_tick=ghost_tick,
        connection_manager=cm,
        logger=_log,
        websocket_token_guard=_require_ws_token,
    )
    _trace("build_gateway_app: create_app OK")
    return app


_GATEWAY_APP_SINGLETON = None


def __getattr__(name: str):
    """Lazily build the gateway app on first attribute access (PEP 562).

    Building at module import time created a second app + scheduler that
    uvicorn never ran (it builds its own in ``__main__``), leaking resources
    and calling ``ensure_ghost_job``/``init_sql`` twice. Tests and tooling that
    do ``from main import app`` still work, but the app is only constructed
    once, on demand, and cached.
    """
    global _GATEWAY_APP_SINGLETON
    if name == "app":
        if _GATEWAY_APP_SINGLETON is None:
            _GATEWAY_APP_SINGLETON = build_gateway_app()
        return _GATEWAY_APP_SINGLETON
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _parse_args():
    parser = argparse.ArgumentParser(description="JustHireMe backend gateway runner")
    parser.add_argument("--port", type=int, default=0)
    # Accepted for backward compatibility: the desktop shell still passes it.
    # The app is always the in-process monolith now (no service subprocesses).
    parser.add_argument("--no-services", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    import traceback
    import uvicorn

    _trace("__main__ entered")
    args = _parse_args()

    # --- Startup diagnostics (wrapped so crash reason is always visible) ---
    try:
        _trace("calling build_gateway_app")
        gateway_app = build_gateway_app()
        _trace("build_gateway_app returned OK")
    except Exception as _startup_exc:
        _tb = traceback.format_exc()
        _trace(f"build_gateway_app FAILED: {type(_startup_exc).__name__}: {_startup_exc}")
        _trace(_tb)
        print(f"ERROR: startup failed: {type(_startup_exc).__name__}: {_startup_exc}", flush=True)
        print(_tb, flush=True)
        sys.exit(1)
    # -----------------------------------------------------------------------

    # Hold the bound socket, announce the port only after we own it, then hand
    # the same socket to uvicorn — no re-bind, no port-steal race.
    sock = _reserve_socket(args.port)
    port = sock.getsockname()[1]
    _trace(f"port reserved: {port}")
    sys.stdout.write(f"JHM_TOKEN={_API_TOKEN}\n")
    sys.stdout.write(f"PORT:{port}\n")
    sys.stdout.flush()
    _trace("token+port announced — starting uvicorn")
    uvicorn.Server(uvicorn.Config(gateway_app, log_level="warning")).run(sockets=[sock])
