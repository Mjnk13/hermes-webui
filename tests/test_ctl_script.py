import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CTL = REPO_ROOT / "ctl.sh"
HEALTH_PROBE = REPO_ROOT / "scripts" / "lib" / "health_probe.sh"


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_ctl_repo(repo_root: Path) -> None:
    """Copy ctl.sh plus its sourced dependencies into an isolated repo dir."""
    shutil.copy2(CTL, repo_root / "ctl.sh")
    lib_dir = repo_root / "scripts" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HEALTH_PROBE, lib_dir / "health_probe.sh")



def run_ctl(
    home: Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 5.0,
    repo_root: Path = REPO_ROOT,
    load_dotenv: bool = False,
):
    merged = os.environ.copy()
    for key in (
        "HERMES_WEBUI_HOST",
        "HERMES_WEBUI_PORT",
        "HERMES_WEBUI_PYTHON",
        "HERMES_WEBUI_STATE_DIR",
        "HERMES_WEBUI_PID_FILE",
        "HERMES_WEBUI_LOG_FILE",
        "HERMES_WEBUI_CTL_STATE_FILE",
        "HERMES_WEBUI_NO_DOTENV",
        "HERMES_WEBUI_DESKTOP_SHELL",
        "HERMES_WEBUI_DESKTOP_PID_FILE",
        "HERMES_WEBUI_DESKTOP_LOG_FILE",
        "HERMES_WEBUI_DESKTOP_USER_DATA_DIR",
        "HERMES_WEBUI_DESKTOP_LAUNCH_METHOD",
    ):
        merged.pop(key, None)
    merged.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "PATH": os.environ.get("PATH", ""),
            "HERMES_WEBUI_NO_DOTENV": "0" if load_dotenv else "1",
        }
    )
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", str(repo_root / "ctl.sh"), *args],
        cwd=repo_root,
        env=merged,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def write_fake_python(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env bash
            printf 'fake-python args:%s\n' "$*" >> "${FAKE_PYTHON_LOG}"
            printf 'host=%s port=%s state=%s\n' "${HERMES_WEBUI_HOST:-}" "${HERMES_WEBUI_PORT:-}" "${HERMES_WEBUI_STATE_DIR:-}" >> "${FAKE_PYTHON_LOG}"
            trap 'printf "terminated\n" >> "${FAKE_PYTHON_LOG}"; exit 0' TERM INT
            while true; do sleep 0.1; done
            """
        ).lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o755)


def wait_for_pid_file(pid_file: Path, timeout: float = 3.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pid_file.exists():
            raw = pid_file.read_text(encoding="utf-8").strip()
            if raw:
                return int(raw)
        time.sleep(0.05)
    raise AssertionError(f"PID file was not written: {pid_file}")


def wait_for_file_text(path: Path, timeout: float = 3.0, contains: str | None = None) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text and (contains is None or contains in text):
                return text
        time.sleep(0.05)
    raise AssertionError(f"File was not written: {path}")


def assert_path_in_text(path: Path, text: str) -> None:
    assert str(path).replace("\\", "/") in text.replace("\\", "/")


def bash_path(path: Path) -> str:
    raw = str(path.resolve()).replace("\\", "/")
    if sys.platform == "win32" and len(raw) > 1 and raw[1] == ":":
        return f"/{raw[0].lower()}{raw[2:]}"
    return raw


def bash_pid(pid: int) -> int:
    if sys.platform != "win32":
        return pid
    result = subprocess.run(
        [
            "bash",
            "-lc",
            "ps -W | awk -v winpid=\"$1\" '$4 == winpid { print $1; exit }'",
            "_",
            str(pid),
        ],
        text=True,
        capture_output=True,
        timeout=3,
    )
    if result.returncode == 0 and result.stdout.strip():
        return int(result.stdout.strip())
    return pid


def windows_pid(pid: int) -> int | None:
    if sys.platform != "win32":
        return pid
    result = subprocess.run(
        [
            "bash",
            "-lc",
            "ps -p \"$1\" -l | awk 'NR == 2 { print $4 }'",
            "_",
            str(pid),
        ],
        text=True,
        capture_output=True,
        timeout=3,
    )
    if result.returncode == 0 and result.stdout.strip():
        return int(result.stdout.strip())
    return None


def start_fake_launchd_process() -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-lc", "exec sleep 30"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        winpid = windows_pid(pid)
        if winpid is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(winpid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        return (
            subprocess.run(
                ["bash", "-lc", "kill -0 \"$1\"", "_", str(pid)],
                text=True,
                capture_output=True,
                timeout=3,
            ).returncode
            == 0
        )
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def assert_process_exits(pid: int, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.05)
    _kill_tree(pid)
    raise AssertionError(f"process {pid} did not exit")


def test_start_writes_pid_under_hermes_home_runs_foreground_no_browser_and_logs(tmp_path):
    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)

    result = run_ctl(
        tmp_path,
        "start",
        env={
            "HERMES_WEBUI_PYTHON": str(fake_python),
            "FAKE_PYTHON_LOG": str(fake_log),
            "HERMES_WEBUI_HOST": "0.0.0.0",
            "HERMES_WEBUI_PORT": "18991",
            "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    hermes_home = tmp_path / ".hermes"
    pid_file = hermes_home / "webui.pid"
    log_file = hermes_home / "webui.log"
    pid = wait_for_pid_file(pid_file)
    try:
        assert pid > 1
        assert log_file.exists()
        fake_output = wait_for_file_text(fake_log, contains="host=0.0.0.0 port=18991")
        assert "bootstrap.py --no-browser --foreground" in fake_output
        assert "host=0.0.0.0 port=18991" in fake_output
        assert_path_in_text(hermes_home / "webui", fake_output)
        status = run_ctl(tmp_path, "status")
        assert status.returncode == 0
        assert "running" in status.stdout
        assert f"PID:     {pid}" in status.stdout
        assert "Bound:   0.0.0.0:18991" in status.stdout
        assert_path_in_text(log_file, status.stdout)
    finally:
        stop = run_ctl(tmp_path, "stop")
        assert stop.returncode == 0, stop.stderr + stop.stdout
        _kill_tree(pid)
        assert_process_exits(pid)
        assert not pid_file.exists()


def test_start_uses_nohup_so_daemon_survives_launcher_exit():
    ctl_text = CTL.read_text(encoding="utf-8")

    assert "trap '' HUP" in ctl_text
    assert 'exec nohup "${python_exe}"' in ctl_text


def test_start_reopens_missing_electron_without_duplicating_running_app(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# fake bootstrap target\n", encoding="utf-8")
    sidecar = repo_root / "scripts" / "start-browser-workbench-desktop.sh"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s|%s|%s\\n' \"${HERMES_WEBUI_URL:-}\" \"${HERMES_WEBUI_PID:-}\" \"${HERMES_WEBUI_DESKTOP_PID_FILE:-}\" >> \"${DESKTOP_TEST_LOG}\"\n",
        encoding="utf-8",
    )
    sidecar.chmod(0o755)

    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    desktop_log = tmp_path / "desktop-sidecar.log"
    write_fake_python(fake_python)
    port = "18995"
    base_env = {
        "HERMES_WEBUI_PYTHON": str(fake_python),
        "FAKE_PYTHON_LOG": str(fake_log),
        "DESKTOP_TEST_LOG": str(desktop_log),
        "HERMES_WEBUI_PORT": port,
        "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        "HERMES_WEBUI_DESKTOP_LAUNCH_METHOD": "background",
    }

    started_pid = None
    electron = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    launching_helper = None
    try:
        first = run_ctl(
            tmp_path,
            "start",
            env={**base_env, "HERMES_WEBUI_DESKTOP_SHELL": "0"},
            repo_root=repo_root,
        )
        assert first.returncode == 0, first.stderr + first.stdout
        started_pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")

        desktop_pid_file = tmp_path / ".hermes" / "webui" / f"desktop-shell-{port}.pid.app"
        desktop_pid_file.write_text(str(electron.pid), encoding="utf-8")
        already_running = run_ctl(
            tmp_path,
            "start",
            env={**base_env, "HERMES_WEBUI_DESKTOP_SHELL": "1"},
            repo_root=repo_root,
        )
        assert already_running.returncode == 0, already_running.stderr + already_running.stdout
        assert "Electron desktop app is already running" in already_running.stdout
        assert not desktop_log.exists(), "start must not launch a duplicate Electron sidecar"

        electron.terminate()
        electron.wait(timeout=3)
        launching_helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
        runner_pid_file = tmp_path / ".hermes" / "webui" / f"desktop-shell-{port}.pid.runner"
        runner_pid_file.write_text(str(launching_helper.pid), encoding="utf-8")
        in_progress = run_ctl(
            tmp_path,
            "start",
            env={**base_env, "HERMES_WEBUI_DESKTOP_SHELL": "1"},
            repo_root=repo_root,
        )
        assert in_progress.returncode == 0, in_progress.stderr + in_progress.stdout
        assert "Electron desktop shell launch is already in progress" in in_progress.stdout
        assert not desktop_log.exists(), "start must not race an existing Electron helper"

        launching_helper.terminate()
        launching_helper.wait(timeout=3)
        reopened = run_ctl(
            tmp_path,
            "start",
            env={**base_env, "HERMES_WEBUI_DESKTOP_SHELL": "1"},
            repo_root=repo_root,
        )
        assert reopened.returncode == 0, reopened.stderr + reopened.stdout
        assert "Electron desktop shell launch requested" in reopened.stdout
        launched = wait_for_file_text(desktop_log, contains=f"http://127.0.0.1:{port}")
        assert f"http://127.0.0.1:{port}|{started_pid}|" in launched
        assert str(tmp_path / ".hermes" / "webui" / f"desktop-shell-{port}.pid") in launched
    finally:
        if started_pid:
            stop = run_ctl(tmp_path, "stop", repo_root=repo_root)
            assert stop.returncode == 0, stop.stderr + stop.stdout
            _kill_tree(started_pid)
            assert_process_exits(started_pid)
        if electron.poll() is None:
            electron.terminate()
            try:
                electron.wait(timeout=3)
            except subprocess.TimeoutExpired:
                electron.kill()
        if launching_helper is not None and launching_helper.poll() is None:
            launching_helper.terminate()
            try:
                launching_helper.wait(timeout=3)
            except subprocess.TimeoutExpired:
                launching_helper.kill()


def test_start_can_ignore_repo_dotenv_for_authoritative_test_env(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# fake bootstrap target\n", encoding="utf-8")
    (repo_root / ".env").write_text(
        f"HERMES_WEBUI_STATE_DIR={tmp_path / 'host-specific-webui'}\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)

    result = run_ctl(
        tmp_path,
        "start",
        env={
            "HERMES_WEBUI_PYTHON": str(fake_python),
            "FAKE_PYTHON_LOG": str(fake_log),
            "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        },
        repo_root=repo_root,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")
    try:
        fake_output = wait_for_file_text(fake_log, contains="host=127.0.0.1 port=8787")
        assert_path_in_text(tmp_path / ".hermes" / "webui", fake_output)
        assert "host-specific-webui" not in fake_output
    finally:
        stop = run_ctl(tmp_path, "stop", repo_root=repo_root)
        assert stop.returncode == 0, stop.stderr + stop.stdout
        _kill_tree(pid)
        assert_process_exits(pid)


def test_start_loads_dotenv_but_inline_overrides_win(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# fake bootstrap target\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)
    (repo_root / ".env").write_text(
        "HERMES_WEBUI_HOST=127.9.9.9\nHERMES_WEBUI_PORT=18888\n",
        encoding="utf-8",
    )

    result = run_ctl(
        tmp_path,
        "start",
        env={
            "HERMES_WEBUI_PYTHON": str(fake_python),
            "FAKE_PYTHON_LOG": str(fake_log),
            "HERMES_WEBUI_HOST": "0.0.0.0",
            "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        },
        repo_root=repo_root,
        load_dotenv=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")
    try:
        fake_output = wait_for_file_text(fake_log, contains="host=0.0.0.0 port=18888")
        assert "fake-python args:" in fake_output
        assert "host=0.0.0.0 port=18888" in fake_output
    finally:
        stop = run_ctl(tmp_path, "stop", repo_root=repo_root)
        assert stop.returncode == 0, stop.stderr + stop.stdout
        _kill_tree(pid)
        assert_process_exits(pid)


def test_start_loads_dotenv_double_quoted_port_with_trailing_comment(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# fake bootstrap target\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)
    (repo_root / ".env").write_text(
        'HERMES_WEBUI_PORT="19004" # inline comment\n',
        encoding="utf-8",
    )

    result = run_ctl(
        tmp_path,
        "start",
        env={
            "HERMES_WEBUI_PYTHON": str(fake_python),
            "FAKE_PYTHON_LOG": str(fake_log),
            "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        },
        repo_root=repo_root,
        load_dotenv=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")
    try:
        fake_output = wait_for_file_text(fake_log, contains="host=127.0.0.1 port=19004")
        assert "host=127.0.0.1 port=19004" in fake_output
    finally:
        stop = run_ctl(tmp_path, "stop", repo_root=repo_root)
        assert stop.returncode == 0, stop.stderr + stop.stdout
        _kill_tree(pid)
        assert_process_exits(pid)


def test_start_loads_dotenv_export_tab_host_assignment(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# fake bootstrap target\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)
    (repo_root / ".env").write_text(
        "export\tHERMES_WEBUI_HOST=0.0.0.0\nHERMES_WEBUI_PORT=19005\n",
        encoding="utf-8",
    )

    result = run_ctl(
        tmp_path,
        "start",
        env={
            "HERMES_WEBUI_PYTHON": str(fake_python),
            "FAKE_PYTHON_LOG": str(fake_log),
            "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
        },
        repo_root=repo_root,
        load_dotenv=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")
    try:
        fake_output = wait_for_file_text(fake_log, contains="host=0.0.0.0 port=19005")
        assert "host=0.0.0.0 port=19005" in fake_output
    finally:
        stop = run_ctl(tmp_path, "stop", repo_root=repo_root)
        assert stop.returncode == 0, stop.stderr + stop.stdout
        _kill_tree(pid)
        assert_process_exits(pid)


def test_stale_pid_file_is_removed_without_killing_unrelated_process(tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    pid_file = hermes_home / "webui.pid"
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    try:
        pid_file.write_text(str(sleeper.pid), encoding="utf-8")
        result = run_ctl(tmp_path, "stop")
        assert result.returncode == 0
        assert "stale" in (result.stdout + result.stderr).lower()
        assert sleeper.poll() is None, "ctl.sh must not kill unrelated PIDs"
        assert not pid_file.exists()
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_stop_recovers_and_kills_repo_owned_listener_without_pid_file(tmp_path):
    if sys.platform == "win32":
        pytest.skip("lsof-based orphan listener recovery is a POSIX/macOS path")
    if not shutil.which("lsof"):
        pytest.skip("requires lsof to discover the orphan listener")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    port = free_tcp_port()
    (repo_root / "server.py").write_text(
        textwrap.dedent(
            """
            import signal
            import socket
            import sys
            import time

            alive = True
            def stop(*_args):
                global alive
                alive = False
            signal.signal(signal.SIGTERM, stop)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', int(sys.argv[1])))
            sock.listen(1)
            while alive:
                time.sleep(0.05)
            sock.close()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    listener = subprocess.Popen(
        [sys.executable, str(repo_root / "server.py"), str(port)],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("fake server.py did not listen")

        result = run_ctl(
            tmp_path,
            "stop",
            env={"HERMES_WEBUI_PORT": str(port)},
            repo_root=repo_root,
            timeout=10,
        )

        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Stopping Hermes WebUI orphan" in combined
        assert str(listener.pid) in combined
        listener.wait(timeout=3)
    finally:
        if listener.poll() is None:
            listener.terminate()
            try:
                listener.wait(timeout=3)
            except subprocess.TimeoutExpired:
                listener.kill()


def _write_fake_launchctl(fake_bin, pid):
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            if [[ "$1" == "print" ]]; then
              printf '\\tpid = {pid}\\n'
              exit 0
            fi
            exit 1
            """
        ).lstrip(),
        encoding="utf-8",
    )
    launchctl.chmod(0o755)


def _write_fake_lsof(fake_bin, listening):
    """Fake lsof: exit 0 (PID listens on the port) iff `listening` is True."""
    lsof = fake_bin / "lsof"
    lsof.write_text(
        "#!/usr/bin/env bash\n" + ("exit 0\n" if listening else "exit 1\n"),
        encoding="utf-8",
    )
    lsof.chmod(0o755)


def test_start_refuses_second_instance_when_launchd_job_owns_the_port(tmp_path):
    if sys.platform == "win32":
        pytest.skip("launchd conflict guard is a macOS path; fake launchctl PIDs are not stable under Git Bash")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    sleeper = start_fake_launchd_process()
    _write_fake_launchctl(fake_bin, bash_pid(sleeper.pid))
    # launchd-owned PID IS listening on the requested (default) port → real conflict.
    _write_fake_lsof(fake_bin, listening=True)

    try:
        result = run_ctl(
            tmp_path,
            "start",
            env={
                "PATH": f"{bash_path(fake_bin)}{os.pathsep}{os.environ.get('PATH', '')}",
                "HERMES_WEBUI_LAUNCHD_LABEL": "com.parantoux.hermes-webui",
            },
        )
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "Refusing to start a second Hermes WebUI" in combined
        assert "launchctl kickstart -k" in combined
        assert not (tmp_path / ".hermes" / "webui.pid").exists()
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_start_allows_alternate_port_while_launchd_job_runs_on_default(tmp_path):
    """A second instance on a DIFFERENT port must not be blocked by the launchd
    guard, even while the launchd-managed default instance is alive (#3291 fix /
    Codex regression-gate finding for v0.51.191)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    sleeper = start_fake_launchd_process()
    _write_fake_launchctl(fake_bin, bash_pid(sleeper.pid))
    # launchd-owned PID is alive but NOT listening on our (alternate) port → no conflict.
    _write_fake_lsof(fake_bin, listening=False)

    # A fake python so `start` "launches" without needing the real server.
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\ntrap 'exit 0' TERM INT\nwhile true; do sleep 1; done\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    started_pid = None
    try:
        result = run_ctl(
            tmp_path,
            "start",
            env={
                "PATH": f"{bash_path(fake_bin)}{os.pathsep}{os.environ.get('PATH', '')}",
                "HERMES_WEBUI_LAUNCHD_LABEL": "com.parantoux.hermes-webui",
                "HERMES_WEBUI_PORT": "18992",
                "HERMES_WEBUI_PYTHON": str(fake_python),
            },
        )
        combined = result.stdout + result.stderr
        assert "Refusing to start a second Hermes WebUI" not in combined, combined
        assert result.returncode == 0, combined
        pid_file = tmp_path / ".hermes" / "webui.pid"
        if pid_file.exists():
            started_pid = int(pid_file.read_text().strip())
    finally:
        if started_pid:
            _kill_tree(started_pid)
        sleeper.terminate()
        try:
            sleeper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_restart_reloads_matching_launchd_job_without_spawning_second_daemon(tmp_path):
    if sys.platform == "win32":
        pytest.skip("launchd lifecycle is a macOS path")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / "bootstrap.py").write_text("# test fixture\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_python_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)
    port = "18993"
    common_env = {
        "HERMES_WEBUI_PYTHON": str(fake_python),
        "FAKE_PYTHON_LOG": str(fake_python_log),
        "HERMES_WEBUI_PORT": port,
        "HERMES_WEBUI_DESKTOP_SHELL": "0",
    }

    first = run_ctl(
        tmp_path,
        "start",
        env={**common_env, "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1"},
        repo_root=repo_root,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    direct_pid = wait_for_pid_file(tmp_path / ".hermes" / "webui.pid")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_state = tmp_path / "launchctl.state"
    launchctl_log = tmp_path / "launchctl.log"
    launchctl_state.write_text("loaded\n", encoding="utf-8")
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env bash
            printf '%s\n' "$*" >> "${FAKE_LAUNCHCTL_LOG}"
            case "$1" in
              print)
                [[ "$(cat "${FAKE_LAUNCHCTL_STATE}" 2>/dev/null)" == "loaded" ]] || exit 1
                printf 'environment = {\n\tHERMES_WEBUI_PORT => %s\n}\n' "${FAKE_LAUNCHCTL_PORT}"
                ;;
              bootout)
                printf 'unloaded\n' > "${FAKE_LAUNCHCTL_STATE}"
                ;;
              bootstrap)
                printf 'loaded\n' > "${FAKE_LAUNCHCTL_STATE}"
                ;;
              kickstart) ;;
              *) exit 1 ;;
            esac
            """
        ).lstrip(),
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    plist = tmp_path / "Library" / "LaunchAgents" / "com.parantoux.hermes-webui.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<?xml version='1.0'?><plist><dict/></plist>\n", encoding="utf-8")

    restarted = run_ctl(
        tmp_path,
        "restart",
        env={
            **common_env,
            "PATH": f"{bash_path(fake_bin)}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_LAUNCHCTL_STATE": str(launchctl_state),
            "FAKE_LAUNCHCTL_LOG": str(launchctl_log),
            "FAKE_LAUNCHCTL_PORT": port,
        },
        repo_root=repo_root,
    )
    combined = restarted.stdout + restarted.stderr
    assert restarted.returncode == 0, combined
    assert "Stopping launchd-managed Hermes WebUI" in combined
    assert "Started launchd-managed Hermes WebUI" in combined
    assert_process_exits(direct_pid)
    assert not (tmp_path / ".hermes" / "webui.pid").exists()

    calls = launchctl_log.read_text(encoding="utf-8")
    assert "bootout gui/" in calls
    assert "bootstrap gui/" in calls
    assert "kickstart gui/" in calls
    assert fake_python_log.read_text(encoding="utf-8").count("fake-python args:") == 1


def test_reset_electron_uses_resolved_port_pid_file_and_clears_cache_dirs(tmp_path):
    hermes_home = tmp_path / ".hermes"
    state_dir = hermes_home / "webui"
    state_dir.mkdir(parents=True)
    user_data_dir = tmp_path / "electron-user-data"
    for relative in ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage", "Service Worker/ScriptCache"):
        target = user_data_dir / relative
        target.mkdir(parents=True)
        (target / "stale").write_text("old", encoding="utf-8")

    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    pid_file = state_dir / "desktop-shell-8788.pid"
    pid_file.write_text(str(sleeper.pid), encoding="utf-8")
    try:
        result = run_ctl(
            tmp_path,
            "reset-electron",
            env={
                "HERMES_WEBUI_PORT": "8788",
                "HERMES_WEBUI_DESKTOP_USER_DATA_DIR": str(user_data_dir),
                "HERMES_WEBUI_DESKTOP_LAUNCH_METHOD": "background",
            },
        )

        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Resetting Electron desktop shell for 127.0.0.1:8788" in combined
        assert not pid_file.exists()
        sleeper.wait(timeout=3)
        assert not (user_data_dir / "Cache").exists()
        assert not (user_data_dir / "Code Cache").exists()
        assert not (user_data_dir / "GPUCache").exists()
        assert not (user_data_dir / "Service Worker" / "CacheStorage").exists()
        assert not (user_data_dir / "Service Worker" / "ScriptCache").exists()
    finally:
        if sleeper.poll() is None:
            sleeper.terminate()
            try:
                sleeper.wait(timeout=3)
            except subprocess.TimeoutExpired:
                sleeper.kill()


def test_start_electron_requires_an_existing_webui_and_never_starts_backend(tmp_path):
    result = run_ctl(
        tmp_path,
        "start-electron",
        env={"HERMES_WEBUI_PORT": str(free_tcp_port())},
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 1, combined
    assert "Cannot start Electron: no Hermes WebUI is listening" in combined
    assert not (tmp_path / ".hermes" / "webui.pid").exists()


def test_start_electron_opens_only_shell_for_existing_healthy_webui(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    sidecar = repo_root / "scripts" / "start-browser-workbench-desktop.sh"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        "#!/usr/bin/env bash\n"
        "sleep 2 & app_pid=$!\n"
        "printf '%s\\n' \"$app_pid\" > \"$HERMES_WEBUI_DESKTOP_PID_FILE.app\"\n"
        "printf 'url=%s\\nhealth=%s\\nwebui_pid=%s\\n' \"$HERMES_WEBUI_URL\" \"$HERMES_WEBUI_HEALTH_URL\" \"$HERMES_WEBUI_PID\" > \"$FAKE_ELECTRON_LOG\"\n"
        "wait \"$app_pid\"\n",
        encoding="utf-8",
    )
    sidecar.chmod(0o755)

    port = free_tcp_port()
    fake_server = repo_root / "server.py"
    fake_server.write_text(
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "import sys\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'{\\\"status\\\":\\\"ok\\\"}')\n"
        "    def log_message(self, *args):\n"
        "        pass\n"
        "ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    server = subprocess.Popen(
        [sys.executable, str(fake_server), str(port)],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    electron_log = tmp_path / "electron-start.log"
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        result = run_ctl(
            tmp_path,
            "start-electron",
            env={
                "HERMES_WEBUI_PORT": str(port),
                "HERMES_WEBUI_DESKTOP_SHELL": "0",
                "HERMES_WEBUI_DESKTOP_LAUNCH_METHOD": "background",
                "FAKE_ELECTRON_LOG": str(electron_log),
            },
            repo_root=repo_root,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Electron desktop shell launch requested" in combined
        assert "Electron desktop app is running" in combined
        launched = wait_for_file_text(electron_log, contains="webui_pid=")
        assert f"url=http://127.0.0.1:{port}" in launched
        assert f"health=http://127.0.0.1:{port}/health" in launched
        assert f"webui_pid={server.pid}" in launched
        assert not (tmp_path / ".hermes" / "webui.pid").exists()
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()


def test_start_stops_existing_electron_shell_for_resolved_port_before_launch(tmp_path):
    hermes_home = tmp_path / ".hermes"
    state_dir = hermes_home / "webui"
    state_dir.mkdir(parents=True)
    fake_python = tmp_path / "fake-python"
    fake_log = tmp_path / "fake-python.log"
    write_fake_python(fake_python)
    old_shell = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    (state_dir / "desktop-shell-18993.pid").write_text(str(old_shell.pid), encoding="utf-8")
    started_pid = None
    try:
        result = run_ctl(
            tmp_path,
            "start",
            env={
                "HERMES_WEBUI_PYTHON": str(fake_python),
                "FAKE_PYTHON_LOG": str(fake_log),
                "HERMES_WEBUI_PORT": "18993",
                "HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT": "1",
            },
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Stopping Electron desktop shell" in combined
        old_shell.wait(timeout=3)
        assert not (state_dir / "desktop-shell-18993.pid").exists()
        started_pid = wait_for_pid_file(hermes_home / "webui.pid")
    finally:
        if started_pid:
            stop = run_ctl(tmp_path, "stop")
            assert stop.returncode == 0, stop.stderr + stop.stdout
            _kill_tree(started_pid)
            assert_process_exits(started_pid)
        if old_shell.poll() is None:
            old_shell.terminate()
            try:
                old_shell.wait(timeout=3)
            except subprocess.TimeoutExpired:
                old_shell.kill()


def test_stop_closes_electron_app_for_dotenv_port_even_when_webui_is_stopped(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_ctl_repo(repo_root)
    (repo_root / ".env").write_text("HERMES_WEBUI_PORT=18994\n", encoding="utf-8")
    hermes_home = tmp_path / ".hermes"
    state_dir = hermes_home / "webui"
    state_dir.mkdir(parents=True)
    old_app = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    (state_dir / "desktop-shell-18994.pid.app").write_text(str(old_app.pid), encoding="utf-8")
    try:
        result = run_ctl(tmp_path, "stop", repo_root=repo_root, load_dotenv=True)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Stopping Electron desktop app" in combined
        assert "Hermes WebUI is stopped" in combined
        old_app.wait(timeout=3)
        assert not (state_dir / "desktop-shell-18994.pid.app").exists()
    finally:
        if old_app.poll() is None:
            old_app.terminate()
            try:
                old_app.wait(timeout=3)
            except subprocess.TimeoutExpired:
                old_app.kill()


def test_logs_supports_non_following_line_count(tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    log_file = hermes_home / "webui.log"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = run_ctl(tmp_path, "logs", "--lines", "2", "--no-follow")

    assert result.returncode == 0
    assert result.stdout == "two\nthree\n"
