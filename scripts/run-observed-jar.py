#!/usr/bin/env python3
"""校验个人 Spring Boot Jar 与观测制品，然后以受限配置启动服务器 JVM。"""
import argparse
import json
import os
from pathlib import Path
import zipfile

from observability_runtime import ROOT, SERVICES, controlled_environment, load_binding, prepare, validate_application_args


def jar_version(path, service):
    with zipfile.ZipFile(path) as jar:
        info = jar.getinfo("META-INF/MANIFEST.MF")
        if info.file_size > 65536:
            raise ValueError("application manifest too large")
        manifest = jar.read(info).decode("utf-8").replace("\r\n ", "").replace("\n ", "")
    fields = dict(line.split(": ", 1) for line in manifest.splitlines() if ": " in line)
    if fields.get("Start-Class") != SERVICES[service] or not fields.get("Implementation-Version"):
        raise ValueError("application identity or version invalid")
    return fields["Implementation-Version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("jar", type=Path)
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--dev-env", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args, application_args = parser.parse_known_args()
    try:
        application_args = application_args[1:] if application_args[:1] == ["--"] else application_args
        validate_application_args(application_args)
        artifact = args.jar.resolve(strict=True)
        version = jar_version(artifact, args.service)
        inputs = load_binding() if not args.runtime_env else {
            "runtime_env": args.runtime_env, "dev_env": args.dev_env,
            "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
        if not inputs.get("dev_env"):
            raise ValueError("private dev environment required")
        receipt = prepare(args.service, version, **inputs, write=not args.dry_run)
        java = str(args.java or (Path(os.environ["JAVA_HOME"]) / "bin/java" if os.environ.get("JAVA_HOME") else "java"))
        command = [java, *receipt["jvm_arguments"], "-jar", str(artifact), *application_args]
        if args.dry_run:
            print(json.dumps({"service": args.service, "version": version, "agent": "2.33.1 verified",
                              "privacy_extension": "0.1.0 verified", "application_argument_count": len(application_args),
                              "secret_values": "not displayed", "files_written": False}))
            return 0
        # 配置文件是这次启动的权威；旧 shell 中的遥测变量不能覆盖私有文件。
        environment = controlled_environment(os.environ)
        os.execvpe(java, command, environment)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__, "private_values": "omitted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
