#!/usr/bin/env python3
"""托管个人服务观测进程；私有值只传入子进程环境，不写命令行。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from observability_runtime import (SAFE_FIELDS, checkout_version, configure_binding, load_binding, prepare,
                                   secure_directory, validate_dev_bootstrap)

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    "user": (7500, "com.kellen.UserApplication"),
    "message": (7400, "com.kellen.MessageApplication"),
    "report": (7600, "com.kellen.ReportApplication"),
    "ai-memory": (7301, "com.kellen.AiMemoryApplication"),
    "rag": (7200, "com.kellen.RagApplication"),
    "mcp": (7700, "com.kellen.McpApplication"),
    "ai-agent": (7310, "com.kellen.agent.AiAgentApplication"),
    "ai": (7300, "com.kellen.AiApplication"),
    "gateway": (7100, "com.kellen.GatewayApplication"),
}
# 官方 0.164.0 darwin_arm64 archive SHA af6a0c56… 校验后提取的原始二进制摘要。
# 版本升级须重做 native validate/实际 filelog 出站验证，不能仅改私有 env 绕过固定版本。
PINNED_COLLECTOR_SHA256 = "4d60abcd101c768268f885e3c42a5d2ca083d9d7461c68c6c77dc044371aa0c5"


def private_env(path, prefixes):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("private environment file must be a restricted regular file")
    if path.stat().st_uid != os.geteuid():
        raise ValueError("private environment file must be owned by the current user")
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if not key.startswith(prefixes) or not key.replace("_", "").isalnum():
            raise ValueError("unexpected private environment key")
        parsed = shlex.split(value)
        if len(parsed) > 1:
            raise ValueError("private value must be a single quoted or unquoted value")
        result[key] = parsed[0] if parsed else ""
    return result


def process_command(pid):
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()


def owned_service(pid, service):
    command = process_command(pid)
    repo = (ROOT / service).resolve()
    # 同名主类不足以证明归属；必须同时匹配个人 checkout 的 classpath。
    return SERVICES[service][1] in command and str(repo / "build") + "/" in command


def listener(port):
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return sorted({int(line) for line in result.stdout.splitlines() if line.isdigit()})


def stop_owned(pid, service):
    if not owned_service(pid, service) or pid not in listener(SERVICES[service][0]):
        raise ValueError("PID does not own the selected personal service port")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 45
    while process_command(pid) and time.monotonic() < deadline:
        time.sleep(0.5)
    if process_command(pid):
        raise TimeoutError("old personal JVM did not stop; no forced kill attempted")


def http_status(url, token=None):
    request = urllib.request.Request(url, headers={"actuatorReq": token} if token else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            # 不读取业务响应或日志正文；metrics/health 只核 HTTP 状态。
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, urllib.error.URLError):
        return None


def write_record(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    # 保留各次启动/失败/切换记录，当前 PID 文件更新不能抹去前一次尝试。
    history = path.parent / "runtime-events.jsonl"
    with history.open("a") as stream:
        stream.write(json.dumps({"record": path.stem, "recorded_at": time.time(), **data}, ensure_ascii=False) + "\n")
    history.chmod(0o600)


def service_start(args, environment, state):
    service = args.service
    if service not in SERVICES:
        raise ValueError("not a declared service")
    repo = ROOT / service
    validate_dev_bootstrap(repo)
    token = environment.get("GITHUB_ACTUATOR_TOKEN")
    if not token or environment.get("GITHUB_NACOS_DEV_NAMESPACE", "github-dev") != "github-dev":
        raise ValueError("personal namespace or actuator token invalid")
    environment.update(APP_ENV="dev", GITHUB_NACOS_DEV_NAMESPACE="github-dev", SECURITY_ACTUATOR_TOKEN=token,
                       LOGGING_FILE_NAME=str(state / "logs" / f"{service}.json"),
                       LOGGING_STRUCTURED_FORMAT_FILE="ecs", LOGGING_STRUCTURED_FORMAT_CONSOLE="ecs",
                       LOGGING_STRUCTURED_JSON_INCLUDE=SAFE_FIELDS,
                       LOGGING_LOGBACK_ROLLINGPOLICY_MAX_FILE_SIZE="10MB",
                       LOGGING_LOGBACK_ROLLINGPOLICY_MAX_HISTORY="3",
                       LOGGING_LOGBACK_ROLLINGPOLICY_TOTAL_SIZE_CAP="40MB")
    management_port = {"gateway": 17100, "ai-agent": 17310}.get(service, SERVICES[service][0])
    management_host = "127.0.0.1"
    if service in {"gateway", "ai-agent"}:
        # 必须与 NAS Prometheus 目标显式一致；OTLP 路由可能经过代理/虚拟网卡，不能用来猜入站绑定。
        management_host = environment.get("FLEET_MANAGEMENT_BIND_ADDRESS")
        if not management_host:
            raise ValueError("explicit NAS target management interface missing")
        import ipaddress
        address = ipaddress.ip_address(management_host)
        if not address.is_private or address.is_unspecified or address.is_loopback:
            raise ValueError("management interface must be an explicit private address")
        environment.update(MANAGEMENT_SERVER_ADDRESS=management_host, MANAGEMENT_SERVER_PORT=str(management_port))
    command = [sys.executable, str(ROOT / "scripts/run-observed.py"), service]
    if management_port != SERVICES[service][0]:
        occupied = listener(management_port)
        # 管理口被其它进程占用时保留旧 JVM；共享令牌不能证明响应者就是本次服务。
        if occupied and occupied != [args.replace_pid]:
            raise ValueError("management port belongs to a different process; old JVM preserved")
    # 完整准备必须在停止旧 JVM 前完成，不能仅校验环境中的 Jar 后让实际启动缺 binding。
    launch_preflight(args, state, service)
    check = subprocess.run(command + ["--dry-run"], env=environment, capture_output=True)
    if check.returncode:
        raise ValueError("observed launcher preflight rejected runtime configuration")
    if args.replace_pid:
        stop_owned(args.replace_pid, service)
    elif listener(SERVICES[service][0]):
        raise ValueError("service port occupied; explicit owned replacement PID required")
    record = {"service": service, "replaced_pid": args.replace_pid, "started_at": time.time(), "status": "starting"}
    with (state / f"{service}.launcher.log").open("ab") as output:
        child = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    record["launcher_pid"] = child.pid
    write_record(state / f"{service}.json", record)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        pids = [pid for pid in listener(SERVICES[service][0]) if owned_service(pid, service)]
        same_process = len(pids) == 1 and listener(management_port) == pids
        # 独立管理端口会迁走 Actuator health；业务端口由已核 JVM 的监听归属验证。
        # 归属不符时连令牌探测也不发送，避免对其它本机服务泄露运维令牌。
        health = http_status(f"http://{management_host}:{management_port}/actuator/health") if same_process else None
        base = f"http://{management_host}:{management_port}/actuator/prometheus"
        metrics = http_status(base, token) if same_process else None
        if same_process and health == 200 and metrics == 200:
            unauth = http_status(base)
            wrong = http_status(base, "safe-invalid-observability-probe")
            if unauth not in {401, 403} or wrong not in {401, 403}:
                raise ValueError("management token boundary failed")
            if listener(management_port) != pids:
                raise RuntimeError("management listener changed during verification")
            record.update(status="verified", jvm_pid=pids[0], health_http=health, metrics_http=metrics,
                          unauthorized_http=unauth, wrong_token_http=wrong, management_port=management_port,
                          business_port_listening=True)
            (state / "logs" / f"{service}.json").chmod(0o600)
            write_record(state / f"{service}.json", record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return
        if child.poll() is not None:
            record.update(status="failed", launcher_exit=child.returncode)
            write_record(state / f"{service}.json", record)
            raise RuntimeError("service launcher exited; private diagnostic log retained")
        time.sleep(2)
    record.update(status="verification_timeout", management_port=management_port,
                  jvm_pid=next((pid for pid in listener(SERVICES[service][0]) if owned_service(pid, service)), None),
                  verification_scope="local client only; keep JVM running and check NAS before retrying a private bind")
    write_record(state / f"{service}.json", record)
    raise TimeoutError("service verification timed out; private logs retained")


def launch_preflight(args, state, service):
    configure_binding(args.runtime_env, args.dev_env, state, root=ROOT)
    prepare(service, checkout_version(service, ROOT), **load_binding(ROOT), local_repo=ROOT / service)


def collector_offsets(state):
    """offset 只落入 Git 外或明确忽略的私有 state；未知共享文件不自动 chmod/接管。"""
    state = secure_directory(state)
    offsets = state / "collector-offsets"
    if offsets.is_symlink() or (offsets.exists() and not offsets.is_dir()):
        raise ValueError("collector offset storage must be a private directory")
    secure_directory(offsets)
    for item in (state, offsets, *offsets.rglob("*")):
        info = item.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.geteuid() or stat.S_ISLNK(info.st_mode) or mode & 0o077:
            raise ValueError("collector offset storage ownership or permissions invalid")
        if stat.S_ISDIR(info.st_mode):
            if mode != 0o700:
                raise ValueError("collector offset directory must be 0700")
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError("collector offset storage must contain only private files")
    return offsets


def collector_start(environment, state):
    binary = Path(environment["FLEET_COLLECTOR_PATH"])
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != environment["FLEET_COLLECTOR_SHA256"] or digest != PINNED_COLLECTOR_SHA256:
        raise ValueError("collector binary checksum mismatch")
    offsets = collector_offsets(state)
    config = (ROOT / "utils/ops/github-dev/observability/collector-agent.yaml").resolve()
    environment["FLEET_LOG_DIR"] = str(state / "logs")
    environment["FLEET_LOG_STATE_DIR"] = str(offsets)
    # 只绑定本机专用遥测端口，不占用已有工具常用的 8888，也不修改其它监听者。
    metrics_port = int(environment.get("FLEET_COLLECTOR_METRICS_PORT", "18888"))
    if listener(metrics_port):
        raise ValueError("collector telemetry port occupied")
    overlay = state / "collector-telemetry.yaml"
    overlay.write_text("service:\n  telemetry:\n    metrics:\n      readers:\n        - pull:\n            exporter:\n              prometheus:\n                host: 127.0.0.1\n                port: " + str(metrics_port) + "\n")
    destination = urlsplit(environment["OTEL_EXPORTER_OTLP_ENDPOINT"]).hostname
    direct = ",".join(filter(None, [environment.get("NO_PROXY"), destination, "127.0.0.1", "localhost"]))
    environment.update(NO_PROXY=direct, no_proxy=direct)
    config_args = ["--config", str(config), "--config", str(overlay)]
    command = [str(binary), *config_args]
    validation = subprocess.run([str(binary), "validate", *config_args], env=environment, capture_output=True)
    if validation.returncode:
        (state / "collector.validate.log").write_bytes(validation.stdout + validation.stderr)
        raise ValueError("native collector configuration validation failed")
    record_file = state / "collector.json"
    if record_file.exists():
        old = json.loads(record_file.read_text())
        if str(binary) in process_command(old["pid"]):
            raise ValueError("managed collector already running")
    with (state / "collector.launcher.log").open("ab") as output:
        child = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2)
    if child.poll() is not None:
        raise RuntimeError("native collector exited; private log retained")
    record = {"pid": child.pid, "status": "running", "version": "0.164.0", "configuration_validated": True,
              "local_telemetry_port": metrics_port, "offset_storage": "file_storage/offsets",
              "offset_directory_private": True}
    write_record(record_file, record)
    print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "collector-start"])
    parser.add_argument("service", nargs="?")
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--dev-env", type=Path, default=Path.home() / ".config/kellen-github/dev.env")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "build/observability-runtime")
    parser.add_argument("--replace-pid", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    os.umask(0o077)
    state = args.state_dir.resolve()
    (state / "logs").mkdir(parents=True, mode=0o700, exist_ok=True)
    environment = dict(os.environ)
    for key in ["JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"]:
        environment.pop(key, None)
    try:
        environment.update(private_env(args.dev_env, ("GITHUB_",)))
        environment.update(private_env(args.runtime_env, ("OTEL_", "FLEET_")))
        if args.action == "start":
            service_start(args, environment, state)
        else:
            collector_start(environment, state)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__, "private_values": "omitted"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
