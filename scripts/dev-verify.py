#!/usr/bin/env python3
"""Plan and run checked-in local verification scenarios."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from workspace import ROOT, WorkspaceError, git, load_manifest, validate_repository

NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")
SHA = re.compile(r"[a-f0-9]{40}")
INHERITED_ENV = (
    "PATH", "HOME", "USERPROFILE", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "JAVA_HOME", "GRADLE_USER_HOME", "PNPM_HOME", "PLAYWRIGHT_BROWSERS_PATH",
)


def read_scenarios(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise WorkspaceError("Unsupported scenario manifest schema.")
    entries = data.get("scenarios")
    if not isinstance(entries, dict) or not entries:
        raise WorkspaceError("The scenario manifest is empty.")
    for name, item in entries.items():
        if not NAME.fullmatch(name) or not isinstance(item, dict):
            raise WorkspaceError("Invalid scenario name or entry.")
        if not isinstance(item.get("description"), str) or not item["description"]:
            raise WorkspaceError("A scenario must have a description.")
        for key in ("wikiRefs", "notCovered", "references"):
            values = item.get(key, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise WorkspaceError(f"{key} must be a list of strings.")
        if not all(NAME.fullmatch(name) for name in item.get("references", [])):
            raise WorkspaceError("Invalid reference repository name.")
        checks = item.get("checks")
        if not isinstance(checks, list) or not checks:
            raise WorkspaceError("A scenario must have at least one check.")
        names = set()
        for check in checks:
            if not isinstance(check, dict):
                raise WorkspaceError("Invalid check.")
            label = check.get("name")
            repo = check.get("repo")
            argv = check.get("argv")
            timeout = check.get("timeoutSeconds")
            env = check.get("env", {})
            if not isinstance(label, str) or not NAME.fullmatch(label) or label in names:
                raise WorkspaceError("Invalid or duplicate check name.")
            names.add(label)
            if not isinstance(repo, str) or not NAME.fullmatch(repo):
                raise WorkspaceError("Invalid check repository.")
            if not isinstance(argv, list) or not argv or not all(
                isinstance(arg, str) and arg and "\0" not in arg for arg in argv
            ):
                raise WorkspaceError("Invalid check arguments.")
            if type(timeout) is not int or not 1 <= timeout <= 3600:
                raise WorkspaceError("Check timeout must be between 1 and 3600 seconds.")
            if not isinstance(env, dict) or not all(
                re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key)
                and not key.startswith("DEV_VERIFY_")
                and isinstance(value, str) and "\0" not in value
                for key, value in env.items()
            ):
                raise WorkspaceError("Invalid check environment.")
    return entries, hashlib.sha256(raw).hexdigest()


def require_private_path(path):
    """Reject paths inside a Git checkout unless they are ignored and untracked."""
    existing = next(parent for parent in (path, *path.parents) if parent.is_dir())
    top = git(existing, "rev-parse", "--show-toplevel", optional=True)
    if top.returncode:
        return
    root = Path(top.stdout.strip()).resolve()
    relative = path.relative_to(root).as_posix()
    if (not relative or relative == "."
            or git(root, "check-ignore", "-q", "--no-index", "--", relative, optional=True).returncode
            or git(root, "ls-files", "--", relative).stdout.strip()):
        raise WorkspaceError("Private inputs and artifacts must be outside Git or ignored and untracked.")


def scenarios(local_path):
    entries, digest = read_scenarios(ROOT / "dev-scenarios.json")
    if local_path is None:
        return entries, digest, None
    local_path = local_path.resolve()
    require_private_path(local_path)
    local, local_digest = read_scenarios(local_path)
    if entries.keys() & local.keys():
        raise WorkspaceError("Local scenarios need unique names and cannot override checked-in ones.")
    return {**entries, **local}, digest, local_digest


def snapshot(path):
    filters = git(path, "config", "--name-only", "--get-regexp",
                  r"^filter\..*\.(clean|process)$", optional=True)
    # Status can invoke a repository's configured external clean/process filters.
    dirty = None
    if filters.returncode == 1:
        dirty = bool(git(path, "-c", "core.fsmonitor=false", "status", "--porcelain=v1",
                         "--untracked-files=all").stdout)
    return {
        "head": git(path, "rev-parse", "HEAD").stdout.strip(),
        "branch": git(path, "symbolic-ref", "--quiet", "--short", "HEAD", optional=True).stdout.strip() or None,
        "dirty": dirty,
    }


def expectations(pairs):
    result = {}
    for pair in pairs:
        name, sep, head = pair.partition("=")
        if not sep or not NAME.fullmatch(name) or not SHA.fullmatch(head) or name in result:
            raise WorkspaceError("Expected --expect REPO=<full lowercase 40-character SHA>.")
        result[name] = head
    return result


def origin_identity(url, *, checkout_origin=False):
    """Normalize plain HTTPS/HTTP and GitHub SSH Git origins without credentials."""
    if not isinstance(url, str) or not url or any(c.isspace() for c in url):
        raise WorkspaceError("Invalid repository origin.")
    if url.startswith("git@github.com:"):
        url = "ssh://git@github.com/" + url.removeprefix("git@github.com:")
    parsed = urlsplit(url)
    if checkout_origin and parsed.scheme in ("http", "https"):
        # Git configs can carry userinfo. Compare repository identity without retaining it.
        parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1])
    github_ssh = (parsed.scheme == "ssh" and parsed.hostname == "github.com"
                  and parsed.username == "git" and parsed.port in (None, 22))
    if (not parsed.hostname or parsed.password is not None or parsed.query or parsed.fragment
            or (not github_ssh and (parsed.scheme not in ("http", "https") or parsed.username is not None))):
        raise WorkspaceError("Invalid repository origin.")
    project = parsed.path.removeprefix("/").removesuffix(".git").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*(/[a-z0-9][a-z0-9_.-]*)+", project):
        raise WorkspaceError("Invalid repository project path.")
    if parsed.hostname == "github.com":
        if (not github_ssh and (parsed.scheme, parsed.port) not in (("https", None), ("https", 443))) or project.count("/") != 1:
            raise WorkspaceError("Invalid GitHub repository origin.")
        return ("https", "github.com", None, project)
    return (parsed.scheme, parsed.hostname, parsed.port, project)


def external_repositories(path, manifest):
    if path is None:
        return {}
    path = path.resolve()
    require_private_path(path)
    data = json.loads(path.read_bytes())
    if (not isinstance(data, dict) or type(data.get("schemaVersion")) is not int
            or data["schemaVersion"] != 1 or not isinstance(data.get("repositories"), dict)):
        raise WorkspaceError("Invalid local repository map.")
    origin_host = origin_identity(next(iter(manifest.values()))["url"])[:3]
    entries = data["repositories"]
    for name, entry in entries.items():
        if name in manifest or name == "workspace" or not NAME.fullmatch(name) or not isinstance(entry, dict):
            raise WorkspaceError("Invalid or conflicting external repository name.")
        if (not isinstance(entry.get("path"), str) or not Path(entry["path"]).is_absolute()
                or type(entry.get("readOnly", True)) is not bool):
            raise WorkspaceError("External repositories need an absolute local path and a valid readOnly flag.")
        identity = origin_identity(entry.get("origin"))
        company_loopback = (origin_host[1] != "github.com"
                            and identity[1] in ("localhost", "127.0.0.1", "::1")
                            and identity[0] == origin_host[0] and identity[2] == origin_host[2])
        if identity[:3] != origin_host and not company_loopback:
            raise WorkspaceError("The external origin is outside this workspace's Git host.")
        if origin_host[1] == "github.com" and not entry.get("readOnly", True):
            owner = origin_identity(next(iter(manifest.values()))["url"])[3].split("/", 1)[0]
            if identity[3].split("/", 1)[0] != owner:
                raise WorkspaceError("External GitHub projects from other owners are read-only references.")
    return entries


def verify_external(entry):
    path = Path(entry["path"]).resolve()
    if not path.is_dir() or Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != path:
        raise WorkspaceError("An external checkout is not an independent Git root.")
    urls = git(path, "config", "--get-all", "remote.origin.url").stdout.splitlines()
    if len(urls) != 1 or origin_identity(urls[0], checkout_origin=True) != origin_identity(entry["origin"]):
        raise WorkspaceError("An external checkout does not match its configured origin.")
    return path


def workspace_arg(arg):
    prefix = "{workspace}/"
    if not arg.startswith(prefix):
        return arg
    candidate = (ROOT / arg[len(prefix):]).resolve()
    if not candidate.is_relative_to(ROOT):
        raise WorkspaceError("A workspace argument escapes the workspace.")
    return str(candidate)


def prepare(scenario, checkout_root, expected, receipt, repo_map):
    manifest = {repo["name"]: repo for repo in load_manifest(ROOT)}
    checks = scenario["checks"]
    external = external_repositories(repo_map, manifest)
    executable = {check["repo"] for check in checks}
    names = dict.fromkeys(["workspace", *(check["repo"] for check in checks), *scenario.get("references", [])])
    if expected.keys() - names.keys():
        raise WorkspaceError("An expected SHA names a repository outside the scenario.")
    paths = {}
    for name in names:
        path = ROOT if name == "workspace" else checkout_root / name
        if name in manifest:
            validate_repository(path, manifest[name])
        elif name in external:
            if name in executable and external[name].get("readOnly", True):
                raise WorkspaceError(f"Repository {name} is a read-only reference.")
            path = verify_external(external[name])
        elif name != "workspace":
            raise WorkspaceError(f"Repository {name} has no workspace or local binding.")
        path = path.resolve()
        paths[name] = path
        state = snapshot(path)
        receipt["repositories"][name] = {**state, "expectedHead": expected.get(name)}
        if name in external:
            receipt["repositories"][name]["access"] = (
                "reference" if external[name].get("readOnly", True) else "verification"
            )
        if name in expected and state["head"] != expected[name]:
            raise WorkspaceError(f"Repository {name} does not match the expected SHA.")
    for check in checks:
        argv = [workspace_arg(arg) for arg in check["argv"]]
        receipt["checks"].append({
            "name": check["name"], "repo": check["repo"], "argv": argv,
            "timeoutSeconds": check["timeoutSeconds"], "status": "pending",
        })
    return paths


def run_check(check, row, path, output, checkout_root):
    log = output / (check["name"] + ".log")
    env = {key: os.environ[key] for key in INHERITED_ENV if key in os.environ}
    env.update(check.get("env", {}))
    env.update(DEV_VERIFY_ARTIFACT_DIR=str(output), DEV_VERIFY_CHECKOUT_ROOT=str(checkout_root))
    started = time.monotonic()
    row.update(logPath=str(log), exitCode=None)
    try:
        with log.open("xb") as stream:
            process = subprocess.Popen(
                row["argv"], cwd=path, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
            )
            try:
                row["exitCode"] = process.wait(timeout=check["timeoutSeconds"])
                row["status"] = "passed" if row["exitCode"] == 0 else "failed"
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                row["exitCode"] = process.wait()
                row["status"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
    except OSError:
        row["status"] = "blocked"
        row["reason"] = "Could not start the check or open its log."
    row["durationSeconds"] = round(time.monotonic() - started, 3)


def record(output, receipt):
    target = output / "receipt.json"
    receipt["receiptPath"] = str(target)
    pending = output / ".receipt.json.tmp"
    pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-scenarios", type=Path, help="An optional private scenario manifest.")
    parser.add_argument("--repo-map", type=Path, help="Optional private bindings for external checkouts.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list")
    inspect = subcommands.add_parser("inspect", help="Read a mapped checkout's identity and local Git state.")
    inspect.add_argument("repository")
    inspect.add_argument("--expect", metavar="SHA")
    for name in ("plan", "run"):
        command = subcommands.add_parser(name)
        command.add_argument("scenario")
        command.add_argument("--checkout-root", type=Path, default=ROOT)
        command.add_argument("--expect", action="append", default=[], metavar="REPO=SHA")
        if name == "run":
            command.add_argument("--output-dir", type=Path, help="Parent directory for a fresh run directory.")
    args = parser.parse_args()
    receipt = {"schemaVersion": 1, "command": args.command, "status": "blocked"}
    output = None
    try:
        if args.command == "inspect":
            manifest = {repo["name"]: repo for repo in load_manifest(ROOT)}
            external = external_repositories(args.repo_map, manifest)
            if args.repository not in external:
                raise WorkspaceError("The requested repository has no local binding.")
            entry = external[args.repository]
            path = verify_external(entry)
            state = snapshot(path)
            receipt.update(repository=args.repository, checkoutPath=str(path), **state,
                           access="reference" if entry.get("readOnly", True) else "verification")
            if args.expect and state["head"] != args.expect:
                raise WorkspaceError("The checkout does not match the expected SHA.")
            receipt["status"] = "inspected"
        elif args.command == "list":
            entries, _, _ = scenarios(args.local_scenarios)
            receipt.update(status="listed", scenarios={
                name: item["description"] for name, item in entries.items()
            })
        else:
            entries, digest, local_digest = scenarios(args.local_scenarios)
            if args.scenario not in entries:
                raise WorkspaceError("Unknown scenario. Use list to discover available scenarios.")
            item = entries[args.scenario]
            checkout_root = args.checkout_root.resolve()
            receipt.update(
                scenario=args.scenario, manifestSha256=digest, repositories={}, checks=[],
                wikiRefs=item.get("wikiRefs", []), notCovered=item.get("notCovered", []),
            )
            if local_digest is not None:
                receipt["localManifestSha256"] = local_digest
            if args.command == "run":
                parent = (args.output_dir or ROOT / "build" / "dev-verify").resolve()
                require_private_path(parent)
                parent.mkdir(parents=True, exist_ok=True)
                output = Path(tempfile.mkdtemp(prefix=args.scenario + "-", dir=parent))
            paths = prepare(item, checkout_root, expectations(args.expect), receipt, args.repo_map)
            if args.command == "plan":
                receipt["status"] = "planned"
            else:
                receipt["status"] = "running"
                for check, row in zip(item["checks"], receipt["checks"]):
                    row["status"] = "running"
                    record(output, receipt)
                    run_check(check, row, paths[check["repo"]], output, checkout_root)
                    if row["status"] != "passed":
                        receipt["status"] = "blocked" if row["status"] == "blocked" else "failed"
                        break
                else:
                    receipt["status"] = "passed"
                for row in receipt["checks"]:
                    if row["status"] == "pending":
                        row["status"] = "not_run"
                for name, path in paths.items():
                    before = receipt["repositories"][name]
                    after = snapshot(path)
                    before["after"] = after
                    if any(before[key] != after[key] for key in ("head", "branch", "dirty")):
                        receipt.update(status="failed", reason=f"Repository state changed during the run: {name}.")
    except WorkspaceError as exc:
        receipt.update(status="blocked", reason=str(exc))
    except (OSError, ValueError):
        receipt.update(status="blocked", reason="Could not read scenario inputs or repository state.")
    if output is not None:
        try:
            record(output, receipt)
        except OSError:
            receipt.update(status="failed", reason="Could not save the verification receipt.")
            receipt.pop("receiptPath", None)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] in ("listed", "inspected", "planned", "passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
