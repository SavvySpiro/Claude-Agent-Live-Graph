"""Serve agentgraph on made-up agents, for trying the UI or taking screenshots.

    python3 scripts/demo.py [--port 8766]

Builds a throwaway Claude config dir (CLAUDE_CONFIG_DIR) and a sample project in a temp dir; nothing
under your real ~/.claude is read or written.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

NOW = time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(age, *content):
    return {"type": "assistant", "timestamp": iso(NOW - age), "cwd": "/demo",
            "message": {"role": "assistant", "content": list(content)}}


def say(t):
    return {"type": "text", "text": t}


def tool(name, **inp):
    return {"type": "tool_use", "id": f"t{abs(hash((name, str(inp)))) % 10**8}", "name": name, "input": inp}


def build(root: Path) -> tuple[Path, Path]:
    home, proj = root / "claude", root / "acme-app"
    for d in ("sessions", "jobs", "projects/-demo"):
        (home / d).mkdir(parents=True)
    for f in ("src/api/routes.py", "src/api/auth.py", "src/db/models.py", "src/db/migrations/004_users.sql",
              "tests/test_routes.py", "tests/test_auth.py", "docs/architecture.md", "docs/benchmarks.md",
              "docs/release-notes.md", "README.md", "pyproject.toml"):
        (proj / f).parent.mkdir(parents=True, exist_ok=True)
        (proj / f).write_text("demo\n")
    p = lambda f: str(proj / f)

    agents = [
        # (name, status, job state, intent, current step, transcript)
        ("api-refactor", "busy", "working", "Split the monolithic routes module into per-resource blueprints.",
         "Moving user endpoints into their own module", [
             assistant(200, say("Extracting the auth helpers first."), tool("Edit", file_path=p("src/api/auth.py"), old_string="a", new_string="b")),
             assistant(20, say("Now pointing the user routes at the new helpers."), tool("Edit", file_path=p("src/api/routes.py"), old_string="a", new_string="b\nc"))]),
        ("test-writer", "busy", "working", "Bring route test coverage above 90%.", "Running the test suite", [
             assistant(120, say("Adding cases for expired tokens."), tool("Write", file_path=p("tests/test_auth.py"), content="x")),
             assistant(5, tool("Bash", command="pytest -q tests"))]),
        ("schema-migration", "busy", "working", "Add an email_verified column to users without downtime.",
         "Writing the forward migration", [
             assistant(400, tool("Read", file_path=p("src/db/models.py"))),
             assistant(30, say("Backfill runs in batches of 10k."), tool("Write", file_path=p("src/db/migrations/004_users.sql"), content="x")),
             assistant(10, tool("Edit", file_path=p("src/db/models.py"), old_string="a", new_string="b"))]),
        ("perf-research", "busy", "working", "Find why p99 latency doubled after the ORM upgrade.",
         "Searching for N+1 queries", [
             assistant(300, say("Recording baseline numbers."), tool("Write", file_path=p("docs/benchmarks.md"), content="x")),
             assistant(8, tool("Grep", pattern="select_related", path=p("src")))]),
        ("docs-lead", "busy", "working", "Keep the architecture docs in step with the refactor.",
         "Delegating diagrams to a subagent", [
             assistant(90, say("Documenting the new blueprint layout."), tool("Edit", file_path=p("docs/architecture.md"), old_string="a", new_string="b")),
             assistant(4, tool("Agent", description="Redraw request-flow diagram", prompt="..."))]),
        ("release-notes", "idle", "blocked", "Draft the v2.3 release notes.",
         "Needs input: should the auth change be listed as breaking?", [
             assistant(600, say("Draft ready apart from the auth section."), tool("Write", file_path=p("docs/release-notes.md"), content="x"))]),
        ("dependency-audit", "idle", "idle", "Check dependencies for known CVEs.", "Waiting for the next task", [
             assistant(1500, tool("Read", file_path=p("pyproject.toml")))]),
    ]
    pid = os.getpid()  # sessions must point at a live pid
    for i, (name, status, state, intent, step, lines) in enumerate(agents):
        sid, job = f"demo{i:02d}-0000-4000-8000-000000000000", f"job{i:02d}ab"
        (home / "sessions" / f"{i}.json").write_text(json.dumps({
            "pid": pid, "sessionId": sid, "cwd": "/demo", "kind": "bg", "name": name,
            "jobId": job, "status": status}))
        (home / "jobs" / job).mkdir()
        (home / "jobs" / job / "state.json").write_text(json.dumps({
            "state": state, "detail": step, "intent": intent, "name": name, "sessionId": sid,
            "daemonShort": job, "updatedAt": iso(NOW - 10)}))
        (home / "jobs" / job / "timeline.jsonl").write_text(json.dumps({"at": iso(NOW - 60), "detail": step}) + "\n")
        (home / "projects" / "-demo" / f"{sid}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
        if name == "docs-lead":
            sub = home / "projects" / "-demo" / sid / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-d1a6.meta.json").write_text(json.dumps(
                {"description": "Redraw request-flow diagram", "agentType": "general-purpose", "finished": False}))
            (sub / "agent-d1a6.jsonl").write_text(json.dumps(assistant(
                3, say("Adding the blueprint hop to the diagram."),
                tool("Edit", file_path=p("docs/architecture.md"), old_string="a", new_string="b"))) + "\n")

    # a background job that already finished (no live process)
    (home / "jobs" / "job99ff").mkdir()
    (home / "jobs" / "job99ff" / "state.json").write_text(json.dumps({
        "state": "done", "name": "lint-cleanup", "sessionId": "demo99-0000-4000-8000-000000000000",
        "daemonShort": "job99ff", "updatedAt": iso(NOW - 900), "intent": "Fix all ruff warnings.",
        "output": {"result": "142 warnings fixed, CI green."}, "detail": "142 warnings fixed, CI green."}))
    return home, proj


def team_layout(root: Path) -> None:
    """Boxes, nesting, group-to-group links and a note, as if a user had arranged the graph."""
    a = lambda i: f"agent:demo{i:02d}-0000-4000-8000-000000000000"
    groups = {"group:lead": ("Leads", "#6366f1"), "group:squad": ("Refactor squad", "#a855f7"),
              "group:backend": ("Backend", "#3b82f6"), "group:qa": ("QA", "#f59e0b"),
              "group:investigate": ("Investigations", "#14b8a6"), "group:release": ("Release", "#22c55e")}
    parents = {a(4): "group:lead", "sub:d1a6": "group:lead",
               "group:backend": "group:squad", "group:qa": "group:squad",
               a(0): "group:backend", a(2): "group:backend", a(1): "group:qa",
               a(3): "group:investigate", a(6): "group:investigate",
               a(5): "group:release", "agent:demo99-0000-4000-8000-000000000000": "group:release",
               "note:ship": "group:release"}
    links = [("group:lead", "group:squad", "assigns"), ("group:lead", "group:investigate", "assigns"),
             ("group:backend", "group:qa", "hands off"), ("group:squad", "group:release", "ships via"),
             ("group:investigate", "group:release", "")]
    (root / "layout.json").write_text(json.dumps({
        "positions": {}, "hidden": [], "parents": parents,
        "groups": {g: {"label": l, "color": c} for g, (l, c) in groups.items()},
        "links": [{"id": f"link:{i}", "source": s, "target": t, "label": l} for i, (s, t, l) in enumerate(links)],
        "notes": {"note:ship": {"text": "Ship Friday once QA signs off", "parent": "group:release"}}}))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="agentgraph-demo-"))
    home, proj = build(root)
    team_layout(root)
    os.environ["CLAUDE_CONFIG_DIR"] = str(home)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agentgraph.__main__ import main as agentgraph_main
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "8766"
    return agentgraph_main(["--dir", str(proj), "--port", port, "--layout", str(root / "layout.json")])


if __name__ == "__main__":
    raise SystemExit(main())
