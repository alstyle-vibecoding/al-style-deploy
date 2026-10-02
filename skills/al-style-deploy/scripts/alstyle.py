#!/usr/bin/env python3
"""Portable employee client. Dependencies: Python standard library and Git."""

import argparse
import contextlib
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://server.al-style.kz/platform"
EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "graphify-out",
    "dist",
    "build",
    ".idea",
}
SENSITIVE = re.compile(
    r"(?:^|/)(?:\.env(?:\..+)?|id_(?:rsa|ed25519)|credentials(?:\.json)?|"
    r"[^/]+\.(?:pem|key|p12|pfx|(?:sqlite3?|db)(?:-wal|-shm|-journal)?|dump|sql))$",
    re.I,
)
TOKENS = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b|"
    rb"\bAKIA[A-Z0-9]{16}\b|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"
)
LITERAL = re.compile(
    rb"(?i)\b(?:[a-z_][a-z0-9_]*(?:password|secret|token|api_key|private_key|database_url)|password|secret|token|api_key|private_key|database_url)"
    rb"['\"]?\s*[:=]\s*['\"]([^'\"\r\n]{4,})['\"]"
)
CONNECTION_SECRET = re.compile(rb"(?i)\b(?:postgresql|postgres|mysql|mariadb|redis)://[^:/\s]+:([^@\s/]+)@")
SQL_SECRET = re.compile(rb"(?i)\b(?:PASSWORD|IDENTIFIED\s+BY)\s+'([^'\r\n]{4,})'")
SQL_DUMP = re.compile(
    rb"(?is)PostgreSQL database dump|(?:MySQL|MariaDB) dump|Dumping data for table|"
    rb"Data for Name:|\bCOPY\b[^;]*\bFROM\s+stdin\b|\bINSERT\s+INTO\b[^;]*\bVALUES\s*\("
)


def sql_migration(path):
    parts = pathlib.PurePosixPath(path.lower()).parts
    return path.lower().endswith(".sql") and any(
        part in {"migrations", "migration", "schema-migrations"} for part in parts[:-1]
    )


def secret_literal(data):
    for match in [*LITERAL.finditer(data), *CONNECTION_SECRET.finditer(data), *SQL_SECRET.finditer(data)]:
        value = match.group(1).lower()
        if not value.startswith(
            (b"test-", b"test_", b"dummy-", b"dummy_", b"example-", b"example_", b"your_", b"<")
        ) and value not in (b"changeme", b"change_me", b"replace_me", b"placeholder"):
            return True
    return False


def private_permissions(path, directory=False):
    if sys.platform != "win32":
        path.chmod(0o700 if directory else 0o600)
        return
    # Windows chmod does not restrict readers. Replace the ACL with the current user's SID.
    script = """
$ErrorActionPreference = 'Stop'
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:ALSTYLE_PRIVATE_DIRECTORY -eq '1') {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $owner, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
} else {
    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($owner, 'FullControl', 'Allow')
}
$acl.SetOwner($owner)
$acl.SetAccessRuleProtection($true, $false)
$acl.SetAccessRule($rule)
if ($env:ALSTYLE_PRIVATE_DIRECTORY -eq '1') {
    [System.IO.Directory]::SetAccessControl($env:ALSTYLE_PRIVATE_PATH, $acl)
} else {
    [System.IO.File]::SetAccessControl($env:ALSTYLE_PRIVATE_PATH, $acl)
}
"""
    executable = pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "ALSTYLE_PRIVATE_PATH": str(path),
                 "ALSTYLE_PRIVATE_DIRECTORY": "1" if directory else "0"},
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Could not protect private storage on Windows") from None
    if result.returncode:
        raise RuntimeError("Could not protect private storage on Windows")


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    private_permissions(path.parent, directory=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, "w") as file:
            private_permissions(pathlib.Path(temporary))
            file.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def state_directory():
    return pathlib.Path(os.environ.get("ALSTYLE_CLIENT_STATE", pathlib.Path.home() / ".al-style-deploy"))


def run_git(root, *args, env=None, input=None):
    result = subprocess.run(
        ["git", "-C", str(root), *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    if result.returncode:
        # Credential helpers and remote replies must never become conversation/tool log output.
        raise RuntimeError("Git operation failed: " + args[0] + "; check connectivity and repository state")
    output = result.stdout.decode(errors="replace")
    return output if "-z" in args else output.strip()


def data_paths(root):
    """Explicit runtime data paths, never patterns that hide arbitrary source files."""
    plan = root / ".alstyle" / "storage.json"
    if not plan.exists():
        return []
    if plan.is_symlink():
        raise RuntimeError("Storage plan must be a regular file")
    body = json.loads(plan.read_text())
    if (
        not isinstance(body, dict) or set(body) != {"schema_version", "data_paths"}
        or body["schema_version"] != 1 or not isinstance(body["data_paths"], list)
        or len(body["data_paths"]) > 20
    ):
        raise RuntimeError("Invalid .alstyle/storage.json")
    paths = []
    for value in body["data_paths"]:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise RuntimeError("Data paths must be relative project paths")
        path = pathlib.PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] in {
            ".git", ".alstyle", ".github", "Dockerfile", ".dockerignore", ".gitignore",
        }:
            raise RuntimeError("Data paths must not hide project configuration")
        if not (root / value).resolve().is_relative_to(root):
            raise RuntimeError("Data path escapes the project")
        paths.append(path)
    return paths


def storage_inventory(root):
    candidates = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in EXCLUDED]
        for name in dirs[:]:
            path = pathlib.Path(directory) / name
            if path.is_symlink():
                dirs.remove(name)
            elif name.lower() in {"uploads", "receipts", "media", "user-files", "attachments"}:
                candidates.append({"path": path.relative_to(root).as_posix(), "kind": "uploads-directory"})
                dirs.remove(name)
        for name in files:
            path = pathlib.Path(directory) / name
            if not path.is_file() or path.is_symlink():
                continue
            kind = None
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                kind = "file-database"
            elif path.suffix.lower() in {".json", ".csv", ".jsonl"} and (
                "data" in path.relative_to(root).parts or path.stem.lower() in {
                    "drivers", "cards", "balances", "refuels", "fuel", "records", "history",
                }
            ):
                kind = "possible-records"
            if kind:
                candidates.append({"path": path.relative_to(root).as_posix(), "kind": kind,
                                   "bytes": path.stat().st_size})
            if len(candidates) >= 100:
                return {"candidates": candidates[:100], "truncated": True}
    return {"candidates": candidates, "truncated": False}


def stage_data(root, source):
    """Make a private snapshot, without deleting data or putting it in a build context."""
    root = root.resolve()
    source = pathlib.Path(source).expanduser()
    if not source.is_absolute():
        source = root / source
    if source.is_symlink():
        raise RuntimeError("Data source must not be a symbolic link")
    source = source.resolve()
    if not source.is_relative_to(root) or source == root or not source.exists():
        raise RuntimeError("Select an existing data file or directory inside the project")
    if source.relative_to(root).parts[0] in EXCLUDED | {".alstyle", ".github"}:
        raise RuntimeError("Select runtime data, not project or dependency directories")
    state = state_directory().expanduser().resolve() / "private-data"
    if state.is_relative_to(root):
        raise RuntimeError("Private data storage must be outside the project")
    files = [source] if source.is_file() else source.rglob("*")
    total = 0
    selected = []
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(source if source.is_dir() else root):
            raise RuntimeError("Data snapshots cannot contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError("Data snapshot contains a special file")
        total += path.stat().st_size
        selected.append(path)
        if total > 1024**3 or len(selected) > 10000:
            raise RuntimeError("Data snapshot exceeds 1 GB or 10000 files; prepare a bounded migration")
    state.mkdir(parents=True, exist_ok=True)
    private_permissions(state, directory=True)
    directory = pathlib.Path(tempfile.mkdtemp(prefix="snapshot-", dir=state))
    private_permissions(directory, directory=True)
    payload = directory / "payload"
    payload.mkdir()
    private_permissions(payload, directory=True)
    target = payload / source.name
    try:
        if source.is_dir():
            target.mkdir()
            private_permissions(target, directory=True)
        inventory = []
        for path in selected:
            destination = target if source.is_file() else target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as file:
                sqlite = file.read(16) == b"SQLite format 3\x00"
            destination.touch(mode=0o600, exist_ok=False)
            private_permissions(destination)
            if sqlite:
                deadline = time.monotonic() + 60
                with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as original:
                    page_size = original.execute("PRAGMA page_size").fetchone()[0]

                    def progress(status, remaining, pages):
                        if time.monotonic() > deadline:
                            raise RuntimeError("SQLite snapshot timeout; stop writes and retry")
                        if pages * page_size > 1024**3:
                            raise RuntimeError("SQLite snapshot exceeds 1 GB; prepare a bounded migration")

                    with contextlib.closing(sqlite3.connect(destination)) as backup:
                        original.backup(backup, pages=256, progress=progress)
            else:
                before = path.stat()
                shutil.copyfile(path, destination)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError("Data changed during snapshot; stop writes and retry")
            with destination.open("rb") as file:
                checksum = hashlib.file_digest(file, "sha256").hexdigest()
            inventory.append({"path": destination.relative_to(directory).as_posix(),
                              "bytes": destination.stat().st_size, "sha256": checksum,
                              "kind": "sqlite" if sqlite else "file"})
        private_write(directory / "inventory.json", json.dumps(inventory, ensure_ascii=False))
    except BaseException:
        shutil.rmtree(directory)
        raise
    return {"snapshot_path": str(target), "inventory_file": str(directory / "inventory.json"),
            "files": len(inventory), "bytes": sum(item["bytes"] for item in inventory),
            "source_preserved": True}


def source_files(root):
    excluded_data = data_paths(root)
    git_root = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if git_root.returncode == 0 and pathlib.Path(git_root.stdout.strip()).resolve() == root:
        output = run_git(root, "ls-files", "-co", "--exclude-standard", "-z")
        names = output.split("\x00") if output else []
        files = [root / x for x in set(names) if x]
    else:
        files = []
        for directory, dirs, names in os.walk(root):
            dirs[:] = [
                d for d in dirs if d not in EXCLUDED and not (pathlib.Path(directory) / d).is_symlink()
            ]
            files.extend(pathlib.Path(directory) / name for name in names)
    return sorted(
        p
        for p in files
        if not set(p.relative_to(root).parts) & EXCLUDED
        and p.relative_to(root).as_posix() != ".alstyle/project.json"
        and not any(p.relative_to(root).is_relative_to(path) for path in excluded_data)
    )


def scan(root):
    files = source_files(root)
    issues, total = [], 0
    for file in files:
        relative = file.relative_to(root).as_posix()
        if not file.exists():
            continue
        if not file.is_file():
            issues.append(relative + ": nested repository or special file")
            continue
        if not file.resolve().is_relative_to(root):
            issues.append(relative + ": symbolic link escapes project")
            continue
        if SENSITIVE.search(relative) and not sql_migration(relative) and not relative.endswith(
            (".env.example", ".env.sample", ".env.template")
        ):
            issues.append(relative + ": sensitive filename")
        if relative.startswith(".github/workflows/"):
            issues.append(relative + ": employee workflows are unavailable; builds use the company runner")
        size = file.stat().st_size
        total += size
        if size > 10 * 1024**2:
            issues.append(relative + ": file exceeds 10 MB")
        else:
            data = file.read_bytes()
            if relative.lower().endswith(".sql") and SQL_DUMP.search(data):
                issues.append(relative + ": SQL data export; migrate records privately")
            if TOKENS.search(data) or secret_literal(data):
                issues.append(relative + ": credential pattern")
    if total > 50 * 1024**2:
        issues.append("Source exceeds 50 MB")
    if len(files) > 10000:
        issues.append("Source exceeds 10000 files")
    if issues:
        raise RuntimeError("Publication scan failed:\n" + "\n".join(issues))
    return [p for p in files if p.is_file()]


class Client:
    def __init__(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise RuntimeError("Gateway must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("Invalid gateway URL")
        self.url = url.rstrip("/")
        self.file = state_directory() / "session.json"
        self.token = None
        if self.file.exists():
            session = json.loads(self.file.read_text())
            if session["url"] == self.url:
                self.token = session["token"]

    def request(self, method, path, data=None, auth=True):
        if auth and not self.token:
            raise RuntimeError("Run login first")
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            self.url + path,
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail", "Request failed")
            except (ValueError, AttributeError):
                detail = "Request failed"
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


def mapping(root):
    path = root / ".alstyle" / "project.json"
    if not path.exists():
        raise RuntimeError("Publish this project first")
    return json.loads(path.read_text())


def issue_invitation(client, email, name):
    private = state_directory().expanduser().resolve() / "private"
    if private.is_relative_to(pathlib.Path.cwd().resolve()):
        raise RuntimeError("Invitation storage must be outside the current project")
    private.mkdir(parents=True, exist_ok=True)
    private_permissions(private, directory=True)
    directory = private / "invitations"
    directory.mkdir(parents=True, exist_ok=True)
    private_permissions(directory, directory=True)
    # Prepare writable private storage before creating a one-time credential remotely.
    handle, temporary = tempfile.mkstemp(prefix="employee-", suffix=".txt", dir=directory)
    path = pathlib.Path(temporary)
    try:
        with os.fdopen(handle, "w") as file:
            private_permissions(path)
            result = client.request("POST", "/invitations", {"email": email, "name": name})
            file.write(result["invite"] + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"email": result["email"], "expires": result["expires"], "invitation_file": str(path)}


def chosen_domain(value, suffix):
    # Accept a short label or the complete company-provided hostname, never arbitrary routes.
    value = value.strip().lower()
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("Use a short address or an HTTPS hostname without ports or paths")
        value = parsed.hostname or ""
    if "." in value:
        ending = "." + suffix
        if not value.endswith(ending):
            raise RuntimeError("This domain needs operator DNS configuration; use an address under " + suffix)
        value = value[: -len(ending)]
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value) or len(value) > 48:
        raise RuntimeError("Address must be 1-48 lowercase Latin letters, digits and single hyphens")
    return value


def read_private_secrets(path, root):
    path = pathlib.Path(path).expanduser().resolve()
    if path.is_relative_to(root) or path.stat().st_size > 128 * 1024:
        raise RuntimeError("Secrets must be in a small private JSON file outside the project")
    values = json.loads(path.read_text())
    if not isinstance(values, dict) or not 1 <= len(values) <= 30:
        raise RuntimeError("Private secrets file must contain 1-30 named values")
    for key, value in values.items():
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
            or key.startswith("PLATFORM_")
            or not isinstance(value, str)
            or not 1 <= len(value) <= 16384
        ):
            raise RuntimeError("Invalid private secret entry; use environment names and string values")
    return values


def stage_env(root, source, move_source=False, names=None):
    source = pathlib.Path(source).expanduser().resolve()
    if source.stat().st_size > 128 * 1024:
        raise RuntimeError("Environment file is too large")
    values = {}
    selected = set(names) if names is not None else None
    if selected is not None and (
        not selected or any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) for name in selected)
    ):
        raise RuntimeError("Select valid environment names")
    content = source.read_text(encoding="utf-8-sig")
    for number, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if selected is not None and key not in selected:
            continue
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
            or key.startswith("PLATFORM_")
            or key in values
        ):
            raise RuntimeError(
                f"Unsupported or duplicate environment name on line {number}; no values were displayed"
            )
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise RuntimeError(
                    f"Unsupported quoted value on line {number}; no values were displayed"
                ) from None
        elif value.startswith("'"):
            if not value.endswith("'"):
                raise RuntimeError(f"Incomplete quoted value on line {number}")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if not isinstance(value, str) or "${" in value or not 1 <= len(value) <= 16384:
            raise RuntimeError(
                f"Empty, oversized or interpolated value on line {number}; resolve it privately"
            )
        values[key] = value
    if selected is not None and selected != values.keys():
        raise RuntimeError("Selected environment names were not found in the source file")
    if not 1 <= len(values) <= 30:
        raise RuntimeError("Stage 1-30 environment values at a time")
    import hashlib

    directory = state_directory() / "private-env" / hashlib.sha256(str(root).encode()).hexdigest()[:24]
    if directory.resolve().is_relative_to(root):
        raise RuntimeError("Client private state must be outside the project")
    if move_source and (
        not source.is_relative_to(root)
        or not source.name.startswith(".env")
        or source.name.endswith((".example", ".sample", ".template"))
    ):
        raise RuntimeError("Moving is supported only for a real .env file inside this project")
    path = directory / "secrets.json"
    private_write(path, json.dumps(values))
    if move_source:
        private_write(directory / "source.env", content)
        source.unlink()
    return {"names": sorted(values), "secrets_file": str(path), "source_moved": move_source}


def publish(client, root, name, title=None, domain=None, secrets_file=None):
    manifest_path = root / ".alstyle" / "deploy.json"
    if not manifest_path.is_file():
        raise RuntimeError("Agent must prepare .alstyle/deploy.json and working Dockerfiles first")
    manifest = json.loads(manifest_path.read_text())
    files = scan(root)  # Check before creating any external repository or uploading a file.
    secrets = read_private_secrets(secrets_file, root) if secrets_file else {}
    referenced = {key for unit in manifest.get("services", []) for key in unit.get("secrets", [])}
    if secrets.keys() - referenced:
        raise RuntimeError("Every imported secret must be referenced by a service's secrets field")
    marker = root / ".alstyle" / "project.json"
    if not marker.exists() and any(not unit.get("test_stage") for unit in manifest.get("services", [])):
        raise RuntimeError("Prepare a Dockerfile test_stage for every service before creating the project")
    if marker.exists():
        project = mapping(root)
        if project["gateway"] != client.url:
            raise RuntimeError("Project belongs to a different gateway")
        remote = client.request("GET", "/projects/" + project["id"])
        if title is not None and title.strip() != remote.get("display_name"):
            raise RuntimeError("Use the existing project's saved name; this command does not rename projects")
        if domain is not None and chosen_domain(domain, remote["domain_suffix"]) != remote.get(
            "domain_label"
        ):
            raise RuntimeError("Use the existing project's saved address; this command does not move domains")
    else:
        if not name or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise RuntimeError(
                "First publication requires --name using lowercase letters, digits and hyphens"
            )
        body = {"slug": name}
        if title is not None:
            body["display_name"] = title
        if domain is not None:
            profile = client.request("GET", "/me")
            if "domain_suffix" not in profile:
                raise RuntimeError("Gateway needs an operator update before choosing an address")
            label = chosen_domain(domain, profile["domain_suffix"])
            checked = client.request("GET", "/domains/check?" + urllib.parse.urlencode({"label": label}))
            if not checked["available"] and not checked["owned_by_you"]:
                raise RuntimeError(
                    "This address is already taken; ask the employee to choose another address"
                )
            body["domain_label"] = label
        remote = client.request("POST", "/projects", body)
        project = {"id": remote["id"], "gateway": client.url}
        marker.write_text(json.dumps(project, indent=2) + "\n")
    for key, value in secrets.items():
        client.request("PUT", "/projects/" + project["id"] + "/secrets/" + key, {"value": value})
    credentials = client.request("POST", "/projects/" + project["id"] + "/git-credential")
    checkout = state_directory() / "checkouts" / project["id"]
    checkout.mkdir(parents=True, exist_ok=True)
    checkout.chmod(0o700)
    # Authorization lives only in the Git subprocess environment, never in a remote URL or config file.
    import base64

    auth = base64.b64encode((credentials["username"] + ":" + credentials["token"]).encode()).decode()
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "Authorization: Basic " + auth,
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_KEY_2": "core.hooksPath",
        "GIT_CONFIG_VALUE_2": os.devnull,
    }
    if not (checkout / ".git").exists():
        run_git(checkout, "init", "--initial-branch=main")
        run_git(checkout, "config", "user.name", "AL-STYLE publication")
        run_git(checkout, "config", "user.email", "deployment@al-style.kz")
        run_git(checkout, "remote", "add", "origin", credentials["repository"])
    # Different computers reconcile with the managed branch before creating a new snapshot.
    remote_heads = run_git(checkout, "ls-remote", credentials["repository"], "refs/heads/main", env=env)
    if remote_heads:
        run_git(checkout, "fetch", "--no-tags", "origin", "main", env=env)
        run_git(checkout, "reset", "--hard", "FETCH_HEAD")  # Only the disposable publication checkout.
    for item in checkout.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
    for file in files:
        destination = checkout / file.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination, follow_symlinks=True)
    # Ignore rules in the source project were already honored; tracked publication files mirror the scan.
    run_git(checkout, "add", "--all", "--force")
    staged = run_git(checkout, "diff", "--cached", "--name-only")
    if staged:
        run_git(checkout, "commit", "-m", "Publish employee project")
    sha = run_git(checkout, "rev-parse", "HEAD")
    run_git(checkout, "push", "origin", "HEAD:refs/heads/main", env=env)
    result = client.request(
        "POST", "/projects/" + project["id"] + "/deployments", {"sha": sha, "manifest": manifest}
    )
    print(
        json.dumps(
            {
                "repository": remote["repository"],
                "display_name": remote.get("display_name"),
                "requested_url": remote.get("requested_url"),
                "deployment": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=DEFAULT_URL)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("login")
    subs.add_parser("logout")
    subs.add_parser("me")
    subs.add_parser("projects")
    stage_data_command = subs.add_parser("stage-data", help="Privately snapshot existing records and uploads")
    stage_data_command.add_argument("--path", default=".")
    stage_data_command.add_argument("--source", required=True)
    invite = subs.add_parser("invite", help="Issue an employee invitation using an approved operator account")
    invite.add_argument("--email", required=True)
    invite.add_argument("--name", required=True)
    domains = subs.add_parser("domains", help="Check a project's desired address before publishing")
    domains.add_argument("--label", required=True)
    stage = subs.add_parser(
        "stage-env", help="Move local environment values to private storage without displaying them"
    )
    stage.add_argument("--path", default=".")
    stage.add_argument("--source", required=True)
    stage.add_argument("--move-source", action="store_true")
    stage.add_argument("--names", help="Comma-separated environment names to stage; defaults to all entries")
    for command in (
        "inspect",
        "scan",
        "publish",
        "status",
        "wait",
        "history",
        "rollback",
        "secret-set",
        "logs",
    ):
        item = subs.add_parser(command)
        item.add_argument("--path", default=".")
        if command == "publish":
            item.add_argument("--name")
            item.add_argument("--title", help="Human-readable project name, including Russian text")
            item.add_argument(
                "--domain", help="Address label or full hostname under the gateway's domain suffix"
            )
            item.add_argument(
                "--secrets-file", help="Private JSON file outside the project; values are never printed"
            )
        elif command == "wait":
            item.add_argument("--timeout", type=int, default=50)
        elif command == "rollback":
            item.add_argument("--release", required=True)
        elif command == "secret-set":
            item.add_argument("--name", required=True)
            item.add_argument("--stdin", action="store_true")
        elif command == "logs":
            item.add_argument("--kind", choices=["build", "runtime"], default="runtime")
            item.add_argument("--service", default="web")
    args = parser.parse_args()
    root = pathlib.Path(getattr(args, "path", ".")).resolve()
    if args.command == "stage-data":
        print(json.dumps(stage_data(root, args.source), ensure_ascii=False, indent=2))
        return
    if args.command == "stage-env":
        print(
            json.dumps(
                stage_env(root, args.source, args.move_source, args.names.split(",") if args.names else None),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "inspect":
        names = [p.relative_to(root).as_posix() for p in source_files(root)]
        indicators = {
            "node": "package.json",
            "python": "pyproject.toml",
            "python-pip": "requirements.txt",
            "php": "composer.json",
            "go": "go.mod",
            "rust": "Cargo.toml",
            "static": "index.html",
        }
        print(
            json.dumps(
                {
                    "path": str(root),
                    "technologies": [k for k, v in indicators.items() if v in names],
                    "files": names[:120],
                    "manifest": (root / ".alstyle/deploy.json").exists(),
                    "project_mapped": (root / ".alstyle/project.json").is_file(),
                    "storage": storage_inventory(root),
                },
                indent=2,
            )
        )
        return
    if args.command == "scan":
        print(json.dumps({"scanned_files": len(scan(root)), "status": "passed"}))
        return
    client = Client(args.gateway)
    if args.command == "invite":
        print(json.dumps(issue_invitation(client, args.email, args.name), ensure_ascii=False, indent=2))
    elif args.command == "login":
        invite = getpass.getpass("Operator invitation: ")
        result = client.request("POST", "/auth/exchange", {"invite": invite}, auth=False)
        private_write(client.file, json.dumps({"url": client.url, "token": result["token"]}))
        print("Signed in; session saved privately")
    elif args.command == "logout":
        client.request("POST", "/auth/logout")
        client.file.unlink(missing_ok=True)
        print("Signed out")
    elif args.command == "me":
        print(json.dumps(client.request("GET", "/me"), ensure_ascii=False, indent=2))
    elif args.command == "projects":
        print(json.dumps(client.request("GET", "/projects"), ensure_ascii=False, indent=2))
    elif args.command == "domains":
        profile = client.request("GET", "/me")
        label = chosen_domain(args.label, profile["domain_suffix"])
        print(
            json.dumps(
                client.request("GET", "/domains/check?" + urllib.parse.urlencode({"label": label})), indent=2
            )
        )
    elif args.command == "publish":
        publish(client, root, args.name, args.title, args.domain, args.secrets_file)
    else:
        project = mapping(root)
        if project["gateway"] != client.url:
            raise RuntimeError("Project belongs to another gateway")
        path = "/projects/" + project["id"]
        if args.command == "secret-set":
            value = sys.stdin.read().rstrip("\r\n") if args.stdin else getpass.getpass("Secret value: ")
            result = client.request(
                "PUT", path + "/secrets/" + urllib.parse.quote(args.name, safe=""), {"value": value}
            )
        elif args.command == "rollback":
            result = client.request("POST", path + "/rollback/" + urllib.parse.quote(args.release, safe=""))
        elif args.command == "history":
            result = client.request("GET", path + "/deployments")
        elif args.command == "logs":
            result = client.request(
                "GET", path + "/logs?" + urllib.parse.urlencode({"kind": args.kind, "service": args.service})
            )
        elif args.command == "wait":
            deadline = time.monotonic() + max(1, min(args.timeout, 50))
            while True:
                result = client.request("GET", path)
                job = result.get("deployment") or {}
                if job.get("status") in ("healthy", "failed", "cancelled") or time.monotonic() >= deadline:
                    break
                time.sleep(5)
        else:
            result = client.request("GET", path)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
