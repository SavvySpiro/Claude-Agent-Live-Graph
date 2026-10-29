"""Tests against a fake ~/.claude tree (CLAUDE_CONFIG_DIR) built in a temp dir."""

import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

from agentgraph import collector as C


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(ts, *content, cwd="/work"):
    return {"type": "assistant", "timestamp": iso(ts), "cwd": cwd, "message": {"role": "assistant", "content": list(content)}}


def tool(name, tid="t1", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def text(t):
    return {"type": "text", "text": t}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "claude"
    for d in ("sessions", "jobs", "projects/-work"):
        (h / d).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(h))
    return h


def write_session(home, sid, job, lines, *, status="busy", state=None, kind="bg", spare=False, pid=None):
    pid = pid or os.getpid()  # an alive pid
    (home / "sessions" / f"{sid[:6]}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": "/work", "kind": kind, "name": f"agent {job}",
        "jobId": job, "status": status, "spare": spare}))
    if job:
        (home / "jobs" / job).mkdir(parents=True, exist_ok=True)
        (home / "jobs" / job / "state.json").write_text(json.dumps({
            "state": state or "working", "detail": "Drafting the weekly report", "intent": "Write the weekly status report for the team. Then email it."}))
        (home / "jobs" / job / "timeline.jsonl").write_text(
            json.dumps({"at": iso(time.time() - 30), "detail": "Collecting metrics for the report"}) + "\n")
    (home / "projects" / "-work" / f"{sid}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))


def test_agent_writing_to_watched_dir_gets_edge(home, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "weekly.md").write_text("x")
    (reports / "other.md").write_text("y")
    now = time.time()
    write_session(home, "s1-aaaa-bbbb", "job1", [
        {"type": "user", "message": {"role": "user", "content": "Write the weekly report"}},
        assistant(now - 10, text("Now I'll add the metrics table to the report."),
                  tool("Edit", "t1", file_path=str(reports / "weekly.md"), old_string="a", new_string="a\nb\nc")),
    ])
    snap = C.Collector(str(reports)).snapshot()
    [agent] = snap["agents"]
    assert agent["category"] == "reporting"
    assert agent["name"] == "agent job1"
    assert agent["purpose"].startswith("Write the weekly status report")
    assert agent["now"] == "Drafting the weekly report"
    assert {f["name"] for f in snap["files"]} == {"weekly.md", "other.md"}
    [edge] = snap["edges"]
    assert edge["target"] == f"file:{reports / 'weekly.md'}"
    assert edge["label"] == "Now I'll add the metrics table to the report."
    assert edge["mechanical"] == "edited 1→3 lines"


def test_edits_outside_watched_dir_are_ignored_and_classified_as_coding(home, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    now = time.time()
    write_session(home, "s2-aaaa", "job2", [assistant(now - 5, tool("Write", file_path="/work/app.py", content="print(1)"))])
    snap = C.Collector(str(reports)).snapshot()
    assert snap["agents"][0]["category"] == "coding"
    assert snap["edges"] == []


def test_categories(home):
    now = time.time()
    ev = lambda kind, path=None: C.ToolEvent(now, "X", kind, "", path)
    assert C.classify("idle", "done", None, now, None) == "done"
    assert C.classify("idle", "working", None, now, None) == "idle"
    assert C.classify("idle", "needs_input", None, now, None) == "waiting"
    assert C.classify("busy", "working", ev("bash"), now, None) == "running"
    assert C.classify("busy", "working", ev("web"), now, None) == "researching"
    assert C.classify("busy", "working", ev("write", "/a/notes.md"), now, None) == "writing"
    assert C.classify("busy", "working", ev("write", "/a/notes.md"), now, "/a") == "reporting"
    assert C.classify("busy", "working", C.ToolEvent(now - 999, "X", "bash", ""), now, None) == "thinking"


def test_spare_and_dead_sessions_are_skipped(home):
    write_session(home, "spare-1", None, [], spare=True)
    write_session(home, "dead-11", "job3", [], pid=2 ** 22 + 12345)
    assert C.Collector(None).snapshot()["agents"] == []


def test_bash_redirect_counts_as_write(home, tmp_path):
    reports = tmp_path / "r"
    reports.mkdir()
    now = time.time()
    write_session(home, "s4-aaaa", "job4", [
        assistant(now - 3, tool("Bash", command=f"python run.py > {reports}/results.txt 2>/dev/null", description="Run eval")),
    ])
    snap = C.Collector(str(reports)).snapshot()
    [edge] = snap["edges"]
    assert edge["path"].endswith("results.txt")
    assert edge["mechanical"] == "shell: Run eval"


def test_bash_false_positives():
    ev = C.make_event(tool("Bash", command="python -c \"print('a'->',e['x'])\" && x=$((a>b))"), 0, "", "/w")
    assert ev.kind == "bash"


def test_narration_resets_after_tool_result(home, tmp_path):
    reports = tmp_path / "r"
    reports.mkdir()
    now = time.time()
    write_session(home, "s5-aaaa", "job5", [
        assistant(now - 9, text("Let me check the tests."), tool("Bash", "t0", command="pytest")),
        tool_result(),
        assistant(now - 5, tool("Write", "t1", file_path=str(reports / "x.md"), content="hi")),
    ])
    [edge] = C.Collector(str(reports)).snapshot()["edges"]
    assert edge["label"] == "Collecting metrics for the report"  # falls back to the job timeline step


def test_transcript_is_tailed_incrementally(home, tmp_path):
    reports = tmp_path / "r"
    reports.mkdir()
    now = time.time()
    write_session(home, "s6-aaaa", "job6", [assistant(now - 5, tool("Read", file_path="/a"))])
    c = C.Collector(str(reports))
    assert c.snapshot(0)["edges"] == []
    with open(home / "projects" / "-work" / "s6-aaaa.jsonl", "a") as f:
        f.write(json.dumps(assistant(now, tool("Write", "t9", file_path=str(reports / "new.md"), content="a\nb"))) + "\n")
    snap = c.snapshot(0)
    assert [e["mechanical"] for e in snap["edges"]] == ["wrote 2 lines"]
    assert any(f["name"] == "new.md" for f in snap["files"])  # listed even before it exists on disk


def test_subagents_become_child_nodes(home, tmp_path):
    reports = tmp_path / "r"
    reports.mkdir()
    now = time.time()
    write_session(home, "s7-aaaa", "job7", [assistant(now - 5, tool("Agent", description="Port layer"))])
    sub = home / "projects" / "-work" / "s7-aaaa" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abc123.meta.json").write_text(json.dumps({"description": "Port layer", "agentType": "fork", "finished": False}))
    (sub / "agent-abc123.jsonl").write_text(json.dumps(
        assistant(now - 2, tool("Write", file_path=str(reports / "port.md"), content="x"))) + "\n")
    snap = C.Collector(str(reports)).snapshot()
    ids = {a["id"] for a in snap["agents"]}
    assert ids == {"agent:s7-aaaa", "sub:abc123"}
    assert snap["links"] == [{"source": "agent:s7-aaaa", "target": "sub:abc123", "kind": "subagent"}]
    assert snap["edges"][0]["source"] == "sub:abc123"


def test_scan_files_skips_junk(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("")
    (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "d" / "deep.md").write_text("")
    (tmp_path / "top.md").write_text("")
    assert [os.path.basename(f) for f in C.scan_files(str(tmp_path), max_depth=3)] == ["top.md"]


def test_bash_redirect_honours_cd():
    ev = C.make_event(tool("Bash", command='cd /tmp/x && cat > "out file.md" <<EOF\nhi\nEOF'), 0, "", "/work")
    assert ev.kind == "write" and ev.path == "/tmp/x/out file.md"
    ev = C.make_event(tool("Bash", command="echo hi > notes.md"), 0, "", "/work")
    assert ev.path == "/work/notes.md"


def test_heredoc_bodies_are_not_parsed_as_redirects():
    cmd = "cat >> tests/t.py <<'EOF'\nx = 1\necho hi > notes.md\nEOF\nls"
    ev = C.make_event(tool("Bash", command=cmd), 0, "", "/work")
    assert ev.path == "/work/tests/t.py"
    ev = C.make_event(tool("Bash", command="python3 - <<'EOF'\nif a > b: pass\nEOF"), 0, "", "/work")
    assert ev.kind == "bash"


def test_background_shell_counts_as_running():
    now = time.time()
    assert C.classify("shell", "working", None, now, None) == "running"
    assert C.classify("idle", "working", None, now, None, in_flight=1) == "running"
    assert C.classify("shell", "needs_input", None, now, None) == "waiting"


def test_blocked_job_state_is_stale_while_session_busy(home, tmp_path):
    now = time.time()
    assert C.classify("busy", "blocked", C.ToolEvent(now, "Bash", "bash", ""), now, None) == "running"
    assert C.classify("idle", "blocked", None, now, None) == "waiting"
    write_session(home, "s8-aaaa", "job8", [assistant(now - 2, text("Re-running the sampler with 50 per bin."),
                                                      tool("Bash", command="python sample.py"))],
                  status="busy", state="blocked")
    [a] = C.Collector(None).snapshot()["agents"]
    assert a["category"] == "running"
    assert a["now"] == "Re-running the sampler with 50 per bin."


def test_exited_background_jobs_stay_on_the_graph(home):
    now = time.time()
    for job, state, age in (("jobA", "done", 600), ("jobB", "blocked", 600), ("jobC", "done", 3 * 86400)):
        (home / "jobs" / job).mkdir()
        (home / "jobs" / job / "state.json").write_text(json.dumps({
            "state": state, "name": f"agent {job}", "sessionId": f"sess-{job}", "daemonShort": job,
            "updatedAt": iso(now - age), "inFlight": {"tasks": 2},
            "output": {"result": "Figure committed."}, "detail": "Figure committed."}))
    got = {a["short"]: a for a in C.Collector(None).snapshot()["agents"]}
    assert set(got) == {"jobA", "jobB"}  # jobC is older than the 24 h default
    assert got["jobA"]["category"] == "done" and got["jobA"]["status"] == "exited"
    assert got["jobA"]["attachable"] and got["jobA"]["now"] == "Finished: Figure committed."
    assert got["jobB"]["category"] == "waiting"
    assert C.Collector(None, keep_finished_s=0).snapshot()["agents"] == []


def test_install_service_writes_portable_unit(tmp_path, monkeypatch):
    import shutil
    from agentgraph import __main__ as M
    monkeypatch.setattr(M, "SERVICE_FILE", tmp_path / "agentgraph.service")
    monkeypatch.setattr(shutil, "which", lambda exe: None)  # don't touch the real systemd
    monkeypatch.setenv("DISPLAY", ":0")
    (tmp_path / "my notes").mkdir()
    assert M.main(["--install-service", "--dir", str(tmp_path / "my notes"), "--summarize"]) == 1
    unit = (tmp_path / "agentgraph.service").read_text()
    assert f"ExecStart={sys.executable} -m agentgraph --host 127.0.0.1 --port 8765" in unit
    assert f'--dir "{tmp_path / "my notes"}" --summarize' in unit
    assert "Environment=DISPLAY=:0" in unit
