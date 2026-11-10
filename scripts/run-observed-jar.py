#!/usr/bin/env python3
"""校验个人 Spring Boot Jar 与观测制品，然后以受限配置启动服务器 JVM。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from observability_runtime import ROOT, SERVICES, controlled_environment, load_binding, prepare, secure_directory, validate_application_args


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


def artifact_stat(path, private=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_mode & 0o022 or (private and stat.S_IMODE(info.st_mode) != 0o400)):
        raise ValueError("application artifact ownership, type or permissions invalid")
    for parent in path.parents:
        if parent.is_symlink() and parent.lstat().st_uid != 0:
            raise ValueError("application artifact must not traverse a user symlink")
    return info


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def unchanged(path, stream, before, private=False):
    if identity(os.fstat(stream.fileno())) != identity(before) or identity(artifact_stat(path, private)) != identity(before):
        raise ValueError("application artifact changed during verification")


def copy_and_hash(stream, output=None):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
        if output is not None:
            output.write(block)
    return digest.hexdigest()


def freeze_jar(source, service, version, state_dir, write=True):
    """创建内容寻址的独立副本；已有目标必须完全匹配，永不覆盖或清理旧制品。"""
    if service not in SERVICES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", version):
        raise ValueError("application service or version invalid")
    source = Path(source).absolute()
    before = artifact_stat(source)
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        unchanged(source, stream, before)
        if jar_version(stream, service) != version:
            raise ValueError("application artifact version changed")
        stream.seek(0)
        digest = copy_and_hash(stream)
        unchanged(source, stream, before)
        directory = secure_directory(Path(state_dir).absolute(), create=write) / "immutable-jars"
        secure_directory(directory, create=write)
        target = directory / f"{service}-{version}-{digest}.jar"
        if target.exists() or target.is_symlink():
            existing = artifact_stat(target, private=True)
            with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as cached:
                unchanged(target, cached, existing, private=True)
                actual = copy_and_hash(cached)
                unchanged(target, cached, existing, private=True)
            if actual != digest:
                raise ValueError("existing immutable application artifact checksum mismatch")
            unchanged(source, stream, before)
            return target, digest
        if not write:
            return target, digest
        # O_EXCL 原子占用新名字，不使用 replace/硬链；竞争中的未完成副本会安全拒绝。
        try:
            descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise ValueError("immutable application artifact was concurrently created") from None
        created = os.fstat(descriptor)
        complete = False
        try:
            with os.fdopen(descriptor, "w+b") as output:
                stream.seek(0)
                copied = copy_and_hash(stream, output)
                unchanged(source, stream, before)
                output.flush()
                output.seek(0)
                if copied != digest or copy_and_hash(output) != digest:
                    raise ValueError("application artifact copy checksum mismatch")
                output.seek(0)
                if jar_version(output, service) != version:
                    raise ValueError("application artifact copy verification failed")
                os.fchmod(output.fileno(), 0o400)
                os.fsync(output.fileno())
                unchanged(target, output, os.fstat(output.fileno()), private=True)
            complete = True
            return target, digest
        finally:
            if not complete and target.exists() and (target.lstat().st_dev, target.lstat().st_ino) == (created.st_dev, created.st_ino):
                target.unlink()


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
        validate_application_args(application_args, service=args.service)
        artifact = args.jar.absolute()
        artifact_stat(artifact)
        version = jar_version(artifact, args.service)
        inputs = load_binding() if not args.runtime_env else {
            "runtime_env": args.runtime_env, "dev_env": args.dev_env,
            "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
        if not inputs.get("dev_env"):
            raise ValueError("private dev environment required")
        # 先完成所有配置校验，再创建制品副本；dry-run 不生成目录或配置。
        receipt = prepare(args.service, version, **inputs, write=False)
        artifact, digest = freeze_jar(artifact, args.service, version, inputs["state_dir"], write=not args.dry_run)
        if not args.dry_run:
            receipt = prepare(args.service, version, **inputs, write=True)
        java = str(args.java or (Path(os.environ["JAVA_HOME"]) / "bin/java" if os.environ.get("JAVA_HOME") else "java"))
        command = [java, *receipt["jvm_arguments"], "-jar", str(artifact), *application_args]
        if args.dry_run:
            print(json.dumps({"service": args.service, "version": version, "agent": "2.33.1 verified",
                              "privacy_extension": "0.1.1 verified", "application_argument_count": len(application_args),
                              "rpc_port": receipt["rpc_port"], "application_sha256": digest,
                              "artifact_policy": "immutable content copy", "secret_values": "not displayed", "files_written": False}))
            return 0
        # 配置文件是这次启动的权威；旧 shell 中的遥测变量不能覆盖私有文件。
        environment = controlled_environment(os.environ, service=args.service)
        os.execvpe(java, command, environment)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__, "private_values": "omitted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
