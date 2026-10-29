"""Read the state of Claude Code agents on this machine.

Sources (all under ~/.claude, or $CLAUDE_CONFIG_DIR):
  sessions/<pid>.json         one file per running Claude process (name, busy/idle, jobId)
  jobs/<id>/state.json        background-job state: intent, current step ("detail"), result
  jobs/<id>/timeline.jsonl    history of job steps, used to explain *why* a file was edited
  projects/<slug>/<sid>.jsonl the session transcript; tool calls tell us what it touches
  projects/<slug>/<sid>/subagents/agent-*.jsonl (+ .meta.json)  subagents of a session
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".java", ".kt", ".swift", ".rb", ".php", ".cs", ".scala", ".sh", ".bash", ".zsh",
    ".sql", ".r", ".jl", ".lua", ".m", ".css", ".scss", ".vue", ".svelte", ".toml", ".yaml",
    ".yml", ".json", ".ipynb", ".cfg", ".ini", ".makefile", ".dockerfile",
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS"}
WEB_TOOLS = {"WebSearch", "WebFetch"}
AGENT_TOOLS = {"Agent", "Task", "SendMessage"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             ".pytest_cache", ".idea", ".vscode", "dist", "build", ".tox", ".cache"}

# How recent a tool call must be to count as "what the agent is doing now".
CURRENT_WINDOW_S = 120
# Bash output redirections / tee targets, e.g. `> out.md`, `>> log.txt`, `tee -a notes.md`.
BASH_WRITE_RE = re.compile(r"""(?:^|[\s;|&(])(?:>>?|tee(?:\s+-a)?)\s*("[^"]+"|'[^']+'|[^\s;|&<>()]+)""")
CD_RE = re.compile(r"""(?:^|[;&|]\s*)cd\s+("[^"]+"|'[^']+'|[^\s;&|]+)""")
HEREDOC_RE = re.compile(r"""<<-?\s*(['"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?:\n|$)""", re.S)
PATHLIKE_RE = re.compile(r"^[\w.~/+@-][\w.~/+@ -]*$")


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _parse_ts(s) -> float:
    if not s:
        return 0.0
    if isinstance(s, (int, float)):
        return s / 1000.0 if s > 1e11 else float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _first_sentence(text: str, limit: int = 140, min_len: int = 0) -> str:
    """Leading sentence(s) of text: at least min_len chars when available, at most limit."""
    text = " ".join((text or "").split())
    s = ""
    for m in re.finditer(r".+?(?:[.!?](?=\s|$)|$)", text):
        s += m.group(0)
        if len(s.strip()) >= max(min_len, 1):
            break
    s = s.strip() or text
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _count_lines(s: str) -> int:
    return s.count("\n") + 1 if s else 0


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def pid_parent(pid) -> int | None:
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def pid_tty(pid) -> str | None:
    try:
        t = os.readlink(f"/proc/{int(pid)}/fd/0")
        return t if t.startswith("/dev/pts") or t.startswith("/dev/tty") else None
    except (OSError, ValueError):
        return None


@dataclass
class ToolEvent:
    ts: float
    tool: str
    kind: str              # write | read | bash | web | agent | other
    summary: str           # short mechanical description ("edited 12 lines")
    path: str | None = None
    narration: str = ""    # the assistant text that preceded this call
    diff: str = ""         # truncated before/after text, for the LLM summarizer
    id: str = ""


@dataclass
class Transcript:
    """Incrementally tails one transcript .jsonl file."""
    path: Path
    offset: int = 0
    buf: bytes = b""
    events: deque = field(default_factory=lambda: deque(maxlen=400))
    first_prompt: str = ""
    last_text: str = ""
    narration: str = ""   # assistant text since the last tool result
    last_ts: float = 0.0
    cwd: str = ""

    MAX_INITIAL = 8 * 1024 * 1024

    def update(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.offset:  # truncated / rewritten
            self.offset, self.buf = 0, b""
            self.events.clear()
        if size == self.offset:
            return
        start, jumped = self.offset, False
        if start == 0 and size > self.MAX_INITIAL:
            # Huge transcript: read the first chunk for the opening prompt, then only the tail.
            with open(self.path, "rb") as f:
                head = f.read(256 * 1024).split(b"\n")[:-1]
            for line in head:
                self._ingest(line, prompt_only=True)
            start, jumped = size - self.MAX_INITIAL, True
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read(size - start)
        self.offset = size
        if jumped:
            data = data.split(b"\n", 1)[-1]  # drop the partial first line
        lines = (self.buf + data).split(b"\n")
        self.buf = lines.pop()
        for line in lines:
            self._ingest(line)

    def _ingest(self, raw: bytes, prompt_only: bool = False) -> None:
        if not raw.strip():
            return
        try:
            d = json.loads(raw)
        except ValueError:
            return
        typ = d.get("type")
        if d.get("cwd"):
            self.cwd = d["cwd"]
        if typ == "user":
            self.narration = ""
        if typ == "user" and not self.first_prompt:
            content = (d.get("message") or {}).get("content")
            text = content if isinstance(content, str) else " ".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            text = text.strip()
            if text and not text.startswith("<") and not text.startswith("[Request interrupted"):
                self.first_prompt = text
        if prompt_only or typ != "assistant":
            return
        ts = _parse_ts(d.get("timestamp"))
        self.last_ts = max(self.last_ts, ts)
        for c in (d.get("message") or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and c.get("text", "").strip():
                self.last_text = self.narration = c["text"].strip()
            elif c.get("type") == "tool_use":
                ev = make_event(c, ts, self.narration, d.get("cwd") or self.cwd)
                if ev:
                    self.events.append(ev)


def make_event(c: dict, ts: float, narration: str, cwd: str) -> ToolEvent | None:
    name = c.get("name", "")
    inp = c.get("input") or {}
    narr = _first_sentence(narration, 100)
    if name in WRITE_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path")
        if name == "Write":
            content = inp.get("content", "")
            summary = f"wrote {_count_lines(content)} lines"
            diff = "NEW CONTENT:\n" + content[:1500]
        elif name == "MultiEdit":
            edits = inp.get("edits") or []
            summary = f"{len(edits)} edits"
            diff = "\n---\n".join(f"OLD:\n{e.get('old_string','')[:400]}\nNEW:\n{e.get('new_string','')[:400]}"
                                  for e in edits[:4])
        elif name == "NotebookEdit":
            summary = f"{inp.get('edit_mode', 'edit')} notebook cell"
            diff = "NEW CELL:\n" + (inp.get("new_source") or "")[:1200]
        else:
            old, new = inp.get("old_string", ""), inp.get("new_string", "")
            summary = f"edited {_count_lines(old)}→{_count_lines(new)} lines"
            diff = f"OLD:\n{old[:700]}\nNEW:\n{new[:900]}"
        return ToolEvent(ts, name, "write", summary, path, narr, diff, c.get("id", ""))
    if name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description") or _first_sentence(cmd, 70)
        shell = HEREDOC_RE.sub("\n", cmd)  # heredoc bodies are data, not commands
        targets = [(m.start(), m.group(1).strip("'\"")) for m in BASH_WRITE_RE.finditer(shell)]
        targets = [(i, t) for i, t in targets if PATHLIKE_RE.match(t) and not t.startswith("/dev/")]
        if targets:
            at, path = targets[-1]
            base = cwd
            for m in CD_RE.finditer(shell[:at]):  # honour `cd somewhere && ... > file`
                d = os.path.expanduser(m.group(1).strip("'\""))
                base = d if os.path.isabs(d) else os.path.join(base or "", d)
            path = os.path.expanduser(path)
            if base and not os.path.isabs(path):
                path = os.path.join(base, path)
            return ToolEvent(ts, name, "write", f"shell: {desc}", os.path.normpath(path), narr,
                             "COMMAND:\n" + cmd[:1200], c.get("id", ""))
        return ToolEvent(ts, name, "bash", desc, None, narr, "", c.get("id", ""))
    if name in READ_TOOLS:
        target = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
        return ToolEvent(ts, name, "read", f"{name.lower()} {os.path.basename(str(target)) or target}",
                         inp.get("file_path"), narr, "", c.get("id", ""))
    if name in WEB_TOOLS:
        return ToolEvent(ts, name, "web", inp.get("query") or inp.get("url") or name, None, narr, "", c.get("id", ""))
    if name in AGENT_TOOLS:
        return ToolEvent(ts, name, "agent", inp.get("description") or "delegating", None, narr, "", c.get("id", ""))
    if name in ("ToolSearch", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return None
    return ToolEvent(ts, name, "other", name, None, narr, "", c.get("id", ""))


def classify(status: str, job_state: str, last: ToolEvent | None, now: float, watch_dir: str | None,
             in_flight: int = 0) -> str:
    """Map raw state to one of the activity categories the UI colours."""
    js = (job_state or "").lower()
    if status == "busy":
        # A busy session is working right now; a blocked/failed job state is left over from
        # before the user replied and is only rewritten at the agent's next report.
        js = ""
    if "fail" in js or "error" in js:
        return "failed"
    if "input" in js or "block" in js or "wait" in js or "permission" in js:
        return "waiting"
    if status != "busy" and (status == "shell" or in_flight > 0):
        return "running"  # turn ended, but a background command (e.g. a training run) is still going
    if status != "busy":
        return "done" if js in ("done", "completed", "complete", "finished") else "idle"
    if not last or now - last.ts > CURRENT_WINDOW_S:
        return "thinking"
    if last.kind == "write":
        if last.path and watch_dir and in_dir(last.path, watch_dir):
            return "reporting"
        ext = os.path.splitext(last.path or "")[1].lower()
        return "coding" if ext in CODE_EXTS or not ext else "writing"
    return {"bash": "running", "read": "researching", "web": "researching",
            "agent": "delegating"}.get(last.kind, "thinking")


def in_dir(path: str, directory: str) -> bool:
    try:
        p = os.path.realpath(path)
        d = os.path.realpath(directory)
    except OSError:
        return False
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def scan_files(directory: str, max_depth: int = 3, max_files: int = 300) -> list[str]:
    out: list[str] = []
    root = os.path.realpath(directory)
    if not os.path.isdir(root):
        return out
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if depth >= max_depth:
            dirnames[:] = []
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.endswith((".pyc", ".swp")):
                continue
            out.append(os.path.join(dirpath, fn))
            if len(out) >= max_files:
                return out
    return out


class Collector:
    """Builds the graph snapshot. Thread-safe; caches transcript tails between calls."""

    def __init__(self, watch_dir: str | None, summarizer=None, edge_window_s: int = 900,
                 subagent_window_s: int = 600, max_depth: int = 3, keep_finished_s: int = 24 * 3600):
        self.home = claude_home()
        self.watch_dir = os.path.realpath(watch_dir) if watch_dir else None
        self.summarizer = summarizer
        self.edge_window_s = edge_window_s
        self.subagent_window_s = subagent_window_s
        self.max_depth = max_depth
        self.keep_finished_s = keep_finished_s
        self.lock = threading.Lock()
        self.transcripts: dict[Path, Transcript] = {}
        self.timelines: dict[Path, tuple[float, list]] = {}
        self._cache: tuple[float, dict] | None = None

    # ---------- raw readers ----------
    def _read_json(self, p: Path) -> dict:
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _transcript(self, p: Path) -> Transcript:
        t = self.transcripts.get(p)
        if t is None:
            t = self.transcripts[p] = Transcript(p)
        t.update()
        return t

    def _timeline(self, job_id: str) -> list[tuple[float, str]]:
        p = self.home / "jobs" / job_id / "timeline.jsonl"
        try:
            m = p.stat().st_mtime
        except OSError:
            return []
        cached = self.timelines.get(p)
        if cached and cached[0] == m:
            return cached[1]
        rows = []
        with open(p) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("detail"):
                    rows.append((_parse_ts(d.get("at")), d["detail"]))
        self.timelines[p] = (m, rows)
        return rows

    def _find_transcript(self, session_id: str, hint: str | None) -> Path | None:
        if hint and os.path.exists(hint):
            return Path(hint)
        hits = list((self.home / "projects").glob(f"*/{session_id}.jsonl"))
        return max(hits, key=lambda p: p.stat().st_mtime) if hits else None

    def sessions(self) -> list[dict]:
        out = []
        me = os.getpid()
        for p in (self.home / "sessions").glob("*.json"):
            s = self._read_json(p)
            if not s or s.get("spare") or not pid_alive(s.get("pid")):
                continue
            if pid_parent(s.get("pid")) == me:  # our own summarizer calls
                continue
            out.append(s)
        return out

    # ---------- semantic text ----------
    def _step_at(self, timeline: list[tuple[float, str]], ts: float) -> str:
        best = ""
        for at, detail in timeline:
            if at <= ts + 2:
                best = detail
            else:
                break
        return best

    def _purpose(self, key: str, intent: str, steps: list[str], fallback: str) -> str:
        if self.summarizer and intent:
            s = self.summarizer.purpose(key, intent, steps)
            if s:
                return s
        return fallback

    # ---------- snapshot ----------
    def snapshot(self, min_interval: float = 1.0) -> dict:
        with self.lock:
            now = time.time()
            if self._cache and now - self._cache[0] < min_interval:
                return self._cache[1]
            snap = self._build(now)
            self._cache = (now, snap)
            return snap

    def set_watch_dir(self, d: str | None) -> None:
        with self.lock:
            self.watch_dir = os.path.realpath(d) if d else None
            self._cache = None

    def _build(self, now: float) -> dict:
        agents, edges, links = [], {}, []
        live = self.sessions()
        for s in live:
            sid = s.get("sessionId", "")
            job_id = s.get("jobId")
            job = self._read_json(self.home / "jobs" / job_id / "state.json") if job_id else {}
            tpath = self._find_transcript(sid, job.get("linkScanPath"))
            tr = self._transcript(tpath) if tpath else None
            timeline = self._timeline(job_id) if job_id else []
            node_id = f"agent:{sid}"
            agent = self._agent_node(
                node_id=node_id, short=job_id or sid[:8], name=s.get("name") or job.get("name"),
                status=s.get("status", "idle"), job=job, tr=tr, timeline=timeline, now=now,
                cwd=s.get("cwd", ""), kind="background" if s.get("kind") == "bg" else (s.get("kind") or "interactive"),
                pid=s.get("pid"), attachable=bool(job_id) and s.get("kind") == "bg", session_id=sid,
            )
            agents.append(agent)
            if tr:
                self._edges_for(node_id, tr, timeline, now, edges)
                self._subagents(node_id, tpath, now, agents, edges, links)
            for child in (job.get("children") or []) if isinstance(job.get("children"), list) else []:
                cid = child.get("sessionId") if isinstance(child, dict) else None
                if cid:
                    links.append({"source": node_id, "target": f"agent:{cid}", "kind": "child"})

        # Background jobs whose process has exited (finished, failed, or stopped waiting for you) are
        # still listed by `claude agents` and can be re-attached, so keep them on the graph for a while.
        live_jobs = {s.get("jobId") for s in live if s.get("jobId")}
        live_sids = {a["sessionId"] for a in agents}
        for jd in (self.home / "jobs").glob("*/state.json") if self.keep_finished_s else []:
            job_id = jd.parent.name
            if job_id in live_jobs:
                continue
            job = self._read_json(jd)
            sid = job.get("sessionId") or ""
            updated = _parse_ts(job.get("updatedAt")) or jd.stat().st_mtime
            if not sid or sid in live_sids or now - updated > self.keep_finished_s:
                continue
            tpath = self._find_transcript(sid, job.get("linkScanPath"))
            tr = self._transcript(tpath) if tpath else None
            timeline = self._timeline(job_id)
            node_id = f"agent:{sid}"
            agents.append(self._agent_node(
                node_id=node_id, short=job.get("daemonShort") or job_id, name=job.get("name"), status="exited",
                job=job, tr=tr, timeline=timeline, now=now, cwd=job.get("cwd", ""), kind="background",
                pid=None, attachable=True, session_id=sid,
            ))
            if tr:
                self._edges_for(node_id, tr, timeline, now, edges)

        files = []
        if self.watch_dir:
            seen = set()
            for f in scan_files(self.watch_dir, self.max_depth):
                seen.add(f)
                files.append(self._file_node(f))
            for e in edges.values():
                if e["path"] not in seen:
                    seen.add(e["path"])
                    files.append(self._file_node(e["path"]))
        ids = {a["id"] for a in agents}
        links = [l for l in links if l["source"] in ids and l["target"] in ids]
        return {
            "generatedAt": now,
            "watchDir": self.watch_dir,
            "agents": agents,
            "files": files,
            "edges": sorted(edges.values(), key=lambda e: -e["lastTs"]),
            "links": links,
            "summarizer": bool(self.summarizer),
            "edgeWindow": self.edge_window_s,
        }

    def _file_node(self, path: str) -> dict:
        rel = os.path.relpath(path, self.watch_dir) if self.watch_dir else path
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        return {"id": f"file:{path}", "path": path, "label": rel, "name": os.path.basename(path),
                "mtime": mtime, "exists": os.path.exists(path)}

    def _agent_node(self, *, node_id, short, name, status, job, tr, timeline, now, cwd, kind, pid,
                    attachable, session_id, description=None) -> dict:
        events = list(tr.events) if tr else []
        last = events[-1] if events else None
        in_flight = (job.get("inFlight") or {}).get("tasks") or 0 if isinstance(job.get("inFlight"), dict) else 0
        if status == "exited":
            in_flight = 0  # nothing can still be running in a process that is gone
        category = classify(status, job.get("state", ""), last, now, self.watch_dir, in_flight)
        intent = job.get("intent") or (tr.first_prompt if tr else "") or description or ""
        now_text = job.get("detail") or ""
        stale = status == "busy" and any(w in (job.get("state") or "").lower()
                                         for w in ("block", "input", "wait", "permission", "fail", "error"))
        if stale:  # detail is still the question (or the user's reply), not what it's doing now
            now_text = ""
        if not now_text and last and category not in ("idle", "done"):
            now_text = last.narration or last.summary
        if category == "done":
            out = job.get("output")
            result = out.get("result") if isinstance(out, dict) else (out if isinstance(out, str) else "")
            now_text = "Finished: " + _first_sentence(result or now_text, 120) if (result or now_text) else "Finished"
        elif category == "idle" and not now_text:
            now_text = "Idle"
        steps = [d for _, d in timeline[-6:]] or [e.narration for e in events[-6:] if e.narration]
        purpose = self._purpose(node_id, intent, steps, description or _first_sentence(intent, 160, 60) or "—")
        return {
            "id": node_id,
            "short": short,
            "name": name or short,
            "category": category,
            "status": status,
            "jobState": job.get("state"),
            "inFlight": in_flight,
            "purpose": purpose,
            "now": now_text,
            "intent": intent[:2000],
            "cwd": cwd,
            "kind": kind,
            "pid": pid,
            "tty": pid_tty(pid) if pid and kind != "background" else None,
            "attachable": attachable,
            "sessionId": session_id,
            "tokens": job.get("tokens"),
            "lastActivity": max(tr.last_ts if tr else 0, _parse_ts(job.get("updatedAt"))),
            "recent": [
                {"ts": e.ts, "tool": e.tool, "kind": e.kind, "summary": e.summary,
                 "path": e.path, "narration": e.narration}
                for e in events[-15:][::-1]
            ],
        }

    def _edges_for(self, node_id: str, tr: Transcript, timeline, now: float, edges: dict) -> None:
        if not self.watch_dir:
            return
        for e in tr.events:
            if e.kind != "write" or not e.path or now - e.ts > self.edge_window_s:
                continue
            if not in_dir(e.path, self.watch_dir):
                continue
            path = os.path.realpath(e.path)
            key = f"{node_id}->{path}"
            desc = None
            if self.summarizer:
                desc = self.summarizer.edit(e.id, path, e.diff, e.narration)
            desc = desc or e.narration or self._step_at(timeline, e.ts) or e.summary
            prev = edges.get(key)
            edges[key] = {
                "id": f"edge:{key}",
                "source": node_id,
                "target": f"file:{path}",
                "path": path,
                "label": desc,
                "mechanical": e.summary,
                "tool": e.tool,
                "count": (prev["count"] if prev else 0) + 1,
                "lastTs": e.ts,
                "age": now - e.ts,
                "history": ((prev["history"] if prev else []) + [{"ts": e.ts, "label": desc, "summary": e.summary}])[-10:],
            }

    def _subagents(self, parent_id: str, tpath: Path, now: float, agents, edges, links) -> None:
        sub_dir = tpath.with_suffix("") / "subagents"
        if not sub_dir.is_dir():
            return
        for meta_path in sub_dir.glob("agent-*.meta.json"):
            jsonl = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            if now - mtime > self.subagent_window_s:
                continue
            meta = self._read_json(meta_path)
            tr = self._transcript(jsonl)
            agent_id = jsonl.stem.replace("agent-", "")
            node_id = f"sub:{agent_id}"
            finished = bool(meta.get("finished"))
            status = "idle" if finished else ("busy" if now - mtime < CURRENT_WINDOW_S else "idle")
            node = self._agent_node(
                node_id=node_id, short=agent_id[:8], name=meta.get("description") or f"subagent {agent_id[:8]}",
                status=status, job={"state": "done" if finished else ""}, tr=tr, timeline=[], now=now,
                cwd=tr.cwd, kind=f"subagent ({meta.get('agentType', 'agent')})", pid=None,
                attachable=False, session_id=parent_id, description=meta.get("description"),
            )
            node["parent"] = parent_id
            agents.append(node)
            links.append({"source": parent_id, "target": node_id, "kind": "subagent"})
            self._edges_for(node_id, tr, [], now, edges)
