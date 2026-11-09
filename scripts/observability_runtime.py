"""个人 JVM 三种启动入口共用的制品校验与私有配置准备。"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PINNED_AGENT_SHA256 = "e43a270df53c6f423e0ab157be9c27ccc01d5dc3232e162fe54777573db2f149"
SERVICES = {
    "gateway": "com.kellen.GatewayApplication", "user": "com.kellen.UserApplication",
    "ai-agent": "com.kellen.agent.AiAgentApplication", "ai": "com.kellen.AiApplication",
    "ai-memory": "com.kellen.AiMemoryApplication", "rag": "com.kellen.RagApplication",
    "mcp": "com.kellen.McpApplication", "message": "com.kellen.MessageApplication",
    "report": "com.kellen.ReportApplication",
}
SAFE_FIELDS = "@timestamp,log,log.level,service,service.name,service.version,service.environment,event,event.action,duration_ms,error_code,trace_id,span_id"
CONTROLLED_ENV_PREFIXES = ("OTEL_", "MANAGEMENT_SERVER_", "LOGGING_", "GITHUB_")
CONTROLLED_ENV_NAMES = {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "APP_ENV",
                        "SECURITY_ACTUATOR_TOKEN", "SPRING_APPLICATION_VERSION", "SPRING_APPLICATION_JSON",
                        "SPRING_CONFIG_ADDITIONAL_LOCATION", "SPRING_PROFILES_ACTIVE"}


def controlled_environment(environment):
    return {key: value for key, value in environment.items()
            if not controlled_env_name(key)}


def managed_property(name):
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return normalized.startswith(("otel", "management", "securityactuator", "logging", "github", "appenv",
                                  "springapplicationversion", "springapplicationname", "springapplicationjson",
                                  "springconfig", "springprofiles"))


def controlled_env_name(name):
    return managed_property(name) or name in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}


def private_output(path):
    """生成配置不得落入可跟踪的 Git 路径；不依赖调用者碰巧使用默认 build/。"""
    path = Path(path).absolute()
    for ancestor in path.parents:
        if (ancestor / ".git").exists():
            result = subprocess.run(["git", "-C", str(ancestor), "check-ignore", "--quiet", "--", str(path)],
                                    capture_output=True)
            if result.returncode:
                raise ValueError("private output must be outside Git or explicitly ignored")
            break


def validate_application_args(arguments):
    if any(argument.startswith("--") and managed_property(argument[2:].split("=", 1)[0]) for argument in arguments):
        raise ValueError("application argument overrides private runtime configuration")


def private_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("private input must be a regular file")
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
        raise ValueError("private input owner, permissions or size invalid")
    return path


def private_env(path, prefixes):
    values = {}
    for line in private_file(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if not key.startswith(prefixes) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("private environment key invalid")
        parsed = shlex.split(value)
        if len(parsed) > 1 or any(c in value for c in ("\x00", "\r", "\n")):
            raise ValueError("private environment value invalid")
        values[key] = parsed[0] if parsed else ""
    return values


def verified_jar(path_key, digest_key, version, environment=None):
    environment = os.environ if environment is None else environment
    path = Path(environment[path_key]).resolve(strict=True)
    digest = environment[digest_key]
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
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


def runtime_inputs(runtime_env, dev_env):
    environment = private_env(dev_env, ("GITHUB_",))
    environment.update(private_env(runtime_env, ("OTEL_", "FLEET_")))
    if not environment.get("GITHUB_ACTUATOR_TOKEN") or environment.get("GITHUB_NACOS_DEV_NAMESPACE", "github-dev") != "github-dev":
        raise ValueError("personal namespace or actuator token invalid")
    agent = verified_jar("OTEL_JAVAAGENT_PATH", "OTEL_JAVAAGENT_SHA256", "2.33.1", environment)
    privacy = verified_jar("FLEET_PRIVACY_EXTENSION_PATH", "FLEET_PRIVACY_EXTENSION_SHA256", "0.1.0", environment)
    endpoint = urlsplit(environment["OTEL_EXPORTER_OTLP_ENDPOINT"])
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("collector endpoint invalid")
    ratio = environment.get("FLEET_TRACE_RATIO", "0.1")
    if not re.fullmatch(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", ratio):
        raise ValueError("trace ratio invalid")
    return environment, agent, privacy


def secure_directory(path, create=True):
    path = Path(path)
    private_output(path / ".private-output-probe")
    # 不允许已存在的父级 symlink 把受限输出导向其它位置。
    for item in (path, *path.parents):
        if item.is_symlink() and item.lstat().st_uid != 0:
            raise ValueError("private output path must not traverse a symlink")
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists() and (path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
        raise ValueError("private output directory must be owner-only")
    return path


def write_private(path, data):
    path = Path(path)
    private_output(path)
    secure_directory(path.parent)
    if path.exists() or path.is_symlink():
        private_file(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".prepare-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def properties(values):
    def escape(value):
        result = []
        for char in str(value):
            if char in "\\=:#! ":
                result.append("\\" + char)
            elif char in "\r\n\t":
                result.append({"\r": "\\r", "\n": "\\n", "\t": "\\t"}[char])
            elif not 0x20 <= ord(char) <= 0x7e:
                encoded = char.encode("utf-16-be")
                result.extend("\\u" + encoded[i:i + 2].hex() for i in range(0, len(encoded), 2))
            else:
                result.append(char)
        return "".join(result)
    return "".join(f"{escape(key)}={escape(value)}\n" for key, value in values.items())


def checkout_version(service, root=ROOT):
    text = (root / service / "build.gradle").read_text()
    match = re.search(r"(?m)^version\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        raise ValueError("service version must be explicit")
    return match.group(1)


def binding_path(root=ROOT):
    return root / "build/observability-runtime/launch.json"


def load_binding(root=ROOT):
    result = json.loads(private_file(binding_path(root)).read_text())
    if set(result) != {"runtime_env", "dev_env", "state_dir"}:
        raise ValueError("local launch binding invalid")
    return {key: Path(value) for key, value in result.items()}


def configure_binding(runtime_env, dev_env, state_dir, root=ROOT):
    runtime_inputs(runtime_env, dev_env)
    result = {"runtime_env": str(Path(runtime_env).absolute()), "dev_env": str(Path(dev_env).absolute()),
              "state_dir": str(Path(state_dir).absolute())}
    write_private(binding_path(root), json.dumps(result, indent=2) + "\n")


def prepare(service, version, runtime_env, dev_env, state_dir, write=True):
    if service not in SERVICES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", version):
        raise ValueError("service or version invalid")
    environment, agent, privacy = runtime_inputs(runtime_env, dev_env)
    state = Path(state_dir).absolute()
    directory = state / "prepared" / service
    logs = state / "logs"
    secure_directory(state, create=write)
    secure_directory(directory, create=write)
    secure_directory(logs, create=write)
    app = {key: value for key, value in environment.items() if key.startswith("GITHUB_")}
    app.update({"APP_ENV": "dev", "GITHUB_NACOS_DEV_NAMESPACE": "github-dev",
                "security.actuator.token": environment["GITHUB_ACTUATOR_TOKEN"],
                "spring.application.version": version, "logging.file.name": str(logs / f"{service}.json"),
                "logging.structured.format.file": "ecs", "logging.structured.format.console": "ecs",
                "logging.structured.json.include": SAFE_FIELDS,
                "logging.logback.rollingpolicy.max-file-size": "10MB",
                "logging.logback.rollingpolicy.max-history": "3",
                "logging.logback.rollingpolicy.total-size-cap": "40MB"})
    if service in {"gateway", "ai-agent"}:
        address = ipaddress.ip_address(environment["FLEET_MANAGEMENT_BIND_ADDRESS"])
        if not address.is_private or address.is_unspecified or address.is_loopback:
            raise ValueError("management interface must be an explicit private address")
        app.update({"management.server.address": str(address),
                    "management.server.port": "17100" if service == "gateway" else "17310"})
    telemetry = {"otel.javaagent.extensions": str(privacy), "otel.service.name": service,
                 "otel.resource.attributes": f"service.version={version},deployment.environment.name=github-dev",
                 "otel.exporter.otlp.protocol": "http/protobuf",
                 "otel.exporter.otlp.endpoint": environment["OTEL_EXPORTER_OTLP_ENDPOINT"],
                 "otel.exporter.otlp.headers": environment.get("OTEL_EXPORTER_OTLP_HEADERS", ""),
                 "otel.traces.exporter": "otlp", "otel.metrics.exporter": "none", "otel.logs.exporter": "none",
                 "otel.propagators": "tracecontext", "otel.span.event.count.limit": "0",
                 "otel.traces.sampler": "traceidratio" if service == "gateway" else "parentbased_traceidratio",
                 "otel.traces.sampler.arg": environment.get("FLEET_TRACE_RATIO", "0.1"),
                 "otel.bsp.max.queue.size": "256", "otel.bsp.max.export.batch.size": "32",
                 "otel.exporter.otlp.timeout": "1000"}
    agent_config, app_config = directory / "agent.properties", directory / "application.properties"
    for path in (agent_config, app_config):
        if path.exists() or path.is_symlink():
            private_file(path)
    if write:
        write_private(agent_config, properties(telemetry))
        write_private(app_config, properties(app))
    # 回执只有文件路径、版本和固定开关；令牌、地址等值不进入命令行或 stdout。
    return {"service": service, "version": version, "agent": str(agent), "privacy": str(privacy),
            "agent_config": str(agent_config), "app_config": str(app_config),
            "jvm_arguments": [f"-javaagent:{agent}", f"-Dotel.javaagent.extensions={privacy}",
                              f"-Dotel.javaagent.configuration-file={agent_config}",
                              f"-Dspring.config.additional-location={app_config.as_uri()}"]}


def check_idea_runtime(service, receipt, root=ROOT):
    """IDE 每次运行前拒绝用户 VM/env 中会覆盖受控配置的值，不输出其内容。"""
    import workspace
    if any(value for key, value in os.environ.items() if controlled_env_name(key)):
        raise ValueError("inherited IDE environment overrides private runtime configuration")
    tree = workspace.parse_idea_xml(root / ".idea/workspace.xml")
    matched = False
    expected = set(receipt["jvm_arguments"])
    for config in tree.getroot().findall("./component[@name='RunManager']/configuration"):
        if config.get("type") not in {"SpringBootApplicationConfigurationType", "Application"}:
            continue
        options = {item.get("name"): item.get("value", "") for item in config.findall("option")}
        main = options.get("SPRING_BOOT_MAIN_CLASS", options.get("MAIN_CLASS_NAME"))
        if main != SERVICES[service]:
            continue
        module = config.find("module")
        if module is None or module.get("name") not in {service, f"{service}.main"}:
            raise ValueError("IDE module outside personal service scope")
        arguments = shlex.split(options.get("VM_PARAMETERS", ""))
        if not expected.issubset(arguments):
            raise ValueError("IDE runtime references missing; configure the Application entry")
        for argument in arguments:
            if argument in expected:
                continue
            if argument.startswith("-javaagent:") or (argument.startswith("-D") and managed_property(argument[2:].split("=", 1)[0])):
                raise ValueError("IDE VM parameter overrides private runtime configuration")
        validate_application_args(shlex.split(options.get("PROGRAM_PARAMETERS", "")))
        for item in config.findall("./envs/env"):
            key = item.get("name", "")
            if controlled_env_name(key):
                raise ValueError("IDE environment overrides private runtime configuration")
        matched = True
    if not matched:
        raise ValueError("personal IDE Application entry missing")
