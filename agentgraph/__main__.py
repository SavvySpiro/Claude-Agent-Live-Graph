"""agentgraph: live graph of the Claude Code agents running on this machine.

    python -m agentgraph --dir ~/Projects/foo/reports --open
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_LAYOUT = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agentgraph" / "layout.json"
SKILL_DIR = Path.home() / ".claude" / "skills" / "agent-graph"
SKILL_MD = """---
name: agent-graph
description: Open the live agent graph (agentgraph) in the browser, starting its server if needed. Use when the user asks to see, open or show the agent graph / agent visualization.
---

Run this exactly, then tell the user the URL it prints:

```bash
{python} -m agentgraph --open --background --port {port}{dir_arg}
```
"""
SERVICE_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / "agentgraph.service"
SERVICE_UNIT = """[Unit]
Description=agentgraph - live graph of Claude Code agents ({url})

[Service]
WorkingDirectory={workdir}
Environment="PATH={path}"
{display}ExecStart={exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_service(args) -> int:
    """Write a systemd user unit that runs this server with the current flags, then enable it."""
    import shlex
    import shutil
    import subprocess
    pkg_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "agentgraph", "--host", args.host, "--port", str(args.port),
           "--keep-finished", str(args.keep_finished), "--edge-window", str(args.edge_window)]
    if args.dir:
        cmd += ["--dir", os.path.abspath(os.path.expanduser(args.dir))]
    if args.summarize:
        cmd += ["--summarize", "--model", args.model]
    if args.terminal:
        cmd += ["--terminal", args.terminal]
    # systemd's quoting is close enough to POSIX shell quoting for paths with spaces
    exec_start = " ".join(f'"{c}"' if any(ch in c for ch in " \t") else c for c in cmd)
    display = f"Environment=DISPLAY={os.environ['DISPLAY']}\n" if os.environ.get("DISPLAY") else ""
    SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(SERVICE_UNIT.format(
        url=f"http://{args.host}:{args.port}", workdir=pkg_root, path=os.environ.get("PATH", "/usr/bin:/bin"),
        display=display, exec_start=exec_start))
    print(f"wrote {SERVICE_FILE}")
    if not shutil.which("systemctl"):
        print("systemctl not found; start the server some other way", file=sys.stderr)
        return 1
    for step in (["systemctl", "--user", "daemon-reload"],
                 ["systemctl", "--user", "enable", "--now", "agentgraph"],
                 ["systemctl", "--user", "restart", "agentgraph"]):
        print("$", shlex.join(step))
        if subprocess.call(step):
            return 1
    print(f"agentgraph service running at http://{args.host}:{args.port}")
    print(f"to start it at boot (before you log in): loginctl enable-linger {os.environ.get('USER', '$USER')}")
    return 0


def open_browser(url: str) -> None:
    """Open url without letting the browser inherit our stdio (which would hang callers reading our output)."""
    import shutil
    import subprocess
    opener = "open" if sys.platform == "darwin" else ("xdg-open" if shutil.which("xdg-open") else None)
    if opener:
        subprocess.Popen([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        webbrowser.open(url)


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=1) as r:
            return r.read().startswith(b"agentgraph")
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agentgraph", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="directory whose files become nodes (default: last used)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the graph in a browser")
    ap.add_argument("--background", action="store_true", help="detach the server and return immediately")
    ap.add_argument("--summarize", action="store_true",
                    help="use `claude -p` (haiku) for semantic purpose / edit descriptions")
    ap.add_argument("--model", default="haiku", help="model for --summarize (default: haiku)")
    ap.add_argument("--edge-window", type=int, default=15, help="minutes an edit edge stays visible (default 15)")
    ap.add_argument("--keep-finished", type=float, default=24,
                    help="hours to keep exited background jobs (done/failed/waiting) on the graph; 0 hides them (default 24)")
    ap.add_argument("--depth", type=int, default=3, help="how deep to list files under --dir (default 3)")
    ap.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT, help=f"layout file (default {DEFAULT_LAYOUT})")
    ap.add_argument("--terminal", help="terminal launch template for attaching, e.g. 'kitty -e {cmd}'")
    ap.add_argument("--install-skill", action="store_true",
                    help="install a /agent-graph skill so any Claude session can open this view, then exit")
    ap.add_argument("--install-service", action="store_true",
                    help="install and start a systemd user service running with these flags, then exit")
    ap.add_argument("--dump", action="store_true", help="print one JSON snapshot and exit")
    args = ap.parse_args(argv)
    url = f"http://{args.host}:{args.port}"

    if args.install_skill:
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        pkg_root = Path(__file__).resolve().parent.parent
        dir_arg = f" --dir '{os.path.abspath(os.path.expanduser(args.dir))}'" if args.dir else ""
        python = f"cd '{pkg_root}' && {sys.executable}"
        (SKILL_DIR / "SKILL.md").write_text(SKILL_MD.format(python=python, port=args.port, dir_arg=dir_arg))
        print(f"installed {SKILL_DIR / 'SKILL.md'} — type /agent-graph in any Claude session")
        return 0

    if args.install_service:
        return install_service(args)

    from .collector import Collector
    from .server import LayoutStore, create_app

    layout = LayoutStore(args.layout)
    watch_dir = os.path.expanduser(args.dir) if args.dir else layout.load().get("watchDir")
    if watch_dir and not os.path.isdir(watch_dir):
        print(f"warning: {watch_dir} is not a directory; pick one in the UI", file=sys.stderr)
        watch_dir = None

    if args.dump:
        c = Collector(watch_dir, None, args.edge_window * 60, max_depth=args.depth,
                      keep_finished_s=int(args.keep_finished * 3600))
        json.dump(c.snapshot(), sys.stdout, indent=1, default=str)
        return 0

    if already_running(url):
        print(f"agentgraph already running at {url}")
        if args.dir:
            req = urllib.request.Request(url + "/api/watch", json.dumps({"dir": watch_dir}).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        if args.open:
            open_browser(url)
        return 0

    if args.background:
        import subprocess
        child = ["--host", args.host, "--port", str(args.port), "--layout", str(Path(args.layout).resolve()),
                 "--edge-window", str(args.edge_window), "--depth", str(args.depth), "--model", args.model,
                 "--keep-finished", str(args.keep_finished)]
        if args.dir:
            child += ["--dir", os.path.abspath(watch_dir)]
        if args.summarize:
            child.append("--summarize")
        if args.terminal:
            child += ["--terminal", args.terminal]
        log = Path(args.layout).with_name("server.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([sys.executable, "-m", "agentgraph", *child], start_new_session=True,
                         cwd=Path(__file__).resolve().parent.parent,
                         stdout=open(log, "ab"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        import time
        for _ in range(50):
            if already_running(url):
                break
            time.sleep(0.1)
        print(f"agentgraph running at {url} (log: {log})")
        if args.open:
            open_browser(url)
        return 0

    summarizer = None
    if args.summarize:
        from .summarizer import Summarizer
        summarizer = Summarizer(model=args.model)

    collector = Collector(watch_dir, summarizer, args.edge_window * 60, max_depth=args.depth,
                          keep_finished_s=int(args.keep_finished * 3600))
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # don't log every 2s poll
    app = create_app(collector, layout, args.terminal)
    print(f"agentgraph at {url}  (watching: {watch_dir or 'nothing yet'})")
    if args.open:
        import threading
        threading.Timer(0.8, open_browser, [url]).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
