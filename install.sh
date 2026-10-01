#!/bin/sh
# Install/update only the public skill. No Git, Python or company credentials needed.
set -eu
umask 077
repository=alstyle-vibecoding/al-style-deploy
agent=
destination=
revision=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --agent) agent=$2; shift 2 ;;
        --destination) destination=$2; shift 2 ;;
        --ref) revision=$2; shift 2 ;;
        *) printf '%s\n' 'Usage: install.sh --agent codex|claude [--destination DIRECTORY] [--ref COMMIT]' >&2; exit 1 ;;
    esac
done
case "$agent" in
    codex) destination=${destination:-"$HOME/.agents/skills/al-style-deploy"} ;;
    claude) destination=${destination:-"$HOME/.claude/skills/al-style-deploy"} ;;
    *) printf '%s\n' '--agent codex or --agent claude is required.' >&2; exit 1 ;;
esac
fail() { printf '%s\n' "$*" >&2; exit 1; }
download() {
    if command -v curl >/dev/null 2>&1; then
        curl --disable --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
            --connect-timeout 20 --max-time 120 --output "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only --timeout=30 --tries=2 -q -O "$2" "$1"
    else
        fail 'Installation needs curl or wget; ask IT to provide an HTTPS download tool.'
    fi
}
digest() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        fail 'Installation needs sha256sum or shasum.'
    fi
}
case "$destination" in /*) ;; *) fail 'Destination must be an absolute path.' ;; esac
[ "$(basename "$destination")" = al-style-deploy ] || fail 'Destination must end in /al-style-deploy.'
[ ! -L "$destination" ] || fail 'Refusing to replace a symbolic link.'
if [ -e "$destination" ]; then
    [ -f "$destination/SKILL.md" ] && grep -q '^name: al-style-deploy$' "$destination/SKILL.md" \
        || fail 'Destination contains a different skill; it was not changed.'
fi
parent=$(dirname "$destination")
mkdir -p "$parent"
parent=$(CDPATH= cd -- "$parent" && pwd)
destination="$parent/al-style-deploy"
lock="$parent/.al-style-deploy.install-lock"
mkdir "$lock" 2>/dev/null || fail 'Another installation is active. Retry when it finishes.'
stage=
backup=
cleanup() {
    if [ -n "$backup" ] && [ ! -e "$destination" ]; then mv "$backup" "$destination"; fi
    [ -z "$stage" ] || rm -rf "$stage"
    rmdir "$lock"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
stage=$(mktemp -d "$parent/.al-style-deploy-download.XXXXXX")
if [ -z "$revision" ]; then
    download "https://api.github.com/repos/$repository/git/ref/heads/main" "$stage/ref.json"
    revision=$(sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([a-f0-9]*\)".*/\1/p' "$stage/ref.json" | head -n 1)
fi
[ "${#revision}" -eq 40 ] || fail 'GitHub did not return a valid commit.'
case "$revision" in *[!a-f0-9]*) fail 'Invalid commit.' ;; esac
base="https://raw.githubusercontent.com/$repository/$revision"
download "$base/skill-files.sha256" "$stage/manifest"
mkdir "$stage/skill"
count=0
while read -r expected path extra; do
    [ -z "$extra" ] && [ "${#expected}" -eq 64 ] || fail 'Invalid file manifest.'
    case "$expected" in *[!a-f0-9]*) fail 'Invalid checksum.' ;; esac
    case "$path" in skills/al-style-deploy/*) relative=${path#skills/al-style-deploy/} ;; *) fail 'Unexpected manifest path.' ;; esac
    case "$relative" in
        SKILL.md|scripts/alstyle.py|scripts/run.sh|scripts/run.ps1|scripts/install-from-github.sh|scripts/install-from-github.ps1|assets/toolchain.conf|assets/static.Dockerfile|references/setup.md|references/contract.md|references/install.md) ;;
        *) fail 'File is outside the reviewed skill package.' ;;
    esac
    target="$stage/skill/$relative"
    [ ! -e "$target" ] || fail 'Duplicate manifest entry.'
    mkdir -p "$(dirname "$target")"
    download "$base/$path" "$target"
    [ "$(digest "$target")" = "$expected" ] || fail "Checksum mismatch: $relative. Existing skill was not changed."
    count=$((count + 1))
done < "$stage/manifest"
[ "$count" -eq 11 ] || fail 'Incomplete skill package.'
grep -q '^name: al-style-deploy$' "$stage/skill/SKILL.md" || fail 'Unexpected skill identity.'
printf '%s\n' "$repository" "$revision" > "$stage/skill/.github-source"
chmod 700 "$stage/skill/scripts/"*.sh
if [ -e "$destination" ]; then
    backup_root="$(dirname "$parent")/.al-style-deploy-backups"
    mkdir -p "$backup_root"
    backup="$backup_root/al-style-deploy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mv "$destination" "$backup"
fi
mv "$stage/skill" "$destination"
printf 'Installed for %s: %s\nGitHub commit: %s\n' "$agent" "$destination" "$revision"
[ -z "$backup" ] || printf 'Previous skill saved: %s\n' "$backup"
printf '%s\n' 'Client login and projects are preserved. Read SKILL.md and run its setup launcher next.'
