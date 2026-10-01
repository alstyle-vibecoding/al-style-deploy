#!/usr/bin/env python3
"""Portable employee client. Dependencies: Python standard library and Git."""

import argparse
import getpass
import json
import os
import pathlib
import re
import shutil
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
    r"[^/]+\.(?:pem|key|p12|pfx|sqlite3?|db|dump|sql))$",
    re.I,
)
TOKENS = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b|"
    rb"\bAKIA[A-Z0-9]{16}\b|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"
)


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w") as file:
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


def source_files(root):
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
        if SENSITIVE.search(relative) and not relative.endswith(
            (".env.example", ".env.sample", ".env.template")
        ):
            issues.append(relative + ": sensitive filename")
        if relative.startswith(".github/workflows/"):
            issues.append(relative + ": employee workflows are unavailable; builds use the company runner")
        size = file.stat().st_size
        total += size
        if size > 10 * 1024**2:
            issues.append(relative + ": file exceeds 10 MB")
        elif TOKENS.search(file.read_bytes()):
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


def publish(client, root, name):
    manifest_path = root / ".alstyle" / "deploy.json"
    if not manifest_path.is_file():
        raise RuntimeError("Agent must prepare .alstyle/deploy.json and working Dockerfiles first")
    manifest = json.loads(manifest_path.read_text())
    files = scan(root)  # Check before creating any external repository or uploading a file.
    marker = root / ".alstyle" / "project.json"
    if marker.exists():
        project = mapping(root)
        if project["gateway"] != client.url:
            raise RuntimeError("Project belongs to a different gateway")
        remote = client.request("GET", "/projects/" + project["id"])
    else:
        if not name or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise RuntimeError(
                "First publication requires --name using lowercase letters, digits and hyphens"
            )
        remote = client.request("POST", "/projects", {"slug": name})
        project = {"id": remote["id"], "gateway": client.url}
        marker.write_text(json.dumps(project, indent=2) + "\n")
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
        json.dumps({"repository": remote["repository"], "deployment": result}, ensure_ascii=False, indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=DEFAULT_URL)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("login")
    subs.add_parser("logout")
    subs.add_parser("me")
    subs.add_parser("projects")
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
                },
                indent=2,
            )
        )
        return
    if args.command == "scan":
        print(json.dumps({"scanned_files": len(scan(root)), "status": "passed"}))
        return
    client = Client(args.gateway)
    if args.command == "login":
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
    elif args.command == "publish":
        publish(client, root, args.name)
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
