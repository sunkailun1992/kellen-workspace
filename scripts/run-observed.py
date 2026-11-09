#!/usr/bin/env python3
"""使用已配置的私有绑定启动服务 bootRun；dry-run 与实际运行共用完整校验。"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from observability_runtime import checkout_version, controlled_environment, load_binding, prepare

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads((ROOT / "repos.json").read_text())
        names = {repo["name"] for repo in manifest["repositories"] if repo["kind"] == "java-service"}
        if args.service not in names:
            raise ValueError("not a declared service")
        prepare(args.service, checkout_version(args.service), **load_binding(), write=not args.dry_run)
    except Exception:
        parser.exit(2, "personal launch binding, pinned artifacts or runtime configuration invalid; private values omitted\n")
    command = [str(ROOT / "gradlew"), "--init-script", str(ROOT / "scripts/observability.init.gradle"),
               f":{args.service}:bootRun"]
    if args.dry_run:
        print(json.dumps({"service": args.service, "command": command,
                          "agent": "2.33.1 checksum verified", "privacy_extension": "0.1.1 checksum verified",
                          "runtime": "full preflight verified", "secrets": "not displayed"}, ensure_ascii=False))
        return 0
    environment = dict(controlled_environment(os.environ), FLEET_OBSERVABILITY_SERVICE=args.service)
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
