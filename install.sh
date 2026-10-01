#!/bin/sh
# koda installer
#
#   curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh
#
# Installs koda (with the TUI extra) as an isolated uv tool, installing uv first
# if it is missing. Safe to re-run: an existing install is upgraded in place.
#
# Options — flags, or the environment variable next to them:
#   --ref <git-ref>       KODA_REF=<git-ref>     branch, tag or commit (default: main)
#   --python <version>    KODA_PYTHON=<version>  interpreter for the tool env (default: 3.13)
#   --no-modify-path      KODA_NO_MODIFY_PATH=1  never edit shell startup files
#   --uninstall                                  remove koda (uv tool uninstall koda)
#   -h, --help
#   KODA_SOURCE=<url>     package source (default: git+https://github.com/DONGRYEOLLEE1/koda)
#
# Pass flags through the pipe with:   ... | sh -s -- --ref <tag-or-commit>
#
# Why --python is always passed: koda needs Python >= 3.13, and `uv tool install`
# does not pick an interpreter from requires-python on its own. If uv's default
# interpreter is older, resolution fails, so we ask uv for a matching one (uv
# downloads it if none is installed).

set -eu

DEFAULT_SOURCE="git+https://github.com/DONGRYEOLLEE1/koda"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
DOCS_URL="https://github.com/DONGRYEOLLEE1/koda#readme"

# --- output -----------------------------------------------------------------

setup_colors() {
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
        BOLD=$(printf '\033[1m')
        DIM=$(printf '\033[2m')
        RED=$(printf '\033[31m')
        GREEN=$(printf '\033[32m')
        YELLOW=$(printf '\033[33m')
        ACCENT=$(printf '\033[38;5;105m')
        RESET=$(printf '\033[0m')
    else
        BOLD="" DIM="" RED="" GREEN="" YELLOW="" ACCENT="" RESET=""
    fi
}

say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$ACCENT$BOLD" "$RESET" "$*"; }
ok()   { printf '%s ok%s %s\n' "$GREEN" "$RESET" "$*"; }
note() { printf '%snote:%s %s\n' "$YELLOW" "$RESET" "$*"; }
err()  { printf '%serror:%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
koda installer

usage: install.sh [--ref <git-ref>] [--python <version>] [--no-modify-path] [--uninstall]

  --ref <git-ref>     branch, tag or commit to install      (env KODA_REF, default: main)
  --python <version>  Python for koda's tool environment   (env KODA_PYTHON, default: 3.13)
  --no-modify-path    do not edit shell startup files       (env KODA_NO_MODIFY_PATH=1)
  --uninstall         remove koda
  -h, --help          show this help

  KODA_SOURCE overrides the package source (default: git+https://github.com/DONGRYEOLLEE1/koda)

through a pipe:  curl -fsSL <url>/install.sh | sh -s -- --ref <tag-or-commit>
EOF
}

# --- helpers ----------------------------------------------------------------

has() { command -v "$1" >/dev/null 2>&1; }

detect_os() {
    kernel=$(uname -s 2>/dev/null || echo unknown)
    case "$kernel" in
        Darwin) OS=macos ;;
        Linux) OS=linux ;;
        MINGW* | MSYS* | CYGWIN* | Windows_NT)
            err "native Windows is not supported. Install WSL2 (https://learn.microsoft.com/windows/wsl/install) and run this command inside your WSL shell." ;;
        *)
            err "unsupported OS '$kernel'. koda runs on macOS and Linux (on Windows, use WSL2)." ;;
    esac
}

# Look where the official uv installer may have put uv, in its own order.
# The list is spelled out (not word-split) so a HOME with spaces still works.
find_uv_on_disk() {
    for d in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -n "$d" ] && [ -x "$d/uv" ]; then
            PATH="$d:$PATH"
            export PATH
            return 0
        fi
    done
    return 1
}

download() { # url dest
    if has curl; then
        curl -fsSL --retry 3 -o "$2" "$1"
    elif has wget; then
        wget -q -O "$2" "$1"
    else
        err "curl or wget is required to download uv"
    fi
}

ensure_uv() {
    if has uv || find_uv_on_disk; then
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}') ($(command -v uv))"
        return 0
    fi
    step "uv not found, installing it with the official installer ($UV_INSTALLER_URL)"
    tmp=$(mktemp "${TMPDIR:-/tmp}/uv-install.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    download "$UV_INSTALLER_URL" "$tmp" || err "could not download the uv installer"
    if [ "$NO_MODIFY_PATH" = 1 ]; then
        UV_NO_MODIFY_PATH=1 sh "$tmp" || err "the uv installer failed"
    else
        sh "$tmp" || err "the uv installer failed"
    fi
    rm -f "$tmp"
    trap - EXIT
    has uv || find_uv_on_disk || err "uv was installed but is not on PATH. Open a new shell and re-run this script."
    ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
}

installed_version() {
    uv tool list 2>/dev/null | sed -n 's/^koda v\([^ ]*\).*/\1/p' | head -n 1
}

# --- actions ----------------------------------------------------------------

do_uninstall() {
    has uv || find_uv_on_disk || err "uv not found, so there is no uv-managed koda to remove"
    if [ -z "$(installed_version)" ]; then
        say "koda is not installed; nothing to do."
        return 0
    fi
    step "removing koda"
    uv tool uninstall koda
    ok "koda removed. Workspace data (.koda/) and ~/.koda/ were left in place."
}

do_install() {
    detect_os
    step "installing koda on $OS"
    has git || err "git is required (uv fetches koda from a git repository). Install git and re-run."
    ensure_uv

    spec="koda[tui] @ ${SOURCE}@${REF}"
    previous=$(installed_version)
    if [ -n "$previous" ]; then
        step "koda $previous is already installed, upgrading from ref '$REF'"
    else
        step "installing koda from ref '$REF' (Python $PYTHON)"
    fi
    # --force makes a re-run an upgrade: uv re-resolves the ref to its current
    # commit and replaces the existing tool environment.
    uv tool install --force --python "$PYTHON" "$spec" ||
        err "uv tool install failed (source: $SOURCE, ref: $REF, python: $PYTHON)"

    bin_dir=$(uv tool dir --bin)
    koda_bin="$bin_dir/koda"
    "$koda_bin" --help >/dev/null 2>&1 || err "koda was installed but '$koda_bin --help' failed"
    version=$(installed_version)
    ok "koda ${version:-?} -> $koda_bin"

    on_path=0
    case ":$PATH:" in *":$bin_dir:"*) on_path=1 ;; esac
    if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -n "${GITHUB_PATH:-}" ]; then
        printf '%s\n' "$bin_dir" >>"$GITHUB_PATH"
    fi
    if [ "$on_path" = 0 ]; then
        if [ "$NO_MODIFY_PATH" != 1 ] && uv tool update-shell >/dev/null 2>&1; then
            note "added $bin_dir to PATH in your shell startup file. Open a new shell, or run:  export PATH=\"$bin_dir:\$PATH\""
        else
            note "$bin_dir is not on your PATH. Add it to your shell profile:  export PATH=\"$bin_dir:\$PATH\""
        fi
    else
        found=$(command -v koda 2>/dev/null || true)
        if [ -n "$found" ] && [ "$found" != "$koda_bin" ]; then
            note "another koda comes first on your PATH ($found). Remove it, or put $bin_dir before it."
        fi
    fi

    if [ "$OS" = linux ]; then
        if has bwrap; then
            ok "bubblewrap found: shell commands will run in the OS sandbox (set BH_SANDBOX=off if they fail inside a container)"
        else
            note "install bubblewrap (e.g. 'sudo apt install bubblewrap') to run shell commands in an OS sandbox. Without it only koda's deny list and approval gate apply."
        fi
    else
        note "macOS has no bubblewrap, so shell commands run without an OS sandbox; koda's deny list and approval gate still apply (see docs/safety.md)."
    fi

    say ""
    say "${BOLD}Next steps${RESET}"
    if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
        say "  ${DIM}# DEEPSEEK_API_KEY is already set in this shell${RESET}"
    else
        say "  export DEEPSEEK_API_KEY=sk-...    ${DIM}# default provider; OpenAI and llama.cpp also work${RESET}"
    fi
    say "  cd your-project"
    say "  koda                               ${DIM}# TUI, asks before writes and commands${RESET}"
    say "  koda run \"<task>\" --workspace . --yolo   ${DIM}# headless, auto-approve${RESET}"
    say ""
    say "Docs: $DOCS_URL"
}

main() {
    setup_colors
    REF="${KODA_REF:-main}"
    PYTHON="${KODA_PYTHON:-3.13}"
    SOURCE="${KODA_SOURCE:-$DEFAULT_SOURCE}"
    NO_MODIFY_PATH="${KODA_NO_MODIFY_PATH:-0}"
    action=install

    while [ $# -gt 0 ]; do
        case "$1" in
            --ref) [ $# -ge 2 ] || err "--ref needs a value"; REF=$2; shift 2 ;;
            --ref=*) REF=${1#--ref=}; shift ;;
            --python) [ $# -ge 2 ] || err "--python needs a value"; PYTHON=$2; shift 2 ;;
            --python=*) PYTHON=${1#--python=}; shift ;;
            --no-modify-path) NO_MODIFY_PATH=1; shift ;;
            --uninstall) action=uninstall; shift ;;
            -h | --help) usage; return 0 ;;
            *) usage >&2; err "unknown option: $1" ;;
        esac
    done

    case "$REF" in
        "" | *[!A-Za-z0-9._/-]*) err "invalid ref '$REF' (use a branch, tag or commit)" ;;
    esac

    if [ "$action" = uninstall ]; then
        do_uninstall
    else
        do_install
    fi
}

# Everything above only defines functions, so a truncated download runs nothing.
main "$@"
