#!/bin/sh
# Bootstrap without Python, then run the portable client. No persistent PATH changes.
set -eu
umask 077
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
config="$script_dir/../assets/toolchain.conf"
state=${ALSTYLE_CLIENT_STATE:-"$HOME/.al-style-deploy"}
system=$(uname -s)
download_dir=
trap '[ -z "$download_dir" ] || rm -rf "$download_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() { printf '%s\n' "$*" >&2; exit 1; }
setting() { awk -F= -v key="$1" '$1 == key { print $2 }' "$config"; }
probe_python() {
    [ -x "$1" ] || return 1
    # Apple stubs can open an installer. They are not installed runtimes.
    if [ "$system" = Darwin ] && [ "$1" = /usr/bin/python3 ]; then
        xcode-select -p >/dev/null 2>&1 || return 1
    fi
    "$1" -I -c 'import sys, ssl; sys.exit(1) if sys.version_info < (3, 12) else print(sys.executable)' 2>/dev/null
}
find_python() {
    if [ -f "$state/python-path.txt" ]; then
        if probe_python "$(cat "$state/python-path.txt")"; then return 0; fi
    fi
    for name in python3 python3.14 python3.13 python3.12 python; do
        candidate=$(command -v "$name" || true)
        if [ -n "$candidate" ] && probe_python "$candidate"; then return 0; fi
    done
    return 1
}
find_git() {
    candidate=$(command -v git || true)
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        if [ "$system" != Darwin ] || [ "$candidate" != /usr/bin/git ] || xcode-select -p >/dev/null 2>&1; then
            if "$candidate" --version >/dev/null 2>&1; then printf '%s\n' "$candidate"; return 0; fi
        fi
    fi
    [ "$system" = Darwin ] || return 1
    for candidate in /opt/homebrew/bin/git /usr/local/bin/git /Library/Developer/CommandLineTools/usr/bin/git; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        "$candidate" --version >/dev/null 2>&1 || continue
        printf '%s\n' "$candidate"
        return 0
    done
    return 1
}
download() {
    if command -v curl >/dev/null 2>&1; then
        curl --disable --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
            --connect-timeout 20 --max-time 600 --output "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only --timeout=30 --tries=2 -q -O "$2" "$1"
    else
        fail 'Setup needs curl or wget. Ask IT to provide a standard HTTPS download tool.'
    fi
}
verify_archive() {
    expected=$(setting "sha256.$2")
    [ "${#expected}" -eq 64 ] || fail "No pinned checksum for $2; installation stopped."
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$1" | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$1" | awk '{print $1}')
    else
        fail 'Setup needs sha256sum or shasum; installation stopped before executing downloads.'
    fi
    [ "$actual" = "$expected" ] || fail "Checksum mismatch for $2; installation stopped."
}
install_python() {
    version=$(setting uv_version)
    uv="$state/tools/uv-$version/uv"
    if [ ! -x "$uv" ]; then
        case "$(uname -m)" in
            arm64|aarch64) arch=aarch64 ;;
            x86_64|amd64) arch=x86_64 ;;
            *) fail 'Automatic Python setup supports x64 and ARM64; ask IT about this architecture.' ;;
        esac
        case "$system" in
            Darwin) target="$arch-apple-darwin" ;;
            Linux)
                target="$arch-unknown-linux-gnu"
                if ldd --version 2>&1 | grep -qi musl; then target="$arch-unknown-linux-musl"; fi
                ;;
            *) fail 'Use scripts/run.ps1 on Windows; this shell launcher supports macOS/Linux.' ;;
        esac
        asset="uv-$target.tar.gz"
        download_dir=$(mktemp -d "$state/.download.XXXXXX")
        download "https://github.com/astral-sh/uv/releases/download/$version/$asset" "$download_dir/$asset"
        verify_archive "$download_dir/$asset" "$asset"
        tar -xzf "$download_dir/$asset" -C "$download_dir"
        mkdir -p "$state/tools/uv-$version"
        cp "$download_dir/uv-$target/uv" "$uv"
        chmod 700 "$uv"
        rm -rf "$download_dir"
        download_dir=
    fi
    printf '%s\n' 'Installing a private Python runtime for AL-STYLE deployment...' >&2
    UV_PYTHON_BIN_DIR="$state/bin" UV_PYTHON_INSTALL_DIR="$state/python" \
        "$uv" --no-config python install --no-bin 3.13
    python=$(UV_PYTHON_INSTALL_DIR="$state/python" "$uv" --no-config python find --managed-python 3.13)
    python=$(probe_python "$python") || fail 'Python installation did not produce a working Python 3.12+ runtime.'
    printf '%s\n' "$python" > "$state/python-path.txt"
}
as_admin() {
    if [ "$(id -u)" = 0 ]; then "$@"
    elif command -v sudo >/dev/null 2>&1; then sudo "$@"
    else fail 'Installing Git requires administrator access. Ask IT to run this setup.'
    fi
}
install_git() {
    case "$system" in
        Darwin)
            brew=$(command -v brew || true)
            for location in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                if [ -z "$brew" ] && [ -x "$location" ]; then brew=$location; fi
            done
            if [ -n "$brew" ]; then
                "$brew" install git
                prefix=$("$brew" --prefix git)
                PATH="$prefix/bin:$PATH"; export PATH
            else
                # Apple's own installation UI must be confirmed by the employee.
                xcode-select --install || true
                printf '%s\n' 'SETUP_PENDING: confirm/install Apple Command Line Tools in the system window, then rerun the same command. No deployment was started.' >&2
                exit 20
            fi
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                as_admin apt-get update
                as_admin apt-get install -y git ca-certificates
            elif command -v dnf >/dev/null 2>&1; then as_admin dnf install -y git ca-certificates
            elif command -v yum >/dev/null 2>&1; then as_admin yum install -y git ca-certificates
            elif command -v apk >/dev/null 2>&1; then as_admin apk add git ca-certificates
            elif command -v pacman >/dev/null 2>&1; then as_admin pacman -S --needed --noconfirm git ca-certificates
            elif command -v zypper >/dev/null 2>&1; then as_admin zypper --non-interactive install git ca-certificates
            else fail 'No supported system package manager found for Git; ask IT to install Git.'
            fi
            ;;
        *) fail 'Use scripts/run.ps1 on Windows; this shell launcher supports macOS/Linux.' ;;
    esac
    git=$(find_git) || fail 'Git installation did not finish. Resolve the system installer/IT restriction and rerun.'
}

mode=client
case "${1:-}" in
    --check-only|--setup-only) mode=$1; shift ;;
esac
python=$(find_python || true)
git=$(find_git || true)
if [ "$mode" = --check-only ]; then
    [ -n "$python" ] && [ -n "$git" ] || fail 'Setup needed: Python 3.12+ or Git is missing. Rerun without --check-only to install it.'
else
    mkdir -p "$state" "$state/tools"
    chmod 700 "$state" "$state/tools"
    if [ -z "$git" ]; then install_git; fi
    if [ -z "$python" ]; then install_python; fi
fi
printf 'Ready: Python %s; %s\n' "$("$python" -I -c 'import platform; print(platform.python_version())')" "$("$git" --version)" >&2
if [ "$mode" != client ]; then exit 0; fi
PATH="$(dirname "$git"):$PATH"; export PATH
exec "$python" -I "$script_dir/alstyle.py" "$@"
