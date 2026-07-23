#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/health_probe.sh
. "${REPO_ROOT}/scripts/lib/health_probe.sh"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
PID_FILE="${HERMES_WEBUI_PID_FILE:-${HERMES_HOME}/webui.pid}"
LOG_FILE="${HERMES_WEBUI_LOG_FILE:-${HERMES_HOME}/webui.log}"
STATE_FILE="${HERMES_WEBUI_CTL_STATE_FILE:-${HERMES_HOME}/webui.ctl.env}"
DEFAULT_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-${HERMES_HOME}/webui}"
DEFAULT_LAUNCHD_LABEL="${HERMES_WEBUI_LAUNCHD_LABEL:-com.parantoux.hermes-webui}"

usage() {
  cat <<'EOF'
Usage: ./ctl.sh <command> [args]

Commands:
  start [bootstrap args...]   Start Hermes WebUI as a background daemon
  start-electron [bootstrap args...]
                              Open only the Electron shell for an already-running WebUI
  stop                        Stop the daemon started by ctl.sh
  restart [bootstrap args...] Stop, then start again
  reset-electron [bootstrap args...]
                              Stop/reset the Electron desktop shell for the resolved port
  status                      Show daemon, host/port, log, and health status
  logs [--lines N] [--follow|--no-follow]
                              Show the daemon log (defaults to tail -n 100 -f)
EOF
}

ensure_home() {
  mkdir -p "${HERMES_HOME}" "${DEFAULT_STATE_DIR}"
}

_apply_env_file_safely() {
  local env_file="$1"
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    if [[ "${line}" =~ ^export[[:space:]]+(.+)$ ]]; then
      line="${BASH_REMATCH[1]}"
      line="${line#${line%%[![:space:]]*}}"
    fi
    [[ "${line}" == *=* ]] || continue

    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case "${key}" in
      UID | GID | EUID | EGID | PPID) continue ;;
    esac

    value="${value#${value%%[![:space:]]*}}"
    if [[ "${value}" =~ ^\"(([^\"\\]|\\.)*)\"([[:space:]]*\#.*)?[[:space:]]*$ ]]; then
      value="${BASH_REMATCH[1]}"
      value="$(printf '%s' "$value" | awk '{
        i = 1
        len = length($0)
        while (i <= len) {
          c = substr($0, i, 1)
          if (c == "\\" && i < len) {
            nc = substr($0, i+1, 1)
            if (nc == "n") printf "\n"
            else if (nc == "r") printf "\r"
            else if (nc == "t") printf "\t"
            else if (nc == "\"") printf "\""
            else if (nc == "\\") printf "\\"
            else { printf "\\%s", nc }
            i += 2
          } else {
            printf "%s", c
            i++
          }
        }
      }')"
    elif [[ "${value}" =~ ^\'([^\']*)\'([[:space:]]*\#.*)?[[:space:]]*$ ]]; then
      value="${BASH_REMATCH[1]}"
    else
      value="${value%%[[:space:]]\#*}"
      value="${value%${value##*[![:space:]]}}"
    fi

    export "${key}=${value}"
  done < "${env_file}"
}

_load_repo_dotenv_preserving_env() {
  [[ "${HERMES_WEBUI_NO_DOTENV:-0}" == "1" ]] && return 0
  local env_file="${REPO_ROOT}/.env"
  [[ -f "${env_file}" ]] || return 0

  local -a preserved=()
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    [[ -z "${line}" || "${line}" == \#* || "${line}" != *=* ]] && continue
    key="${line%%=*}"
    if [[ "${key}" =~ ^export[[:space:]]+(.+)$ ]]; then
      key="${BASH_REMATCH[1]}"
    fi
    key="${key//[[:space:]]/}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    # Skip shell-readonly names (UID/GID/EUID/EGID/PPID); re-exporting them
    # below would abort under `set -euo pipefail` with "readonly variable".
    case "${key}" in
      UID | GID | EUID | EGID | PPID) continue ;;
    esac
    if [[ -n "${!key+x}" ]]; then
      value="${!key}"
      preserved+=("${key}=${value}")
    fi
  done < "${env_file}"

  _apply_env_file_safely "${env_file}"

  local assignment
  if [[ ${#preserved[@]} -gt 0 ]]; then
    for assignment in "${preserved[@]}"; do
      export "${assignment}"
    done
  fi
}

_load_hermes_dotenv() {
  # Also load ~/.hermes/.env so that ${VAR} references in config.yaml can
  # resolve against provider credentials defined in the Hermes env file.
  # Repo .env takes precedence (loaded above); variables already exported
  # into the shell environment (including those just set by repo .env) are
  # captured in preserved[] before _apply_env_file_safely runs and are
  # restored afterwards, so this acts as a fallback source for vars the
  # repo .env did not define.
  [[ "${HERMES_WEBUI_NO_DOTENV:-0}" == "1" ]] && return 0
  local hermes_home="${HERMES_HOME:-${HOME}/.hermes}"
  local hermes_env="${hermes_home}/.env"
  [[ -f "${hermes_env}" ]] || return 0

  local -a preserved=()
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    [[ -z "${line}" || "${line}" == \#* || "${line}" != *=* ]] && continue
    key="${line%%=*}"
    if [[ "${key}" =~ ^export[[:space:]]+(.+)$ ]]; then
      key="${BASH_REMATCH[1]}"
    fi
    key="${key//[[:space:]]/}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case "${key}" in
      UID | GID | EUID | EGID | PPID) continue ;;
    esac
    if [[ -n "${!key+x}" ]]; then
      value="${!key}"
      preserved+=("${key}=${value}")
    fi
  done < "${hermes_env}"

  _apply_env_file_safely "${hermes_env}"

  local assignment
  if [[ ${#preserved[@]} -gt 0 ]]; then
    for assignment in "${preserved[@]}"; do
      export "${assignment}"
    done
  fi
}

_find_python() {
  if [[ -n "${HERMES_WEBUI_PYTHON:-}" ]]; then
    printf '%s\n' "${HERMES_WEBUI_PYTHON}"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "[ctl] Python 3 is required to run bootstrap.py" >&2
    return 1
  fi
}

_parse_launch_binding() {
  CTL_HOST="${HERMES_WEBUI_HOST:-127.0.0.1}"
  CTL_PORT="${HERMES_WEBUI_PORT:-8787}"
  local arg next_is_host=0 saw_port=0
  for arg in "$@"; do
    if (( next_is_host )); then
      CTL_HOST="${arg}"
      next_is_host=0
      continue
    fi
    case "${arg}" in
      --host)
        next_is_host=1
        ;;
      --host=*)
        CTL_HOST="${arg#--host=}"
        ;;
      --*)
        ;;
      *)
        if (( ! saw_port )) && [[ "${arg}" =~ ^[0-9]+$ ]]; then
          CTL_PORT="${arg}"
          saw_port=1
        fi
        ;;
    esac
  done
}

_build_bootstrap_args() {
  CTL_BOOTSTRAP_ARGS=()
  local arg next_is_host=0 saw_port=0
  for arg in "$@"; do
    if (( next_is_host )); then
      next_is_host=0
      continue
    fi
    case "${arg}" in
      --host)
        next_is_host=1
        ;;
      --host=*)
        ;;
      --*)
        CTL_BOOTSTRAP_ARGS+=("${arg}")
        ;;
      *)
        if (( ! saw_port )) && [[ "${arg}" =~ ^[0-9]+$ ]]; then
          saw_port=1
        else
          CTL_BOOTSTRAP_ARGS+=("${arg}")
        fi
        ;;
    esac
  done
}

_write_state() {
  local pid="$1" host="$2" port="$3" python_exe="${4:-}"
  local state_dir="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  {
    printf 'PID=%q\n' "${pid}"
    printf 'REPO_ROOT=%q\n' "${REPO_ROOT}"
    printf 'PYTHON_EXE=%q\n' "${python_exe}"
    printf 'HOST=%q\n' "${host}"
    printf 'PORT=%q\n' "${port}"
    printf 'LOG_FILE=%q\n' "${LOG_FILE}"
    printf 'STATE_DIR=%q\n' "${state_dir}"
    printf 'STARTED_AT=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${STATE_FILE}"
}

_load_state_if_present() {
  if [[ -f "${STATE_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${STATE_FILE}"
  fi
}

_pid_from_file() {
  [[ -f "${PID_FILE}" ]] || return 1
  local pid
  pid="$(tr -d '[:space:]' < "${PID_FILE}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${pid}"
}

_is_alive() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

_is_windows_bash() {
  [[ "${OS:-}" == "Windows_NT" ]] && return 0
  case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

_windows_bash_path() {
  local path="${1//\\//}" drive rest
  if [[ "${path}" =~ ^([A-Za-z]):(.*)$ ]]; then
    drive="${BASH_REMATCH[1],,}"
    rest="${BASH_REMATCH[2]}"
    printf '/%s%s\n' "${drive}" "${rest}"
    return
  fi
  printf '%s\n' "${path}"
}

_windows_pid_for_bash_pid() {
  local pid="$1"
  ps -p "${pid}" -l 2>/dev/null | awk 'NR == 2 { print $4 }'
}

_stop_webui_pid() {
  local pid="$1" signal="${2:-TERM}"
  if _is_windows_bash && command -v taskkill >/dev/null 2>&1; then
    local winpid
    winpid="$(_windows_pid_for_bash_pid "${pid}")"
    if [[ "${winpid}" =~ ^[0-9]+$ ]]; then
      taskkill //F //T //PID "${winpid}" >/dev/null 2>&1 || true
      return
    fi
  fi
  if [[ "${signal}" == "KILL" ]]; then
    kill -KILL "${pid}" >/dev/null 2>&1 || true
  else
    kill "${pid}" >/dev/null 2>&1 || true
  fi
}

_proc_args() {
  local pid="$1" args
  args="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  if [[ -n "${args}" ]]; then
    printf '%s\n' "${args}"
    return
  fi
  if _is_windows_bash; then
    local winpid
    winpid="$(_windows_pid_for_bash_pid "${pid}")"
    if [[ "${winpid}" =~ ^[0-9]+$ ]] && command -v wmic >/dev/null 2>&1; then
      args="$(wmic process where "ProcessId=${winpid}" get CommandLine //value 2>/dev/null | sed -n 's/^CommandLine=//p' | tr -d '\r')"
      if [[ -n "${args}" ]]; then
        printf '%s\n' "${args}"
        return
      fi
    fi
    ps -p "${pid}" -f 2>/dev/null | awk 'NR == 2 { for (i = 8; i <= NF; i++) printf "%s%s", (i == 8 ? "" : " "), $i; print "" }'
  fi
}

_is_owned_webui_pid() {
  local pid="$1" args args_slash state_repo="" state_repo_slash="" state_repo_win="" state_repo_win_slash="" state_python="" state_python_slash="" state_python_bash=""
  [[ -f "${STATE_FILE}" ]] || return 1
  _load_state_if_present
  state_repo="${REPO_ROOT:-}"
  state_python="${PYTHON_EXE:-}"
  state_repo_slash="${state_repo//\\//}"
  state_python_slash="${state_python//\\//}"
  if _is_windows_bash; then
    state_repo_win="$(cygpath -w "${state_repo}" 2>/dev/null || true)"
    state_repo_win_slash="${state_repo_win//\\//}"
  fi
  if [[ -n "${state_python}" ]] && _is_windows_bash; then
    state_python_bash="$(_windows_bash_path "${state_python}")"
  fi
  [[ "${state_repo}" == "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" ]] || return 1
  args="$(_proc_args "${pid}")"
  [[ -n "${args}" ]] || return 1
  args_slash="${args//\\//}"
  [[ "${args_slash}" == *"${state_repo_slash}/bootstrap.py"* ||
     "${args_slash}" == *"${state_repo_slash}/server.py"* ||
     "${args_slash}" == *"${state_repo_slash}/start.sh"* ||
     ( -n "${state_repo_win_slash}" && "${args_slash}" == *"${state_repo_win_slash}/bootstrap.py"* ) ||
     ( -n "${state_repo_win_slash}" && "${args_slash}" == *"${state_repo_win_slash}/server.py"* ) ||
     ( -n "${state_repo_win_slash}" && "${args_slash}" == *"${state_repo_win_slash}/start.sh"* ) ||
     ( -n "${state_python}" && "${args}" == *"${state_python}"* ) ||
     ( -n "${state_python_slash}" && "${args_slash}" == *"${state_python_slash}"* ) ||
     ( -n "${state_python_bash}" && "${args_slash}" == *"${state_python_bash}"* ) ]]
}

_is_repo_webui_pid() {
  local pid="$1" args args_slash repo_slash repo_win="" repo_win_slash=""
  args="$(_proc_args "${pid}")"
  [[ -n "${args}" ]] || return 1
  args_slash="${args//\\//}"
  repo_slash="${REPO_ROOT//\\//}"
  if _is_windows_bash; then
    repo_win="$(cygpath -w "${REPO_ROOT}" 2>/dev/null || true)"
    repo_win_slash="${repo_win//\\//}"
  fi
  [[ "${args_slash}" == *"${repo_slash}/bootstrap.py"* ||
     "${args_slash}" == *"${repo_slash}/server.py"* ||
     "${args_slash}" == *"${repo_slash}/start.sh"* ||
     ( -n "${repo_win_slash}" && "${args_slash}" == *"${repo_win_slash}/bootstrap.py"* ) ||
     ( -n "${repo_win_slash}" && "${args_slash}" == *"${repo_win_slash}/server.py"* ) ||
     ( -n "${repo_win_slash}" && "${args_slash}" == *"${repo_win_slash}/start.sh"* ) ]]
}

_webui_listener_pid_for_port() {
  local port="$1" pid
  [[ "${port}" =~ ^[0-9]+$ ]] || return 1
  command -v lsof >/dev/null 2>&1 || return 1
  while IFS= read -r pid; do
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if _is_alive "${pid}" && _is_repo_webui_pid "${pid}"; then
      printf '%s\n' "${pid}"
      return 0
    fi
  done < <(lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)
  return 1
}

_current_pid() {
  local pid
  pid="$(_pid_from_file)" || return 1
  if _is_alive "${pid}" && _is_owned_webui_pid "${pid}"; then
    printf '%s\n' "${pid}"
    return 0
  fi
  return 1
}

_clear_stale_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    rm -f "${PID_FILE}" "${STATE_FILE}"
    echo "[ctl] Removed stale PID file: ${PID_FILE}"
  fi
}

_desktop_shell_user_data_dir() {
  if [[ -n "${HERMES_WEBUI_DESKTOP_USER_DATA_DIR:-}" ]]; then
    printf '%s\n' "${HERMES_WEBUI_DESKTOP_USER_DATA_DIR}"
    return 0
  fi
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin) printf '%s\n' "${HOME}/Library/Application Support/hermes-webui-desktop" ;;
    *) printf '%s\n' "${XDG_CONFIG_HOME:-${HOME}/.config}/hermes-webui-desktop" ;;
  esac
}

_desktop_shell_pid_file_for_port() {
  local port="$1" state_dir="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  printf '%s\n' "${state_dir}/desktop-shell-${port}.pid"
}

_desktop_shell_launchd_label_for_port() {
  local port="$1"
  printf '%s.electron.%s\n' "${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}}" "${port}"
}

_remove_desktop_shell_launchd_job() {
  local port="$1" label
  [[ "${HERMES_WEBUI_DESKTOP_LAUNCH_METHOD:-launchd}" != "background" ]] || return 0
  [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]] || return 0
  command -v launchctl >/dev/null 2>&1 || return 0
  label="$(_desktop_shell_launchd_label_for_port "${port}")"
  launchctl remove "${label}" >/dev/null 2>&1 || true
}

_desktop_shell_enabled() {
  case "${HERMES_WEBUI_DESKTOP_SHELL:-}" in
    1 | true | TRUE | True | yes | YES | on | ON | enabled | ENABLED | Enabled) return 0 ;;
    *) return 1 ;;
  esac
}

_pid_file_process_alive() {
  local pid_file="$1" pid=""
  [[ -f "${pid_file}" ]] || return 1
  pid="$(head -n 1 "${pid_file}" 2>/dev/null | tr -cd '0-9' || true)"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  _is_alive "${pid}"
}

_ensure_desktop_shell_for_running_webui() {
  # `start` is also an idempotent ensure operation. If the WebUI server is
  # already healthy but its optional Electron shell was closed or crashed,
  # reopen only the shell instead of forcing a server restart.
  local webui_pid="$1" host="$2" port="$3"
  _desktop_shell_enabled || return 0
  _is_windows_bash && return 0

  local sidecar_script="${REPO_ROOT}/scripts/start-browser-workbench-desktop.sh"
  if [[ ! -f "${sidecar_script}" ]]; then
    echo "[ctl] Electron desktop shell helper not found: ${sidecar_script}" >&2
    return 0
  fi

  local pid_file state_dir connect_host scheme webui_url bash_path
  state_dir="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  mkdir -p "${state_dir}"
  pid_file="$(_desktop_shell_pid_file_for_port "${port}")"
  if _pid_file_process_alive "${pid_file}.app"; then
    echo "[ctl] Electron desktop app is already running for ${host}:${port}"
    return 0
  fi

  # The helper may be between spawning Electron and writing the app PID, or it
  # may still be cleaning up after a window crash. Give that transition a
  # brief chance to settle. This avoids both a duplicate launch and the race
  # where a second helper sees the old lock, exits, and leaves no app behind.
  local settle_i
  if _pid_file_process_alive "${pid_file}.runner" || _pid_file_process_alive "${pid_file}"; then
    for settle_i in {1..20}; do
      if _pid_file_process_alive "${pid_file}.app"; then
        echo "[ctl] Electron desktop app is already running for ${host}:${port}"
        return 0
      fi
      if ! _pid_file_process_alive "${pid_file}.runner" && ! _pid_file_process_alive "${pid_file}"; then
        break
      fi
      sleep 0.1
    done
    if _pid_file_process_alive "${pid_file}.runner" || _pid_file_process_alive "${pid_file}"; then
      echo "[ctl] Electron desktop shell launch is already in progress for ${host}:${port}"
      return 0
    fi
  fi

  connect_host="${host}"
  case "${connect_host}" in
    "" | 0.0.0.0 | :: | "[::]") connect_host="127.0.0.1" ;;
  esac
  if [[ "${connect_host}" == *:* && "${connect_host}" != \[*\] ]]; then
    connect_host="[${connect_host}]"
  fi

  scheme="$(hermes_webui_probe_scheme)"
  if hermes_webui_probe_health "${host}" "${port}" "/health" 2 >/dev/null 2>&1; then
    scheme="${_HERMES_WEBUI_PROBE_SCHEME:-${scheme}}"
  fi
  webui_url="${scheme}://${connect_host}:${port}"
  bash_path="$(command -v bash 2>/dev/null || printf '/bin/bash')"

  if [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1 && [[ "${HERMES_WEBUI_DESKTOP_LAUNCH_METHOD:-launchd}" != "background" ]]; then
    # A plain `nohup ... &` remains in the invoking terminal/process coalition
    # on macOS and can be reaped as soon as ctl exits. Submit a transient user
    # LaunchAgent instead, so Electron survives the command that opened it.
    local desktop_label
    desktop_label="$(_desktop_shell_launchd_label_for_port "${port}")"
    launchctl remove "${desktop_label}" >/dev/null 2>&1 || true
    launchctl submit -l "${desktop_label}" -- /usr/bin/env \
      "HOME=${HOME}" \
      "PATH=${PATH}" \
      "HERMES_WEBUI_URL=${webui_url}" \
      "HERMES_WEBUI_HEALTH_URL=${webui_url}/health" \
      "HERMES_WEBUI_PID=${webui_pid}" \
      "HERMES_WEBUI_STATE_DIR=${state_dir}" \
      "HERMES_WEBUI_DESKTOP_PID_FILE=${pid_file}" \
      "HERMES_WEBUI_DESKTOP_RESET=${HERMES_WEBUI_DESKTOP_RESET:-0}" \
      "HERMES_WEBUI_DESKTOP_DIR=${HERMES_WEBUI_DESKTOP_DIR:-${REPO_ROOT}/desktop}" \
      "HERMES_WEBUI_DESKTOP_USER_DATA_DIR=${HERMES_WEBUI_DESKTOP_USER_DATA_DIR:-}" \
      "${bash_path}" "${sidecar_script}"
  else
    (
      trap '' HUP
      export HERMES_WEBUI_URL="${webui_url}"
      export HERMES_WEBUI_HEALTH_URL="${webui_url}/health"
      export HERMES_WEBUI_PID="${webui_pid}"
      export HERMES_WEBUI_STATE_DIR="${state_dir}"
      export HERMES_WEBUI_DESKTOP_PID_FILE="${pid_file}"
      exec nohup "${bash_path}" "${sidecar_script}" >/dev/null 2>&1
    ) &
  fi
  echo "[ctl] Electron desktop shell launch requested for ${webui_url}"
}

_stop_pid_file_process() {
  local pid_file="$1" label="${2:-process}" pid=""
  [[ -f "${pid_file}" ]] || return 0
  pid="$(head -n 1 "${pid_file}" 2>/dev/null | tr -cd '0-9' || true)"
  if [[ -n "${pid}" ]] && _is_alive "${pid}"; then
    echo "[ctl] Stopping ${label} (PID ${pid})"
    kill -TERM "${pid}" >/dev/null 2>&1 || true
    local i
    for i in {1..40}; do
      if ! _is_alive "${pid}"; then
        rm -f "${pid_file}"
        return 0
      fi
      sleep 0.1
    done
    echo "[ctl] ${label} did not exit after SIGTERM; sending SIGKILL" >&2
    kill -KILL "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${pid_file}"
}

_stop_desktop_shell_pid_file() {
  local pid_file="$1"
  _stop_pid_file_process "${pid_file}.app" "Electron desktop app"
  _stop_pid_file_process "${pid_file}.runner" "Electron desktop shell helper"
  _stop_pid_file_process "${pid_file}" "Electron desktop shell"
  rm -f "${pid_file}.app" "${pid_file}.runner" >/dev/null 2>&1 || true
}

_stop_desktop_shell_for_port() {
  local port="$1" pid_file
  [[ "${port}" =~ ^[0-9]+$ ]] || return 0
  _remove_desktop_shell_launchd_job "${port}"
  pid_file="$(_desktop_shell_pid_file_for_port "${port}")"
  _stop_desktop_shell_pid_file "${pid_file}"
}

_reset_electron_cache_dir() {
  local user_data_dir="$1"
  [[ -n "${user_data_dir}" && -d "${user_data_dir}" ]] || return 0
  rm -rf \
    "${user_data_dir}/Cache" \
    "${user_data_dir}/Code Cache" \
    "${user_data_dir}/GPUCache" \
    "${user_data_dir}/Service Worker/CacheStorage" \
    "${user_data_dir}/Service Worker/ScriptCache"
  echo "[ctl] Reset Electron desktop shell caches: ${user_data_dir}"
}

reset_electron_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  export HERMES_WEBUI_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  mkdir -p "${HERMES_WEBUI_STATE_DIR}"
  _parse_launch_binding "$@"
  local pid_file user_data_dir
  pid_file="$(_desktop_shell_pid_file_for_port "${CTL_PORT}")"
  user_data_dir="$(_desktop_shell_user_data_dir)"
  echo "[ctl] Resetting Electron desktop shell for ${CTL_HOST}:${CTL_PORT}"
  _stop_desktop_shell_pid_file "${pid_file}"
  _reset_electron_cache_dir "${user_data_dir}"
}

start_electron_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  _load_hermes_dotenv
  export HERMES_WEBUI_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  mkdir -p "${HERMES_WEBUI_STATE_DIR}"
  _parse_launch_binding "$@"

  # This explicit command is intentionally narrower than `start`: it must
  # never bootstrap, restart, or otherwise mutate the WebUI backend. Resolve
  # the existing listener and fail clearly when there is nothing to attach to.
  local webui_pid
  if ! webui_pid="$(_webui_listener_pid_for_port "${CTL_PORT}" 2>/dev/null)"; then
    echo "[ctl] Cannot start Electron: no Hermes WebUI is listening on ${CTL_HOST}:${CTL_PORT}" >&2
    echo "[ctl] Start the backend first with ./ctl.sh start" >&2
    return 1
  fi
  if ! hermes_webui_probe_health "${CTL_HOST}" "${CTL_PORT}" "/health" 2 >/dev/null 2>&1; then
    echo "[ctl] Cannot start Electron: Hermes WebUI on ${CTL_HOST}:${CTL_PORT} is not healthy" >&2
    return 1
  fi

  # An explicit start-electron request overrides the optional auto-launch
  # preference for this invocation only. The helper remains idempotent when an
  # app or launcher is already alive for the resolved port.
  export HERMES_WEBUI_DESKTOP_SHELL=1
  _ensure_desktop_shell_for_running_webui "${webui_pid}" "${CTL_HOST}" "${CTL_PORT}"

  # Do not report success merely because the detached helper was requested.
  # Keeping this command alive until Electron publishes its app PID also avoids
  # shells/supervisors reaping a just-launched helper before it has crossed the
  # dependency-check/build phase.
  local pid_file app_pid="" runner_pid="" i
  pid_file="$(_desktop_shell_pid_file_for_port "${CTL_PORT}")"
  for i in {1..300}; do
    if _pid_file_process_alive "${pid_file}.app"; then
      app_pid="$(head -n 1 "${pid_file}.app" 2>/dev/null | tr -cd '0-9' || true)"
      echo "[ctl] Electron desktop app is running (PID ${app_pid})"
      return 0
    fi
    if [[ -f "${pid_file}.runner" ]]; then
      runner_pid="$(head -n 1 "${pid_file}.runner" 2>/dev/null | tr -cd '0-9' || true)"
      # The file may still contain the previous helper PID for a few
      # milliseconds after _ensure_desktop_shell_for_running_webui returns.
      # Give the newly detached helper time to replace it before treating a
      # dead PID as a launch failure.
      if (( i > 20 )) && [[ -n "${runner_pid}" ]] && ! _is_alive "${runner_pid}"; then
        break
      fi
    fi
    sleep 0.1
  done

  echo "[ctl] Electron desktop app did not start; see ${HERMES_WEBUI_DESKTOP_LOG_FILE:-${HERMES_WEBUI_STATE_DIR}/desktop-shell-${CTL_PORT}.log}" >&2
  _stop_desktop_shell_pid_file "${pid_file}"
  return 1
}

_pid_listens_on_port() {
  # Best-effort check that PID $1 has a listening socket on TCP port $2.
  # macOS (where launchd exists) ships lsof; if we can't determine ownership we
  # return 2 ("unknown") so the caller can fall back conservatively rather than
  # guess. Never blocks on a hard failure.
  local pid="$1" port="$2"
  [[ "${pid}" =~ ^[0-9]+$ && "${port}" =~ ^[0-9]+$ ]] || return 2
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -p "${pid}" -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0   # PID is listening on that port → real conflict
    fi
    return 1     # PID is alive but NOT listening on that port → no conflict
  fi
  return 2       # can't determine
}

_launchd_service_target() {
  local label="${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}}"
  printf 'gui/%s/%s\n' "$(id -u)" "${label}"
}

_launchd_plist_path() {
  local label="${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}}"
  printf '%s/Library/LaunchAgents/%s.plist\n' "${HOME}" "${label}"
}

_launchd_job_loaded() {
  [[ "${HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT:-0}" == "1" ]] && return 1
  command -v launchctl >/dev/null 2>&1 || return 1
  launchctl print "$(_launchd_service_target)" >/dev/null 2>&1
}

_launchd_configured_port() {
  command -v launchctl >/dev/null 2>&1 || return 1
  local target launchd_out port="" plist
  target="$(_launchd_service_target)"
  launchd_out="$(launchctl print "${target}" 2>/dev/null || true)"
  if [[ -n "${launchd_out}" ]]; then
    port="$(printf '%s\n' "${launchd_out}" | awk '/HERMES_WEBUI_PORT => / { print $3; exit }')"
  fi

  plist="$(_launchd_plist_path)"
  if [[ ! "${port}" =~ ^[0-9]+$ && -f "${plist}" ]] && command -v plutil >/dev/null 2>&1; then
    port="$(plutil -extract EnvironmentVariables.HERMES_WEBUI_PORT raw -o - "${plist}" 2>/dev/null || true)"
  fi
  [[ "${port}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${port}"
}

_launchd_job_manages_port() {
  local wanted_port="$1" configured_port=""
  [[ "${HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT:-0}" == "1" ]] && return 1
  configured_port="$(_launchd_configured_port 2>/dev/null)" || return 1
  [[ "${configured_port}" == "${wanted_port}" ]]
}

_stop_launchd_webui_job() {
  local target
  target="$(_launchd_service_target)"
  if _launchd_job_loaded; then
    echo "[ctl] Stopping launchd-managed Hermes WebUI (${target})"
    if ! launchctl bootout "${target}" >/dev/null 2>&1; then
      echo "[ctl] Failed to stop launchd job ${target}" >&2
      return 1
    fi
  fi

  local i
  for i in {1..40}; do
    _launchd_job_loaded || return 0
    sleep 0.1
  done
  echo "[ctl] launchd job ${target} did not stop" >&2
  return 1
}

_start_launchd_webui_job() {
  local target plist domain
  target="$(_launchd_service_target)"
  plist="$(_launchd_plist_path)"
  domain="gui/$(id -u)"

  if ! _launchd_job_loaded; then
    if [[ ! -f "${plist}" ]]; then
      echo "[ctl] launchd plist not found: ${plist}" >&2
      return 1
    fi
    echo "[ctl] Loading launchd-managed Hermes WebUI (${target})"
    if ! launchctl bootstrap "${domain}" "${plist}" >/dev/null 2>&1; then
      echo "[ctl] Failed to load launchd job ${target}" >&2
      return 1
    fi
  fi

  # bootstrap starts RunAtLoad jobs itself. kickstart is still needed for a
  # loaded-but-idle job, and is harmless when launchd has already scheduled it.
  launchctl kickstart "${target}" >/dev/null 2>&1 || true
  echo "[ctl] Started launchd-managed Hermes WebUI (${target})"
}

_launchd_webui_pid() {
  [[ "${HERMES_WEBUI_CTL_ALLOW_LAUNCHD_CONFLICT:-0}" == "1" ]] && return 1
  command -v launchctl >/dev/null 2>&1 || return 1
  local label="${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}}"
  [[ -n "${label}" ]] || return 1
  local uid launchd_out pid
  uid="$(id -u)"
  launchd_out="$(launchctl print "gui/${uid}/${label}" 2>/dev/null)" || return 1
  pid="$(printf '%s\n' "${launchd_out}" | awk '/^[[:space:]]*pid = / {print $3; exit}')"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  (( pid > 0 )) || return 1
  _is_alive "${pid}" || return 1
  # Only treat the launchd job as a conflict for the port we are about to bind.
  # A second instance on a DIFFERENT port (e.g. HERMES_WEBUI_PORT=8788 for a
  # test build) does not collide with the launchd-managed default and must be
  # allowed to start (#3291 over-block fix). When port ownership can't be
  # determined (no lsof), fall back to the conservative previous behavior of
  # only guarding the default port so non-default ports are never wrongly blocked.
  local want_port="${CTL_PORT:-${HERMES_WEBUI_PORT:-8787}}"
  _pid_listens_on_port "${pid}" "${want_port}"
  case "$?" in
    0) printf '%s\n' "${pid}"; return 0 ;;   # launchd job listens on our port → block
    1) return 1 ;;                            # launchd job on a different port → allow
    *)                                        # unknown: only guard the default port
      if [[ "${want_port}" == "8787" ]]; then
        printf '%s\n' "${pid}"; return 0
      fi
      return 1 ;;
  esac
}

start_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  _load_hermes_dotenv
  export HERMES_WEBUI_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}"
  mkdir -p "${HERMES_WEBUI_STATE_DIR}"
  _parse_launch_binding "$@"
  _build_bootstrap_args "$@"
  export HERMES_WEBUI_HOST="${CTL_HOST}"
  export HERMES_WEBUI_PORT="${CTL_PORT}"

  local existing_pid launchd_pid
  if _launchd_job_manages_port "${CTL_PORT}"; then
    if existing_pid="$(_webui_listener_pid_for_port "${CTL_PORT}" 2>/dev/null)"; then
      launchd_pid="$(_launchd_webui_pid 2>/dev/null || true)"
      if [[ -n "${launchd_pid}" && "${launchd_pid}" == "${existing_pid}" ]]; then
        echo "[ctl] launchd-managed Hermes WebUI is already running (PID ${existing_pid})"
        _ensure_desktop_shell_for_running_webui "${existing_pid}" "${CTL_HOST}" "${CTL_PORT}" || true
        return 0
      fi
      if _launchd_job_loaded; then
        echo "[ctl] A standalone Hermes WebUI (PID ${existing_pid}) conflicts with the loaded launchd job on port ${CTL_PORT}." >&2
        echo "[ctl] Run ./ctl.sh restart to reconcile them into one launchd-managed instance." >&2
        return 2
      fi
      echo "[ctl] Hermes WebUI is already running on ${CTL_HOST}:${CTL_PORT} (PID ${existing_pid})"
      _ensure_desktop_shell_for_running_webui "${existing_pid}" "${CTL_HOST}" "${CTL_PORT}" || true
      return 0
    fi
    _clear_stale_pid >/dev/null 2>&1 || true
    _start_launchd_webui_job
    return 0
  fi

  if existing_pid="$(_current_pid 2>/dev/null)"; then
    echo "[ctl] Hermes WebUI is already running (PID ${existing_pid})"
    _ensure_desktop_shell_for_running_webui "${existing_pid}" "${CTL_HOST}" "${CTL_PORT}" || true
    return 0
  fi
  if existing_pid="$(_webui_listener_pid_for_port "${CTL_PORT}" 2>/dev/null)"; then
    echo "[ctl] Hermes WebUI is already running on ${CTL_HOST}:${CTL_PORT} (PID ${existing_pid}; recovered missing ctl state)"
    local python_exe_for_state
    python_exe_for_state="$(_find_python)"
    printf '%s\n' "${existing_pid}" > "${PID_FILE}"
    _write_state "${existing_pid}" "${CTL_HOST}" "${CTL_PORT}" "${python_exe_for_state}"
    _ensure_desktop_shell_for_running_webui "${existing_pid}" "${CTL_HOST}" "${CTL_PORT}" || true
    return 0
  fi
  if launchd_pid="$(_launchd_webui_pid 2>/dev/null)"; then
    echo "[ctl] Refusing to start a second Hermes WebUI while launchd job ${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}} is running (PID ${launchd_pid})." >&2
    echo "[ctl] Use launchctl kickstart -k gui/$(id -u)/${HERMES_WEBUI_LAUNCHD_LABEL:-${DEFAULT_LAUNCHD_LABEL}} or disable the launchd job before using ctl.sh start." >&2
    return 2
  fi
  _stop_desktop_shell_for_port "${CTL_PORT}"
  _clear_stale_pid >/dev/null 2>&1 || true

  local python_exe pid
  python_exe="$(_find_python)"
  : >> "${LOG_FILE}"
  (
    cd "${REPO_ROOT}"
    trap '' HUP
    export HERMES_WEBUI_PRESERVE_ENV=1
    exec nohup "${python_exe}" "${REPO_ROOT}/bootstrap.py" --no-browser --foreground --host "${CTL_HOST}" "${CTL_PORT}" ${CTL_BOOTSTRAP_ARGS[@]+"${CTL_BOOTSTRAP_ARGS[@]}"}
  ) >> "${LOG_FILE}" 2>&1 &
  pid=$!

  printf '%s\n' "${pid}" > "${PID_FILE}"
  _write_state "${pid}" "${CTL_HOST}" "${CTL_PORT}" "${python_exe}"
  sleep 0.15
  if ! _is_alive "${pid}"; then
    echo "[ctl] Hermes WebUI failed to stay running. Log: ${LOG_FILE}" >&2
    rm -f "${PID_FILE}" "${STATE_FILE}"
    return 1
  fi
  echo "[ctl] Started Hermes WebUI (PID ${pid})"
  echo "[ctl] Bound: ${CTL_HOST}:${CTL_PORT}"
  echo "[ctl] Log: ${LOG_FILE}"
}

stop_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  _load_state_if_present
  local stop_port="${PORT:-${HERMES_WEBUI_PORT:-8787}}" stop_state_dir="${STATE_DIR:-${HERMES_WEBUI_STATE_DIR:-${DEFAULT_STATE_DIR}}}"
  export HERMES_WEBUI_STATE_DIR="${stop_state_dir}"
  # Disable KeepAlive before terminating either the server or Electron. If the
  # order is reversed, launchd can recreate both while ctl.sh is still stopping
  # them and the command reports success against a newly running instance.
  if _launchd_job_manages_port "${stop_port}" && _launchd_job_loaded; then
    _stop_launchd_webui_job
  fi
  _stop_desktop_shell_for_port "${stop_port}"
  local pid listener_pid
  if ! pid="$(_pid_from_file 2>/dev/null)"; then
    if listener_pid="$(_webui_listener_pid_for_port "${stop_port}" 2>/dev/null)"; then
      echo "[ctl] Stopping Hermes WebUI orphan on ${stop_port} (PID ${listener_pid})"
      _stop_webui_pid "${listener_pid}" TERM
      local j
      for j in {1..50}; do
        if ! _is_alive "${listener_pid}"; then
          rm -f "${PID_FILE}" "${STATE_FILE}"
          echo "[ctl] Stopped"
          return 0
        fi
        sleep 0.1
      done
      echo "[ctl] Orphan process did not exit after SIGTERM; sending SIGKILL" >&2
      _stop_webui_pid "${listener_pid}" KILL
      rm -f "${PID_FILE}" "${STATE_FILE}"
      return 0
    fi
    echo "[ctl] Hermes WebUI is stopped"
    rm -f "${PID_FILE}" "${STATE_FILE}"
    return 0
  fi

  if ! _is_alive "${pid}" || ! _is_owned_webui_pid "${pid}"; then
    _clear_stale_pid
    if listener_pid="$(_webui_listener_pid_for_port "${stop_port}" 2>/dev/null)"; then
      echo "[ctl] Stopping Hermes WebUI orphan on ${stop_port} (PID ${listener_pid})"
      _stop_webui_pid "${listener_pid}" TERM
      local k
      for k in {1..50}; do
        if ! _is_alive "${listener_pid}"; then
          echo "[ctl] Stopped"
          return 0
        fi
        sleep 0.1
      done
      echo "[ctl] Orphan process did not exit after SIGTERM; sending SIGKILL" >&2
      _stop_webui_pid "${listener_pid}" KILL
    fi
    return 0
  fi

  echo "[ctl] Stopping Hermes WebUI (PID ${pid})"
  _stop_webui_pid "${pid}" TERM
  local i
  for i in {1..50}; do
    if ! _is_alive "${pid}"; then
      rm -f "${PID_FILE}" "${STATE_FILE}"
      echo "[ctl] Stopped"
      return 0
    fi
    sleep 0.1
  done

  echo "[ctl] Process did not exit after SIGTERM; sending SIGKILL" >&2
  _stop_webui_pid "${pid}" KILL
  rm -f "${PID_FILE}" "${STATE_FILE}"
}

_health_line() {
  local host="$1" port="$2" url scheme result
  scheme="$(hermes_webui_probe_scheme)"
  url="${scheme}://${host}:${port}/health"
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "unknown (curl/wget not found; ${url})"
    return 0
  fi
  if result="$(hermes_webui_probe_health "${host}" "${port}" "/health" 2)"; then
    if command -v python3 >/dev/null 2>&1; then
      printf '%s' "${result}" | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
    sessions=data.get("sessions", data.get("session_count", "?"))
    active=data.get("active_streams", "?")
    status=data.get("status", "ok")
    print(f"ok ({sessions} sessions, {active} active streams)" if status == "ok" else status)
except Exception:
    print("ok")'
    else
      echo "ok"
    fi
  else
    echo "unreachable (${url})"
  fi
}

status_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  _load_hermes_dotenv
  _load_state_if_present
  local host="${HOST:-${HERMES_WEBUI_HOST:-127.0.0.1}}"
  local port="${PORT:-${HERMES_WEBUI_PORT:-8787}}"
  local log_path="${LOG_FILE}"
  local pid uptime health

  if pid="$(_current_pid 2>/dev/null)"; then
    uptime="$(ps -p "${pid}" -o etime= 2>/dev/null | sed 's/^ *//' || true)"
    health="$(_health_line "${host}" "${port}")"
    echo "● hermes-webui — running"
    echo "  PID:     ${pid}"
    echo "  Uptime:  ${uptime:-unknown}"
    echo "  Bound:   ${host}:${port}"
    echo "  Log:     ${log_path}"
    echo "  Health:  ${health}"
  elif pid="$(_webui_listener_pid_for_port "${port}" 2>/dev/null)"; then
    uptime="$(ps -p "${pid}" -o etime= 2>/dev/null | sed 's/^ *//' || true)"
    health="$(_health_line "${host}" "${port}")"
    echo "● hermes-webui — running (untracked by ctl; restart will recover it)"
    echo "  PID:     ${pid}"
    echo "  Uptime:  ${uptime:-unknown}"
    echo "  Bound:   ${host}:${port}"
    echo "  Log:     ${log_path}"
    echo "  Health:  ${health}"
  else
    [[ -f "${PID_FILE}" ]] && _clear_stale_pid >/dev/null 2>&1 || true
    echo "● hermes-webui — stopped"
    echo "  PID:     -"
    echo "  Bound:   ${host}:${port}"
    echo "  Log:     ${log_path}"
    echo "  Health:  not checked"
  fi
}

logs_cmd() {
  ensure_home
  local lines=100 follow=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lines)
        shift
        lines="${1:-}"
        [[ "${lines}" =~ ^[0-9]+$ ]] || { echo "[ctl] --lines requires a number" >&2; return 2; }
        ;;
      --lines=*)
        lines="${1#--lines=}"
        [[ "${lines}" =~ ^[0-9]+$ ]] || { echo "[ctl] --lines requires a number" >&2; return 2; }
        ;;
      --follow|-f)
        follow=1
        ;;
      --no-follow)
        follow=0
        ;;
      *)
        echo "[ctl] Unknown logs option: $1" >&2
        return 2
        ;;
    esac
    shift
  done
  touch "${LOG_FILE}"
  if (( follow )); then
    tail -n "${lines}" -f "${LOG_FILE}"
  else
    tail -n "${lines}" "${LOG_FILE}"
  fi
}

restart_cmd() {
  ensure_home
  _load_repo_dotenv_preserving_env
  _load_hermes_dotenv
  _parse_launch_binding "$@"

  # A launchd KeepAlive job must be stopped before any standalone ctl daemon.
  # Otherwise launchd immediately respawns while ctl.sh is starting its own
  # process, leaving the two instances racing for the same port and repeatedly
  # relaunching Electron.
  if _launchd_job_manages_port "${CTL_PORT}"; then
    _stop_launchd_webui_job
    stop_cmd
    reset_electron_cmd "$@"
    rm -f "${PID_FILE}" "${STATE_FILE}"
    _start_launchd_webui_job
    return 0
  fi

  stop_cmd
  reset_electron_cmd "$@"
  export HERMES_WEBUI_DESKTOP_RESET=1
  start_cmd "$@"
}

cmd="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "${cmd}" in
  start) start_cmd "$@" ;;
  start-electron) start_electron_cmd "$@" ;;
  stop) stop_cmd ;;
  restart) restart_cmd "$@" ;;
  reset-electron) reset_electron_cmd "$@" ;;
  status) status_cmd ;;
  logs) logs_cmd "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "[ctl] Unknown command: ${cmd}" >&2; usage >&2; exit 2 ;;
esac
