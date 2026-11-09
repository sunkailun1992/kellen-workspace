#!/usr/bin/env python3
"""为 IDE/Gradle/Jar 刷新私有观测配置；stdout 只输出无秘密回执。"""
import argparse
import json
from pathlib import Path

from observability_runtime import ROOT, check_idea_runtime, checkout_version, configure_binding, load_binding, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "prepare"])
    parser.add_argument("service", nargs="?")
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--dev-env", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--check-idea", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "configure":
            if not args.runtime_env or not args.dev_env:
                raise ValueError("both private environment paths are required")
            configure_binding(args.runtime_env, args.dev_env,
                              args.state_dir or ROOT / "build/observability-runtime")
            result = {"status": "configured", "secrets": "not displayed"}
        else:
            inputs = load_binding() if not args.runtime_env else {
                "runtime_env": args.runtime_env, "dev_env": args.dev_env,
                "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
            if not inputs.get("dev_env"):
                raise ValueError("private dev environment path required")
            result = prepare(args.service, args.version or checkout_version(args.service), **inputs)
            if args.check_idea:
                check_idea_runtime(args.service, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__,
                          "hint": "configure the restricted private launch binding; values omitted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
