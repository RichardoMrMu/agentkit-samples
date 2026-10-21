"""用真实 MCP SDK / 本机 HTTP Server 验证所有 CLI，云端生命周期不在此测试范围。"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def local_server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SANDBOX_MCP_", "MCP_", "AGENTKIT_TOOL_ID"))
    }
    env.update(
        TEST_MCP_PORT=str(port),
        MCP_API_KEY="local-test-token",
        TEST_MCP_CALLS=str(tmp_path / "calls.log"),
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"mcp:\n  keyauth:\n    url: http://127.0.0.1:{port}/mcp\n  oauth:\n    url: http://127.0.0.1:{port}/mcp\n    token_url: http://127.0.0.1:{port}/oauth/token\n    client_id: test-client\n    client_secret: test-secret\npoll_interval_seconds: 1\nwait_timeout_seconds: 5\n"
    )
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/mock_server.py")],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    pytest.fail("本地 MCP Server 提前退出")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("本地 MCP Server 启动超时")
                    time.sleep(0.05)
            yield config, env
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_numbered_cli_flow(local_server):
    config, env = local_server
    calls_path = Path(env["TEST_MCP_CALLS"])
    tool_names = {
        "01_create_session.py": "createSession",
        "02_list_sessions.py": "listSessions",
        "03_get_session.py": "getSession",
        "04_invoke_tool.py": "invokeTool",
        "05_delete_session.py": "deleteSession",
    }

    def calls():
        return calls_path.read_text().splitlines() if calls_path.exists() else []

    def run(name, *args, success=True):
        before = len(calls())
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / name),
                "--config",
                str(config),
                "--json",
                *args,
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert "local-test-token" not in result.stdout + result.stderr
        invoked = calls()[before:]
        assert set(invoked) <= {tool_names[name]}, invoked
        if success:
            assert invoked, name
        return result

    env["MCP_API_KEY"] = "rejected-local-key"
    rejected = run("02_list_sessions.py", success=False)
    assert "401" in rejected.stdout
    assert "rejected-local-key" not in rejected.stdout + rejected.stderr
    env["MCP_API_KEY"] = "local-test-token"
    run("02_list_sessions.py")
    oauth = run("02_list_sessions.py", "--auth", "oauth")
    assert '"auth_mode": "oauth"' in oauth.stdout
    run("01_create_session.py")
    run("01_create_session.py", success=False)
    state_file = config.parent / ".sandbox_mcp_state.json"
    assert json.loads(state_file.read_text())["status"] == "creating"
    original = state_file.read_bytes()
    run("02_list_sessions.py")
    assert state_file.read_bytes() == original
    run("03_get_session.py", "--wait", "ready")
    assert json.loads(state_file.read_text())["status"] == "ready"
    run("04_invoke_tool.py", "--auth", "oauth")
    run("04_invoke_tool.py", "--code", "fail-test", success=False)
    run("05_delete_session.py")
    assert json.loads(state_file.read_text())["status"] == "delete_requested"
    run("03_get_session.py", "--wait", "deleted")
    assert json.loads(state_file.read_text())["status"] == "deleted"


def test_all_entrypoints_help_and_dry_run(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("version: 1\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SANDBOX_MCP_", "MCP_", "VOLCENGINE_"))
    }
    for script in sorted(ROOT.glob("[0-9][0-9]_*.py")):
        for args in (["--help"], ["--config", str(config), "--dry-run", "--json"]):
            result = subprocess.run(
                [sys.executable, str(script), *args],
                env=env,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == [config]
