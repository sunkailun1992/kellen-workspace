#!/usr/bin/env python3
"""仅给清单中的服务 bootRun JVM 启用观测，不设置全局 JAVA_TOOL_OPTIONS。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Maven Central 官方 2.33.1 jar.sha256；升级 agent 时必须连同 SDK/扩展契约验证。
PINNED_AGENT_SHA256 = "e43a270df53c6f423e0ab157be9c27ccc01d5dc3232e162fe54777573db2f149"


def verified_jar(path_key, digest_key, version):
    path = Path(os.environ[path_key]).resolve(strict=True)
    digest = os.environ[digest_key]
    if not re.fullmatch("[a-f0-9]{64}", digest) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("runtime jar checksum mismatch")
    if path_key == "OTEL_JAVAAGENT_PATH" and digest != PINNED_AGENT_SHA256:
        raise ValueError("agent is not the pinned official artifact")
    with zipfile.ZipFile(path) as jar:
        manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8").replace("\r\n ", "").replace("\n ", "")
    fields = dict(line.split(": ", 1) for line in manifest.splitlines() if ": " in line)
    if fields.get("Implementation-Version") != version:
        raise ValueError("runtime jar version mismatch")
    if path_key == "FLEET_PRIVACY_EXTENSION_PATH" and fields.get("Fleet-Agent-Version") != "2.33.1":
        raise ValueError("privacy extension agent version mismatch")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "repos.json").read_text())
    names = {repo["name"] for repo in manifest["repositories"] if repo["kind"] == "java-service"}
    try:
        if args.service not in names:
            raise ValueError("not a declared service")
        agent = verified_jar("OTEL_JAVAAGENT_PATH", "OTEL_JAVAAGENT_SHA256", "2.33.1")
        privacy = verified_jar("FLEET_PRIVACY_EXTENSION_PATH", "FLEET_PRIVACY_EXTENSION_SHA256", "0.1.0")
        endpoint = urlsplit(os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("invalid collector endpoint")
    except (KeyError, ValueError, OSError, zipfile.BadZipFile):
        parser.exit(2, "service, pinned agent/privacy extension checksum or collector endpoint invalid; private values omitted\n")
    command = [str(ROOT / "gradlew"), "--init-script", str(ROOT / "scripts/observability.init.gradle"),
               f":{args.service}:bootRun"]
    if args.dry_run:
        print(json.dumps({"service": args.service, "command": command,
                          "agent": "2.33.1 checksum verified", "privacy_extension": "0.1.0 checksum verified", "secrets": "not displayed"}, ensure_ascii=False))
        return 0
    environment = dict(os.environ, FLEET_OBSERVABILITY_SERVICE=args.service,
                       OTEL_JAVAAGENT_PATH=str(agent), FLEET_PRIVACY_EXTENSION_PATH=str(privacy))
    environment.pop("OTEL_JAVAAGENT_EXTENSIONS", None)
    # 令牌仅由服务运行环境传递，不变成CLI参数或stdout。
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    sys.exit(main())
