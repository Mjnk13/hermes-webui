import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR = REPO_ROOT / "scripts" / "start-browser-workbench-desktop.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _make_fake_desktop(tmp_path: Path) -> Path:
    desktop = tmp_path / "desktop"
    electron_dir = desktop / "node_modules" / "electron"
    electron_dir.mkdir(parents=True)
    (electron_dir / "index.js").write_text(
        textwrap.dedent(
            """
            const fs = require('fs');
            const path = require('path');
            const pathFile = path.join(__dirname, 'path.txt');
            if (!fs.existsSync(pathFile)) {
              throw new Error('missing path.txt');
            }
            module.exports = path.join(__dirname, 'dist', fs.readFileSync(pathFile, 'utf-8'));
            """
        ).lstrip(),
        encoding="utf-8",
    )
    mac_binary = electron_dir / "dist" / "Electron.app" / "Contents" / "MacOS" / "Electron"
    mac_framework = electron_dir / "dist" / "Electron.app" / "Contents" / "Frameworks" / "Electron Framework.framework" / "Electron Framework"
    linux_binary = electron_dir / "dist" / "electron"
    mac_binary.parent.mkdir(parents=True)
    mac_framework.parent.mkdir(parents=True)
    linux_binary.parent.mkdir(parents=True, exist_ok=True)
    mac_binary.write_text("", encoding="utf-8")
    mac_framework.write_text("", encoding="utf-8")
    linux_binary.write_text("", encoding="utf-8")
    (desktop / "node_modules" / "electron-vite").mkdir(parents=True)
    (desktop / "package.json").write_text('{"scripts":{"dev":"fake"}}\n', encoding="utf-8")
    return desktop


def _make_fake_bin(tmp_path: Path, *, health_ok: bool) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "curl",
        f"""
        #!/usr/bin/env bash
        if [[ -n "${{HEALTH_CALLS_FILE:-}}" ]]; then
          count=0
          [[ -f "${{HEALTH_CALLS_FILE}}" ]] && count="$(head -n 1 "${{HEALTH_CALLS_FILE}}")"
          count=$((count + 1))
          printf '%s\n' "${{count}}" > "${{HEALTH_CALLS_FILE}}"
          if [[ -n "${{HEALTH_FAIL_ON_CALL:-}}" && "${{count}}" == "${{HEALTH_FAIL_ON_CALL}}" ]]; then
            exit 22
          fi
          if [[ -n "${{HEALTH_FAIL_FROM_CALL:-}}" && "${{count}}" -ge "${{HEALTH_FAIL_FROM_CALL}}" ]]; then
            exit 22
          fi
        fi
        exit {0 if health_ok else 22}
        """,
    )
    _write_executable(
        fake_bin / "pnpm",
        """
        #!/usr/bin/env bash
        printf 'pnpm args:%s\n' "$*" >> "${PNPM_LOG}"
        printf 'pnpm env CI=%s PNPM_CONFIG_CONFIRM_MODULES_PURGE=%s\n' "${CI:-}" "${PNPM_CONFIG_CONFIRM_MODULES_PURGE:-}" >> "${PNPM_LOG}"
        if [[ "${1:-}" == "run" && "${2:-}" == "dev" ]]; then
          printf '%s\n' "$$" > "${FAKE_DEV_PID_FILE}"
          [[ -n "${HERMES_WEBUI_DESKTOP_APP_PID_FILE:-}" ]] && printf '%s\n' "$$" > "${HERMES_WEBUI_DESKTOP_APP_PID_FILE}"
          trap '[[ -n "${FAKE_DEV_RESULT_FILE:-}" ]] && printf terminated > "${FAKE_DEV_RESULT_FILE}"; exit 143' TERM INT
          sleep "${FAKE_DEV_SLEEP:-0.6}"
          [[ -n "${FAKE_DEV_RESULT_FILE:-}" ]] && printf completed > "${FAKE_DEV_RESULT_FILE}"
        fi
        """,
    )
    return fake_bin


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for electron_ok()")
def test_sidecar_keeps_electron_open_when_startup_pid_exits_but_webui_health_is_ok(tmp_path):
    desktop = _make_fake_desktop(tmp_path)
    fake_bin = _make_fake_bin(tmp_path, health_ok=True)
    state_dir = tmp_path / "state"
    log_file = state_dir / "desktop-shell-19091.log"
    pid_file = state_dir / "desktop-shell-19091.pid"
    pnpm_log = tmp_path / "pnpm.log"
    fake_dev_pid_file = tmp_path / "fake-dev.pid"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "HERMES_WEBUI_DESKTOP_DIR": str(desktop),
            "HERMES_WEBUI_URL": "http://127.0.0.1:19091",
            "HERMES_WEBUI_HEALTH_URL": "http://127.0.0.1:19091/health",
            "HERMES_WEBUI_PID": "99999999",
            "HERMES_WEBUI_STATE_DIR": str(state_dir),
            "HERMES_WEBUI_DESKTOP_LOG_FILE": str(log_file),
            "HERMES_WEBUI_DESKTOP_PID_FILE": str(pid_file),
            "PNPM_LOG": str(pnpm_log),
            "FAKE_DEV_PID_FILE": str(fake_dev_pid_file),
            "FAKE_DEV_SLEEP": "0.6",
        }
    )

    result = subprocess.run(
        ["bash", str(SIDECAR)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=6,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    log_text = log_file.read_text(encoding="utf-8")
    assert "launching Electron Browser Workbench shell" in log_text
    assert "is not alive but http://127.0.0.1:19091/health is healthy" in log_text
    assert "WebUI PID 99999999 exited; closing Electron shell" not in log_text
    pnpm_text = pnpm_log.read_text(encoding="utf-8")
    assert "pnpm args:install" in pnpm_text
    assert "pnpm args:run dev" in pnpm_text
    assert "pnpm env CI=true PNPM_CONFIG_CONFIRM_MODULES_PURGE=false" in pnpm_text
    path_txt = (desktop / "node_modules" / "electron" / "path.txt").read_text(encoding="utf-8")
    assert path_txt in {"Electron.app/Contents/MacOS/Electron", "electron"}
    assert not path_txt.endswith("\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for electron_ok()")
def test_sidecar_tolerates_one_transient_health_failure_after_startup_pid_exits(tmp_path):
    desktop = _make_fake_desktop(tmp_path)
    fake_bin = _make_fake_bin(tmp_path, health_ok=True)
    state_dir = tmp_path / "state"
    log_file = state_dir / "desktop-shell-19093.log"
    pid_file = state_dir / "desktop-shell-19093.pid"
    pnpm_log = tmp_path / "pnpm.log"
    fake_dev_pid_file = tmp_path / "fake-dev.pid"
    fake_dev_result_file = tmp_path / "fake-dev-result"
    health_calls_file = tmp_path / "health-calls"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "HERMES_WEBUI_DESKTOP_DIR": str(desktop),
            "HERMES_WEBUI_URL": "http://127.0.0.1:19093",
            "HERMES_WEBUI_HEALTH_URL": "http://127.0.0.1:19093/health",
            "HERMES_WEBUI_PID": "99999999",
            "HERMES_WEBUI_STATE_DIR": str(state_dir),
            "HERMES_WEBUI_DESKTOP_LOG_FILE": str(log_file),
            "HERMES_WEBUI_DESKTOP_PID_FILE": str(pid_file),
            "PNPM_LOG": str(pnpm_log),
            "FAKE_DEV_PID_FILE": str(fake_dev_pid_file),
            "FAKE_DEV_RESULT_FILE": str(fake_dev_result_file),
            "FAKE_DEV_SLEEP": "4.5",
            "HEALTH_CALLS_FILE": str(health_calls_file),
            # Initial readiness succeeds, then the first monitor check switches
            # from the dead startup PID to health-only mode. The following
            # health probe fails once before recovering.
            "HEALTH_FAIL_ON_CALL": "3",
        }
    )

    result = subprocess.run(
        ["bash", str(SIDECAR)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=9,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    log_text = log_file.read_text(encoding="utf-8")
    assert "is not alive but http://127.0.0.1:19093/health is healthy" in log_text
    assert "WebUI PID 99999999 exited; closing Electron shell" not in log_text
    assert fake_dev_result_file.read_text(encoding="utf-8") == "completed"
    assert int(health_calls_file.read_text(encoding="utf-8")) >= 4


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for electron_ok()")
def test_sidecar_closes_after_consecutive_health_failures_in_fallback_mode(tmp_path):
    desktop = _make_fake_desktop(tmp_path)
    fake_bin = _make_fake_bin(tmp_path, health_ok=True)
    state_dir = tmp_path / "state"
    log_file = state_dir / "desktop-shell-19094.log"
    pid_file = state_dir / "desktop-shell-19094.pid"
    pnpm_log = tmp_path / "pnpm.log"
    fake_dev_pid_file = tmp_path / "fake-dev.pid"
    fake_dev_result_file = tmp_path / "fake-dev-result"
    health_calls_file = tmp_path / "health-calls"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "HERMES_WEBUI_DESKTOP_DIR": str(desktop),
            "HERMES_WEBUI_URL": "http://127.0.0.1:19094",
            "HERMES_WEBUI_HEALTH_URL": "http://127.0.0.1:19094/health",
            "HERMES_WEBUI_PID": "99999999",
            "HERMES_WEBUI_STATE_DIR": str(state_dir),
            "HERMES_WEBUI_DESKTOP_LOG_FILE": str(log_file),
            "HERMES_WEBUI_DESKTOP_PID_FILE": str(pid_file),
            "HERMES_WEBUI_DESKTOP_HEALTH_FAILURE_LIMIT": "2",
            "PNPM_LOG": str(pnpm_log),
            "FAKE_DEV_PID_FILE": str(fake_dev_pid_file),
            "FAKE_DEV_RESULT_FILE": str(fake_dev_result_file),
            "FAKE_DEV_SLEEP": "8",
            "HEALTH_CALLS_FILE": str(health_calls_file),
            # Readiness and the first fallback check succeed. Every later
            # monitor probe fails, so the configured threshold must close it.
            "HEALTH_FAIL_FROM_CALL": "3",
        }
    )

    result = subprocess.run(
        ["bash", str(SIDECAR)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=9,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    log_text = log_file.read_text(encoding="utf-8")
    assert "WebUI health check failed (1/2); keeping Electron open" in log_text
    assert "WebUI health check failed 2 consecutive times" in log_text
    assert "WebUI health remained unavailable; closing Electron shell" in log_text
    assert fake_dev_result_file.read_text(encoding="utf-8") == "terminated"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for electron_ok()")
def test_sidecar_skips_duplicate_launch_when_helper_lock_is_active(tmp_path):
    desktop = _make_fake_desktop(tmp_path)
    fake_bin = _make_fake_bin(tmp_path, health_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log_file = state_dir / "desktop-shell-19092.log"
    pid_file = state_dir / "desktop-shell-19092.pid"
    pnpm_log = tmp_path / "pnpm.log"
    fake_dev_pid_file = tmp_path / "fake-dev.pid"
    (state_dir / "desktop-shell-19092.pid.lock").mkdir()
    (state_dir / "desktop-shell-19092.pid.runner").write_text(f"{os.getpid()}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "HERMES_WEBUI_DESKTOP_DIR": str(desktop),
            "HERMES_WEBUI_URL": "http://127.0.0.1:19092",
            "HERMES_WEBUI_HEALTH_URL": "http://127.0.0.1:19092/health",
            "HERMES_WEBUI_PID": str(os.getpid()),
            "HERMES_WEBUI_STATE_DIR": str(state_dir),
            "HERMES_WEBUI_DESKTOP_LOG_FILE": str(log_file),
            "HERMES_WEBUI_DESKTOP_PID_FILE": str(pid_file),
            "PNPM_LOG": str(pnpm_log),
            "FAKE_DEV_PID_FILE": str(fake_dev_pid_file),
        }
    )

    result = subprocess.run(
        ["bash", str(SIDECAR)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=6,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    log_text = log_file.read_text(encoding="utf-8")
    assert "already managing http://127.0.0.1:19092; skipping duplicate launch" in log_text
    assert not pnpm_log.exists()
    assert not fake_dev_pid_file.exists()
