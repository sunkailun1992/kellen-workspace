#!/usr/bin/env python3
"""编译固定依赖的 Java 21 迁移工具；默认只计划，不安装工具或依赖。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PINS = (
    ("com.mysql", "mysql-connector-j", "9.6.0", "66df1d453789dc8cb759a7dc17f58646893bf28483f262328650f170472a6ead"),
    ("org.postgresql", "postgresql", "42.7.10", "cab1cd67cfa25c25de4348e532298028288a877ba01c77d1619fe45416193387"),
    ("tools.jackson.core", "jackson-core", "3.1.0", "4dd383f96b51b9b9ac4b74bdf1c150df0100443e1da1fba480f57da09a2dcef7"),
    ("tools.jackson.core", "jackson-databind", "3.1.0", "1bf5454a1d589a27ddbb91ee16f31f169b08530843f52f5e83f62c650c64516b"),
    ("com.fasterxml.jackson.core", "jackson-annotations", "2.21", "53ca085f4a150f703f49e1aabd935bd03b43e1ea3d55d135438292af22cef56b"),
)


def java_environment():
    return {key: value for key, value in os.environ.items()
            if key not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}}


class Rejected(Exception):
    pass


def private_file(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        raise Rejected("PRIVATE_FILE_UNAVAILABLE") from None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise Rejected("PRIVATE_FILE_TYPE")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise Rejected("PRIVATE_FILE_PERMISSION")
    return path


def build_runtime():
    home = os.environ.get("JAVA_HOME")
    if not home and sys.platform == "darwin":
        selected = subprocess.run(["/usr/libexec/java_home", "-v", "21"], capture_output=True, text=True)
        if selected.returncode == 0:
            home = selected.stdout.strip()
    java = Path(home) / "bin/java" if home else Path(shutil.which("java") or "/missing")
    javac = Path(home) / "bin/javac" if home else Path(shutil.which("javac") or "/missing")
    if not java.is_file() or not javac.is_file():
        raise Rejected("JAVA21_UNAVAILABLE")
    version = subprocess.run([str(java), "-version"], capture_output=True, text=True, env=java_environment())
    if version.returncode or 'version "21.' not in version.stderr:
        raise Rejected("JAVA21_REQUIRED")
    jars = []
    for group, artifact, version, expected in PINS:
        cache = Path.home() / ".gradle/caches/modules-2/files-2.1" / group / artifact / version
        candidates = [p for p in cache.glob("*/" + artifact + "-" + version + ".jar")
                      if p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest() == expected]
        if len(candidates) != 1:
            raise Rejected("PINNED_DEPENDENCY_UNAVAILABLE")
        jars.append(str(candidates[0]))
    sources = sorted((ROOT / "scripts/database").glob("*.java"))
    fingerprint = hashlib.sha256(b"".join(p.read_bytes() for p in sources) + repr(PINS).encode()).hexdigest()
    classes = ROOT / "build/database-migration" / fingerprint
    classes.mkdir(parents=True, exist_ok=True)
    cp = os.pathsep.join(jars)
    if not (classes / "FleetMigration.class").is_file():
        compiled = subprocess.run([str(javac), "--release", "21", "-encoding", "UTF-8", "-cp", cp,
                                   "-d", str(classes), *map(str, sources)], capture_output=True, text=True, env=java_environment())
        if compiled.returncode:
            raise Rejected("MIGRATION_COMPILATION_FAILED")
    return str(classes) + os.pathsep + cp, java, javac


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        config = private_file(args.config)
        cp, java, _ = build_runtime()
        command = [str(java), "-Xmx512m", "-cp", cp, "FleetMigration", "--config", str(config)]
        if args.execute:
            command.append("--execute")
        if args.expected_plan_sha256:
            command.extend(["--expected-plan-sha256", args.expected_plan_sha256])
        if args.resume:
            command.append("--resume")
        result = subprocess.run(command, capture_output=True, text=True, env=java_environment())
        # 子进程仅允许单个固定形状的 JSON 回执；驱动输出/异常正文不透传。
        try:
            report = json.loads(result.stdout)
            if not isinstance(report, dict) or report.get("status") not in ("planned", "verified", "rejected"):
                raise ValueError()
        except (ValueError, TypeError):
            raise Rejected("MIGRATION_PROCESS_FAILED") from None
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return result.returncode
    except Rejected as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
