#!/usr/bin/env python3
"""为 IDE/Gradle/Jar 刷新私有观测配置；stdout 只输出无秘密回执。"""
import argparse
import json
from pathlib import Path
import sys

from observability_runtime import (ROOT, check_idea_runtime, checkout_version, configure_binding,
                                   failure_hint, failure_reason, load_binding, prepare)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "prepare"])
    parser.add_argument("service", nargs="?")
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--dev-env", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--service-env",action="append",help="service=/absolute/private/environment.json")
    parser.add_argument("--service-env-file",type=Path)
    parser.add_argument("--clear-service-envs",action="store_true")
    parser.add_argument("--check-idea", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "configure" and args.service_env_file is not None:raise ValueError("use --service-env for persistent binding")
        if args.action != "configure" and args.clear_service_envs:raise ValueError("clear is only valid for configure")
        if args.action == "configure":
            if not args.runtime_env or not args.dev_env:
                raise ValueError("both private environment paths are required")
            from service_runtime_environment import parse_references
            if args.clear_service_envs and args.service_env:raise ValueError("service binding flags conflict")
            refs={}if args.clear_service_envs else parse_references(args.service_env)if args.service_env is not None else None
            configure_binding(args.runtime_env, args.dev_env,
                              args.state_dir or ROOT / "build/observability-runtime",service_envs=refs)
            result = {"status": "configured", "secrets": "not displayed"}
        else:
            inputs = load_binding() if not args.runtime_env else {
                "runtime_env": args.runtime_env, "dev_env": args.dev_env,
                "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
            if not inputs.get("dev_env"):
                raise ValueError("private dev environment path required")
            if args.service_env_file is not None:inputs["service_env_file"]=args.service_env_file
            if args.service_env is not None:
                from service_runtime_environment import parse_references
                inputs["service_envs"]={**inputs.get("service_envs",{}),**parse_references(args.service_env)}
            result = prepare(args.service, args.version or checkout_version(args.service), **inputs)
            if args.check_idea:
                check_idea_runtime(args.service, result)
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        reason = failure_reason(error)
        print(json.dumps({"status": "failed", "error_class": type(error).__name__,
                          "reason_code": reason, "hint": failure_hint(reason)}), file=sys.stderr if args.quiet else sys.stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
