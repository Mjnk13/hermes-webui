#!/usr/bin/env bash
set -euo pipefail

# Start the optional Electron native Browser Workbench shell for an already
# launching Hermes WebUI instance. This script is intentionally best-effort:
# WebUI startup must not fail just because Electron/npm is unavailable.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="${HERMES_WEBUI_DESKTOP_DIR:-${REPO_ROOT}/desktop}"
WEBUI_URL="${HERMES_WEBUI_URL:-}"
WEBUI_HEALTH_URL="${HERMES_WEBUI_HEALTH_URL:-${WEBUI_URL%/}/health}"
WEBUI_PID="${HERMES_WEBUI_PID:-}"
WAIT_SECONDS="${HERMES_WEBUI_DESKTOP_WAIT_SECONDS:-60}"
HEALTH_FAILURE_LIMIT="${HERMES_WEBUI_DESKTOP_HEALTH_FAILURE_LIMIT:-3}"
LOG_FILE="${HERMES_WEBUI_DESKTOP_LOG_FILE:-}"
PID_FILE="${HERMES_WEBUI_DESKTOP_PID_FILE:-}"
WEBUI_PID_FALLBACK_LOGGED=0
WEBUI_MONITOR_MODE=pid
WEBUI_HEALTH_FAILURES=0

if [[ ! "${HEALTH_FAILURE_LIMIT}" =~ ^[1-9][0-9]*$ ]]; then
  HEALTH_FAILURE_LIMIT=3
fi

if [[ -z "${WEBUI_URL}" ]]; then
  echo "[desktop-shell] HERMES_WEBUI_URL is required" >&2
  exit 0
fi

if [[ -z "${LOG_FILE}" ]]; then
  state_dir="${HERMES_WEBUI_STATE_DIR:-${HOME}/.hermes/webui}"
  mkdir -p "${state_dir}" 2>/dev/null || true
  port_part="${WEBUI_URL##*:}"
  port_part="${port_part%%/*}"
  [[ "${port_part}" =~ ^[0-9]+$ ]] || port_part="webui"
  LOG_FILE="${state_dir}/desktop-shell-${port_part}.log"
fi
mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
if [[ -z "${PID_FILE}" ]]; then
  state_dir="${HERMES_WEBUI_STATE_DIR:-${HOME}/.hermes/webui}"
  mkdir -p "${state_dir}" 2>/dev/null || true
  port_part="${WEBUI_URL##*:}"
  port_part="${port_part%%/*}"
  [[ "${port_part}" =~ ^[0-9]+$ ]] || port_part="webui"
  PID_FILE="${state_dir}/desktop-shell-${port_part}.pid"
fi
mkdir -p "$(dirname "${PID_FILE}")" 2>/dev/null || true
LOCK_DIR="${PID_FILE}.lock"
LOCK_ACQUIRED=0

log() {
  printf '[desktop-shell] %s\n' "$*" >> "${LOG_FILE}"
}

stop_pid_file_process() {
  local path="$1" label="${2:-process}" pid=""
  [[ -f "${path}" ]] || return 0
  pid="$(head -n 1 "${path}" 2>/dev/null | tr -cd '0-9' || true)"
  if [[ -n "${pid}" && "${pid}" != "$$" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    log "stopping stale ${label} PID ${pid} before launching ${WEBUI_URL}"
    kill "${pid}" >/dev/null 2>&1 || true
    local i
    for i in {1..40}; do
      if ! kill -0 "${pid}" >/dev/null 2>&1; then
        rm -f "${path}" >/dev/null 2>&1 || true
        return 0
      fi
      sleep 0.1
    done
    log "stale ${label} PID ${pid} did not exit; sending SIGKILL"
    kill -KILL "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${path}" >/dev/null 2>&1 || true
}

stop_existing_desktop_shell_for_port() {
  stop_pid_file_process "${PID_FILE}.app" "Electron desktop app"
  stop_pid_file_process "${PID_FILE}.runner" "Electron desktop shell helper"
  stop_pid_file_process "${PID_FILE}" "Electron desktop shell"
  rm -f "${PID_FILE}.app" "${PID_FILE}.runner" >/dev/null 2>&1 || true
}

acquire_launch_lock() {
  if mkdir "${LOCK_DIR}" 2>/dev/null; then
    LOCK_ACQUIRED=1
    return 0
  fi
  local path pid
  for path in "${PID_FILE}.runner" "${PID_FILE}"; do
    [[ -f "${path}" ]] || continue
    pid="$(head -n 1 "${path}" 2>/dev/null | tr -cd '0-9' || true)"
    if [[ -n "${pid}" && "${pid}" != "$$" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      log "another Electron desktop shell helper PID ${pid} is already managing ${WEBUI_URL}; skipping duplicate launch"
      exit 0
    fi
  done
  log "removing stale Electron desktop shell launch lock for ${WEBUI_URL}"
  rm -rf "${LOCK_DIR}" >/dev/null 2>&1 || true
  if mkdir "${LOCK_DIR}" 2>/dev/null; then
    LOCK_ACQUIRED=1
    return 0
  fi
  log "another Electron desktop shell helper is already launching ${WEBUI_URL}; skipping duplicate launch"
  exit 0
}

cleanup() {
  stop_pid_file_process "${PID_FILE}.app" "Electron desktop app"
  if [[ -n "${electron_pid:-}" ]] && kill -0 "${electron_pid}" >/dev/null 2>&1; then
    log "stopping Electron shell PID ${electron_pid}"
    kill "${electron_pid}" >/dev/null 2>&1 || true
    wait "${electron_pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${PID_FILE}" "${PID_FILE}.app" "${PID_FILE}.runner" >/dev/null 2>&1 || true
  if [[ "${LOCK_ACQUIRED}" == "1" ]]; then
    rm -rf "${LOCK_DIR}" >/dev/null 2>&1 || true
  fi
}
acquire_launch_lock
trap cleanup EXIT INT TERM HUP
stop_existing_desktop_shell_for_port
printf '%s\n' "$$" > "${PID_FILE}" 2>/dev/null || true
printf '%s\n' "$$" > "${PID_FILE}.runner" 2>/dev/null || true

webui_pid_alive() {
  [[ -z "${WEBUI_PID}" ]] && return 0
  if [[ "${WEBUI_MONITOR_MODE}" == "pid" ]] && kill -0 "${WEBUI_PID}" >/dev/null 2>&1; then
    WEBUI_HEALTH_FAILURES=0
    return 0
  fi
  # The sidecar can be launched by wrappers that briefly own the startup PID
  # before the long-lived WebUI process settles, or by a retry while an orphaned
  # WebUI is already healthy on the requested port. In those cases the PID can
  # disappear even though the WebUI URL the Electron shell should load is still
  # alive. Fall back to the health endpoint so the Electron app does not open
  # and immediately close just because the bootstrap/helper PID changed.
  if health_ok; then
    if [[ "${WEBUI_PID_FALLBACK_LOGGED}" != "1" ]]; then
      log "WebUI PID ${WEBUI_PID} is not alive but ${WEBUI_HEALTH_URL} is healthy; monitoring health instead"
      WEBUI_PID_FALLBACK_LOGGED=1
    fi
    WEBUI_MONITOR_MODE=health
    WEBUI_HEALTH_FAILURES=0
    return 0
  fi
  if [[ "${WEBUI_MONITOR_MODE}" == "health" ]]; then
    WEBUI_HEALTH_FAILURES=$((WEBUI_HEALTH_FAILURES + 1))
    if (( WEBUI_HEALTH_FAILURES < HEALTH_FAILURE_LIMIT )); then
      log "WebUI health check failed (${WEBUI_HEALTH_FAILURES}/${HEALTH_FAILURE_LIMIT}); keeping Electron open while health recovers"
      return 0
    fi
    log "WebUI health check failed ${WEBUI_HEALTH_FAILURES} consecutive times"
  fi
  return 1
}

health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "${WEBUI_HEALTH_URL}" >/dev/null 2>&1
    return $?
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$WEBUI_HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        sys.exit(0 if response.status < 500 else 1)
except Exception:
    sys.exit(1)
PY
    return $?
  fi
  return 1
}

electron_ok() {
  (
    cd "${DESKTOP_DIR}"
    node - <<'NODE' >/dev/null 2>&1
const fs = require('fs');
const path = require('path');
const electronPath = require('electron');
if (typeof electronPath !== 'string' || /[\r\n]/.test(electronPath) || !fs.existsSync(electronPath)) {
  process.exit(1);
}
if (process.platform === 'darwin') {
  const contentsDir = path.resolve(path.dirname(electronPath), '..');
  const frameworkPath = path.join(contentsDir, 'Frameworks', 'Electron Framework.framework', 'Electron Framework');
  if (!fs.existsSync(frameworkPath)) {
    process.exit(1);
  }
}
NODE
  )
}

electron_platform_path() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin) printf '%s\n' "Electron.app/Contents/MacOS/Electron" ;;
    MINGW*|MSYS*|CYGWIN*) printf '%s\n' "electron.exe" ;;
    *) printf '%s\n' "electron" ;;
  esac
}

repair_electron_path_txt() {
  local electron_dir electron_path
  electron_dir="$(
    cd "${DESKTOP_DIR}"
    node - <<'NODE' 2>/dev/null
const fs = require('fs');
try {
  console.log(fs.realpathSync('node_modules/electron'));
} catch (_error) {
  process.exit(1);
}
NODE
  )" || return 1
  electron_path="$(electron_platform_path)"
  if [[ -f "${electron_dir}/dist/${electron_path}" ]]; then
    if [[ "$(uname -s 2>/dev/null || echo unknown)" == "Darwin" && ! -f "${electron_dir}/dist/Electron.app/Contents/Frameworks/Electron Framework.framework/Electron Framework" ]]; then
      return 1
    fi
    printf '%s' "${electron_path}" > "${electron_dir}/path.txt" 2>/dev/null || return 1
    log "repaired Electron path.txt for existing binary"
    return 0
  fi
  return 1
}

repair_electron_binary() {
  electron_ok && return 0
  repair_electron_path_txt && electron_ok && return 0
  log "Electron package is present but the binary is not installed; repairing"
  (
    cd "${DESKTOP_DIR}"
    "${package_env_prefix[@]}" "${package_rebuild[@]}"
  ) >> "${LOG_FILE}" 2>&1 || true
  electron_ok && return 0
  repair_electron_path_txt && electron_ok && return 0
  if [[ -f "${DESKTOP_DIR}/node_modules/electron/install.js" ]]; then
    (
      cd "${DESKTOP_DIR}"
      node node_modules/electron/install.js
    ) >> "${LOG_FILE}" 2>&1 || true
  fi
  electron_ok && return 0
  repair_electron_path_txt && electron_ok && return 0
  if command -v unzip >/dev/null 2>&1; then
    log "Electron install script did not finish; extracting Electron artifact manually"
    (
      cd "${DESKTOP_DIR}"
      node <<'NODE'
const fs = require('fs');
const os = require('os');
const path = require('path');
const childProcess = require('child_process');
const electronDir = fs.realpathSync('node_modules/electron');
const { downloadArtifact } = require(require.resolve('@electron/get', { paths: [electronDir] }));

function platformPath() {
  const platform = process.env.npm_config_platform || os.platform();
  switch (platform) {
    case 'mas':
    case 'darwin':
      return 'Electron.app/Contents/MacOS/Electron';
    case 'freebsd':
    case 'openbsd':
    case 'linux':
      return 'electron';
    case 'win32':
      return 'electron.exe';
    default:
      throw new Error(`Electron builds are not available on platform: ${platform}`);
  }
}

(async () => {
  const pkg = require(path.join(electronDir, 'package.json'));
  const zipPath = await downloadArtifact({
    version: pkg.version,
    artifactName: 'electron',
    platform: process.env.npm_config_platform || process.platform,
    arch: process.env.npm_config_arch || process.arch,
    checksums: require(path.join(electronDir, 'checksums.json')),
  });
  const distPath = path.join(electronDir, 'dist');
  fs.rmSync(distPath, { recursive: true, force: true });
  fs.mkdirSync(distPath, { recursive: true });
  childProcess.execFileSync('unzip', ['-q', zipPath, '-d', distPath], { stdio: 'inherit' });
  fs.writeFileSync(path.join(electronDir, 'path.txt'), platformPath());
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
NODE
    ) >> "${LOG_FILE}" 2>&1 || true
  fi
  electron_ok
}

if [[ ! -d "${DESKTOP_DIR}" || ! -f "${DESKTOP_DIR}/package.json" ]]; then
  log "desktop package not found at ${DESKTOP_DIR}; skipping Electron shell"
  exit 0
fi
package_env_prefix=()
if command -v pnpm >/dev/null 2>&1; then
  package_manager="pnpm"
  package_env_prefix=(
    env
    "CI=${CI:-true}"
    "PNPM_CONFIG_CONFIRM_MODULES_PURGE=${PNPM_CONFIG_CONFIRM_MODULES_PURGE:-false}"
  )
  package_install=(pnpm install)
  package_rebuild=(pnpm rebuild electron)
  package_run_dev=(pnpm run dev)
elif command -v npm >/dev/null 2>&1; then
  package_manager="npm"
  package_install=(npm install --no-audit --no-fund)
  package_rebuild=(npm rebuild electron --foreground-scripts)
  package_run_dev=(npm run dev)
else
  log "pnpm/npm not found; skipping Electron shell for ${WEBUI_URL}"
  exit 0
fi

log "waiting for WebUI at ${WEBUI_HEALTH_URL} before launching Electron shell"
deadline=$((SECONDS + WAIT_SECONDS))
until health_ok; do
  if ! webui_pid_alive; then
    log "WebUI PID ${WEBUI_PID} exited before health check passed; skipping Electron shell"
    exit 0
  fi
  if (( SECONDS >= deadline )); then
    log "timed out after ${WAIT_SECONDS}s waiting for ${WEBUI_HEALTH_URL}; skipping Electron shell"
    exit 0
  fi
  sleep 0.5
done

if [[ "${package_manager}" == "pnpm" ]]; then
  log "ensuring desktop dependencies with ${package_manager} in ${DESKTOP_DIR}"
  (
    cd "${DESKTOP_DIR}"
    "${package_env_prefix[@]}" "${package_install[@]}"
  ) >> "${LOG_FILE}" 2>&1 || {
    log "${package_manager} install failed; skipping Electron shell"
    exit 0
  }
elif [[ ! -d "${DESKTOP_DIR}/node_modules/electron" || ! -d "${DESKTOP_DIR}/node_modules/electron-vite" ]]; then
  log "installing desktop dependencies with ${package_manager} in ${DESKTOP_DIR}"
  (
    cd "${DESKTOP_DIR}"
    "${package_install[@]}"
  ) >> "${LOG_FILE}" 2>&1 || {
    log "${package_manager} install failed; skipping Electron shell"
    exit 0
  }
fi

if ! repair_electron_binary; then
  log "Electron binary is still unavailable after repair; skipping Electron shell"
  exit 0
fi

if [[ "${HERMES_WEBUI_DESKTOP_RESET:-0}" == "1" ]]; then
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin) user_data_dir="${HERMES_WEBUI_DESKTOP_USER_DATA_DIR:-${HOME}/Library/Application Support/hermes-webui-desktop}" ;;
    *) user_data_dir="${HERMES_WEBUI_DESKTOP_USER_DATA_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/hermes-webui-desktop}" ;;
  esac
  if [[ -d "${user_data_dir}" ]]; then
    rm -rf \
      "${user_data_dir}/Cache" \
      "${user_data_dir}/Code Cache" \
      "${user_data_dir}/GPUCache" \
      "${user_data_dir}/Service Worker/CacheStorage" \
      "${user_data_dir}/Service Worker/ScriptCache" || true
    log "reset Electron cache directories under ${user_data_dir}"
  fi
fi

log "launching Electron Browser Workbench shell for ${WEBUI_URL}"
(
  cd "${DESKTOP_DIR}"
  HERMES_WEBUI_URL="${WEBUI_URL}" HERMES_WEBUI_DESKTOP_APP_PID_FILE="${PID_FILE}.app" "${package_env_prefix[@]}" "${package_run_dev[@]}"
) >> "${LOG_FILE}" 2>&1 &
electron_pid=$!
log "Electron shell PID ${electron_pid}; log ${LOG_FILE}"

while kill -0 "${electron_pid}" >/dev/null 2>&1; do
  if ! webui_pid_alive; then
    if [[ "${WEBUI_MONITOR_MODE}" == "health" ]]; then
      log "WebUI health remained unavailable; closing Electron shell"
    else
      log "WebUI PID ${WEBUI_PID} exited; closing Electron shell"
    fi
    break
  fi
  sleep 2
done
