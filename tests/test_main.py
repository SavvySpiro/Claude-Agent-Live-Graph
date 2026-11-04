"""CLI handling of --dir and flag forwarding (agentgraph/__main__.py)."""

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from agentgraph import __main__ as M

PKG_ROOT = Path(M.__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """A real agentgraph server whose cwd is the package root, like a --background child."""
    port = free_port()
    layout = tmp_path / "layout.json"
    proc = subprocess.Popen([sys.executable, "-m", "agentgraph", "--port", str(port), "--layout", str(layout)],
                            cwd=PKG_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if M.already_running(url):
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield port, layout, url
    proc.kill()
    proc.wait()


def watched(url):
    with urllib.request.urlopen(url + "/api/graph", timeout=3) as r:
        return json.load(r)["watchDir"]


def test_relative_dir_resolved_against_caller_cwd(server, tmp_path, monkeypatch):
    port, layout, url = server
    # "agentgraph" exists both here and in the server's cwd; the caller's must win.
    (tmp_path / "agentgraph").mkdir()
    monkeypatch.chdir(tmp_path)
    assert M.main(["--dir", "agentgraph", "--port", str(port), "--layout", str(layout)]) == 0
    assert watched(url) == str((tmp_path / "agentgraph").resolve())


def test_relative_dir_missing_on_server_side_does_not_crash(server, tmp_path, monkeypatch):
    port, layout, url = server
    (tmp_path / "reports").mkdir()
    monkeypatch.chdir(tmp_path)
    assert M.main(["--dir", "reports", "--port", str(port), "--layout", str(layout)]) == 0
    assert watched(url) == str((tmp_path / "reports").resolve())


def test_invalid_dir_leaves_running_server_alone(server, tmp_path):
    port, layout, url = server
    urllib.request.urlopen(urllib.request.Request(url + "/api/watch", json.dumps({"dir": str(tmp_path)}).encode(),
                                                  {"Content-Type": "application/json"}), timeout=3).read()
    assert M.main(["--dir", "/does/not/exist", "--port", str(port), "--layout", str(layout)]) == 0
    assert watched(url) == str(tmp_path.resolve())


def test_server_rejection_is_reported_not_raised(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(M, "already_running", lambda url: True)

    def reject(req, timeout):
        import io
        raise urllib.error.HTTPError(req.full_url, 400, "BAD REQUEST", {}, io.BytesIO(b'{"error": "nope"}'))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    assert M.main(["--dir", str(tmp_path), "--layout", str(tmp_path / "l.json")]) == 1
    assert "nope" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--install-skill", "--install-service"])
def test_install_refuses_invalid_dir(flag, tmp_path, monkeypatch):
    monkeypatch.setattr(M, "SKILL_DIR", tmp_path / "skill")
    monkeypatch.setattr(M, "SERVICE_FILE", tmp_path / "agentgraph.service")
    assert M.main([flag, "--dir", "/does/not/exist"]) == 2
    assert not (tmp_path / "skill").exists() and not (tmp_path / "agentgraph.service").exists()


def test_install_service_forwards_depth_and_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "SERVICE_FILE", tmp_path / "agentgraph.service")
    monkeypatch.setattr("shutil.which", lambda exe: None)  # stop before calling systemctl
    layout = tmp_path / "custom.json"
    M.main(["--install-service", "--depth", "5", "--layout", str(layout)])
    unit = (tmp_path / "agentgraph.service").read_text()
    assert "--depth 5" in unit
    assert f"--layout {layout.resolve()}" in unit


def test_install_skill_forwards_nondefault_host_and_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "SKILL_DIR", tmp_path / "skill")
    layout = tmp_path / "custom.json"
    M.main(["--install-skill", "--host", "0.0.0.0", "--layout", str(layout), "--dir", str(tmp_path)])
    skill = (tmp_path / "skill" / "SKILL.md").read_text()
    assert "--host 0.0.0.0" in skill
    assert f"--layout '{layout.resolve()}'" in skill
    assert f"--dir '{tmp_path}'" in skill


def test_install_skill_omits_default_host_and_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "SKILL_DIR", tmp_path / "skill")
    M.main(["--install-skill"])
    skill = (tmp_path / "skill" / "SKILL.md").read_text()
    assert "--host" not in skill and "--layout" not in skill
