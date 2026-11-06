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
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


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
        agent = Path(os.environ["OTEL_JAVAAGENT_PATH"]).resolve(strict=True)
        digest = os.environ["OTEL_JAVAAGENT_SHA256"]
        if not re.fullmatch("[a-f0-9]{64}", digest) or hashlib.sha256(agent.read_bytes()).hexdigest() != digest:
            raise ValueError("agent checksum mismatch")
        endpoint = urlsplit(os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("invalid collector endpoint")
    except (KeyError, ValueError, OSError):
        parser.exit(2, "service, agent checksum or collector endpoint invalid; private values omitted\n")
    command = [str(ROOT / "gradlew"), "--init-script", str(ROOT / "scripts/observability.init.gradle"),
               f":{args.service}:bootRun"]
    if args.dry_run:
        print(json.dumps({"service": args.service, "command": command,
                          "agent": "checksum verified", "secrets": "not displayed"}, ensure_ascii=False))
        return 0
    environment = dict(os.environ, FLEET_OBSERVABILITY_SERVICE=args.service,
                       OTEL_JAVAAGENT_PATH=str(agent))
    # 令牌仅由服务运行环境传递，不变成CLI参数或stdout。
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    sys.exit(main())
