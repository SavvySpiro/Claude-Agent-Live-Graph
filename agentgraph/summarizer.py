"""Optional semantic summaries via `claude -p` (uses your normal Claude login, no API key).

Calls happen on one background thread; the graph shows the heuristic text until a
summary arrives, then switches. Results are cached on disk, keyed by content hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

CACHE_PATH = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "agentgraph" / "summaries.json"

PURPOSE_PROMPT = """You label nodes in a live dashboard of AI coding agents.
In at most 14 words, state the PURPOSE of what this agent is currently working toward:
the goal, not the mechanical action (never "reading files" or "running bash").
Reply with the phrase only, no quotes, no trailing period.

Task the agent was given:
{intent}

Its most recent steps:
{steps}
"""

EDIT_PROMPT = """You label edges in a live dashboard of AI coding agents editing files.
In at most 9 words, describe the semantic purpose of this change to `{name}`
(e.g. "adds ablation results table", "documents the retry policy").
Reply with the phrase only, no quotes, no trailing period.

What the agent said just before: {narration}

The change:
{diff}
"""


def _h(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:16]


class Summarizer:
    def __init__(self, model: str = "haiku", purpose_interval_s: float = 60.0):
        self.claude = shutil.which("claude")
        if not self.claude:
            raise RuntimeError("`claude` CLI not found on PATH; run without --summarize")
        self.model = model
        self.purpose_interval_s = purpose_interval_s
        self.cache: dict[str, str] = {}
        self.last_request: dict[str, float] = {}
        self.queue: OrderedDict[str, str] = OrderedDict()  # cache key -> prompt
        self.cv = threading.Condition()
        self.dirty = False
        self.errors = 0
        self._load()
        threading.Thread(target=self._worker, daemon=True, name="summarizer").start()

    # ----- public API used by the collector (never blocks) -----
    def purpose(self, node: str, intent: str, steps: list[str]) -> str | None:
        key = "p:" + _h(intent[:3000], *steps[-3:])
        if key in self.cache:
            if self.cache.get("l:" + node) != self.cache[key]:
                self.cache["l:" + node] = self.cache[key]  # survives restarts, shown while refreshing
                self.dirty = True
            return self.cache[key]
        now = time.time()
        if now - self.last_request.get(node, 0) >= self.purpose_interval_s:
            self.last_request[node] = now
            steps_txt = "\n".join(f"- {s}" for s in steps[-6:]) or "- (none yet)"
            self._enqueue(key, PURPOSE_PROMPT.format(intent=intent[:3000], steps=steps_txt))
        return self.cache.get("l:" + node)

    def edit(self, tool_id: str, path: str, diff: str, narration: str) -> str | None:
        key = "e:" + (tool_id or _h(path, diff))
        if key in self.cache:
            return self.cache[key]
        if diff:
            self._enqueue(key, EDIT_PROMPT.format(name=os.path.basename(path), diff=diff[:2500],
                                                  narration=narration or "(nothing)"))
        return None

    # ----- internals -----
    def _enqueue(self, key: str, prompt: str) -> None:
        with self.cv:
            if key in self.queue:
                return
            self.queue[key] = prompt
            while len(self.queue) > 40:  # keep the newest work
                self.queue.popitem(last=False)
            self.cv.notify()

    def _worker(self) -> None:
        workdir = tempfile.mkdtemp(prefix="agentgraph-")
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait(timeout=5)
                    self._save_if_dirty()
                key, prompt = self.queue.popitem(last=True)  # newest first
            text = self._call(prompt, workdir)
            if text:
                self.cache[key] = text
                self.dirty = True
            else:
                self.errors += 1
                time.sleep(min(60, 2 ** min(self.errors, 6)))
            self._save_if_dirty()

    def _call(self, prompt: str, cwd: str) -> str | None:
        cmd = [self.claude, "-p", "--model", self.model, "--no-session-persistence",
               "--output-format", "text", "--tools", ""]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=120, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = (r.stdout or "").strip().strip('"').strip()
        if r.returncode != 0 or not out:
            return None
        self.errors = 0
        line = out.splitlines()[0].strip().rstrip(".")
        return line[:160]

    def _load(self) -> None:
        try:
            with open(CACHE_PATH) as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            self.cache = {}

    def _save_if_dirty(self) -> None:
        if not self.dirty:
            return
        self.dirty = False
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            items = list(self.cache.items())[-5000:]
            tmp = CACHE_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(dict(items), f)
            os.replace(tmp, CACHE_PATH)
        except OSError:
            pass
