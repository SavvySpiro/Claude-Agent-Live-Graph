"""Flask app: serves the UI, the live graph snapshot, and the saved layout."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import attach
from .collector import Collector

STATIC = Path(__file__).parent / "static"


class LayoutStore:
    """Positions, boxes, notes and manual links, saved as one JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def load(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)


def create_app(collector: Collector, layout: LayoutStore, terminal_template: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(STATIC, p)

    @app.get("/api/graph")
    def graph():
        return jsonify(collector.snapshot())

    @app.get("/api/layout")
    def get_layout():
        return jsonify(layout.load())

    @app.post("/api/layout")
    def put_layout():
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, dict):
            return jsonify(error="expected an object"), 400
        layout.save(data)
        return jsonify(ok=True)

    @app.post("/api/watch")
    def watch():
        d = (request.get_json(force=True, silent=True) or {}).get("dir") or ""
        d = os.path.expanduser(d.strip())
        if d and not os.path.isdir(d):
            return jsonify(error=f"not a directory: {d}"), 400
        collector.set_watch_dir(d or None)
        saved = layout.load()
        saved["watchDir"] = collector.watch_dir
        layout.save(saved)
        return jsonify(watchDir=collector.watch_dir)

    @app.get("/api/dirs")
    def dirs():
        """Directory completions for the folder picker."""
        prefix = os.path.expanduser(request.args.get("prefix", "") or "~/")
        base = prefix if prefix.endswith(os.sep) else os.path.dirname(prefix) or "/"
        stem = "" if prefix.endswith(os.sep) else os.path.basename(prefix)
        try:
            names = sorted(n for n in os.listdir(base)
                           if n.startswith(stem) and not n.startswith(".") and os.path.isdir(os.path.join(base, n)))
        except OSError:
            names = []
        return jsonify([os.path.join(base, n) + os.sep for n in names[:40]])

    @app.post("/api/attach")
    def do_attach():
        body = request.get_json(force=True, silent=True) or {}
        snap = collector.snapshot()
        agent = next((a for a in snap["agents"] if a["id"] == body.get("id")), None)
        if not agent:
            return jsonify(error="agent not found (it may have exited)"), 404
        if not agent["attachable"]:
            where = f" on {agent['tty']}" if agent.get("tty") else ""
            return jsonify(error=f"{agent['kind']} session (pid {agent['pid']}{where}) can't be attached; "
                                 "switch to its terminal"), 409
        try:
            msg = attach.open_in_terminal(agent["short"], f"claude: {agent['name']}", terminal_template)
        except Exception as e:  # noqa: BLE001 - surface any launcher failure to the UI
            return jsonify(error=str(e), command=attach.attach_command(agent["short"])), 500
        return jsonify(ok=True, message=msg, command=attach.attach_command(agent["short"]))

    @app.get("/healthz")
    def healthz():
        return Response("agentgraph", mimetype="text/plain")

    return app
