#!/usr/bin/env python3
"""仅为本工作区已声明的个人 Spring Boot 配置添加观测，不改其它运行入口。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from observability_runtime import (ROOT, SERVICES, checkout_version, configure_binding, controlled_env_name,
                                   load_binding, managed_property, prepare, runtime_inputs, secure_directory, validate_application_args)
import workspace


def option(parent, name, value):
    item = parent.find(f"option[@name='{name}']")
    if item is None:
        item = ET.SubElement(parent, "option", {"name": name})
    item.set("value", value)


def candidate(root, inputs):
    path = root / ".idea/workspace.xml"
    before = workspace.read_idea_bytes(path)
    tree = workspace.parse_idea_xml_bytes(before)
    manifest = {entry["name"]: entry for entry in workspace.load_manifest(root)}
    selected = []
    for config in tree.getroot().findall("./component[@name='RunManager']/configuration"):
        if config.get("type") not in {"SpringBootApplicationConfigurationType", "Application"}:
            continue
        main = config.find("option[@name='SPRING_BOOT_MAIN_CLASS']")
        if main is None:
            main = config.find("option[@name='MAIN_CLASS_NAME']")
        service = next((name for name, cls in SERVICES.items() if main is not None and main.get("value") == cls), None)
        if service is None:
            continue
        module = config.find("module")
        if module is None or module.get("name") not in {service, f"{service}.main"}:
            raise ValueError("declared main class has an unexpected IDE module")
        repo = root / service
        result = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], text=True, capture_output=True)
        if result.returncode or workspace.github_identity(result.stdout.strip()) != workspace.github_identity(manifest[service]["url"]):
            raise ValueError("IDE module does not own the declared personal repository")
        receipt = prepare(service, checkout_version(service, root), **inputs, write=False)
        prior = config.find("option[@name='VM_PARAMETERS']")
        arguments = shlex.split(prior.get("value", "")) if prior is not None else []
        owned = ("-Dotel.javaagent.configuration-file=", "-Dotel.javaagent.extensions=", "-Dspring.config.additional-location=")
        retained = []
        for argument in arguments:
            if argument.startswith(owned):
                # 不覆盖用户指向别处的 Spring/OTel 配置。
                value = argument.split("=", 1)[1]
                if value not in {receipt["agent_config"], receipt["privacy"], Path(receipt["app_config"]).as_uri()}:
                    raise ValueError("custom runtime configuration must be retained; migration stopped")
            elif argument.startswith("-javaagent:"):
                if argument != f'-javaagent:{receipt["agent"]}':
                    raise ValueError("custom Java agent must be retained; migration stopped")
            else:
                if argument.startswith("-D") and managed_property(argument[2:].split("=", 1)[0]):
                    raise ValueError("custom VM parameter overrides private runtime configuration")
                retained.append(argument)
        program = config.find("option[@name='PROGRAM_PARAMETERS']")
        validate_application_args(shlex.split(program.get("value", "")) if program is not None else [])
        if any(controlled_env_name(item.get("name", "")) for item in config.findall("./envs/env")):
            raise ValueError("custom environment overrides private runtime configuration")
        # IntelliJ 的 VM 参数语法支持双引号；XML 中只有路径，无令牌或连接配置正文。
        encoded = " ".join('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
                           for value in [*retained, *receipt["jvm_arguments"]])
        option(config, "VM_PARAMETERS", encoded)
        working = config.find("option[@name='WORKING_DIRECTORY']")
        if working is None:
            option(config, "WORKING_DIRECTORY", f"$PROJECT_DIR$/{service}")
        method = config.find("method")
        if method is None:
            method = ET.SubElement(config, "method", {"v": "2"})
        task = "prepareObserved" + "".join(part.capitalize() for part in service.split("-"))
        hook = next((item for item in method.findall("option")
                     if item.get("name") == "Gradle.BeforeRunTask" and item.get("tasks") == task), None)
        if hook is None:
            hook = ET.SubElement(method, "option")
        hook.attrib.update({"name": "Gradle.BeforeRunTask", "enabled": "true", "tasks": task,
                            "externalProjectPath": "$PROJECT_DIR$", "vmOptions": "", "scriptParameters": ""})
        selected.append({"service": service, "name": config.get("name"), "module": module.get("name"), "preflight_task": task})
    if len({entry["service"] for entry in selected}) != len(SERVICES):
        raise ValueError("all nine personal Application configurations must be present")
    after = workspace.idea_xml_bytes(tree)
    workspace.parse_idea_xml_bytes(after)
    return path, before, after, selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--dev-env", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        inputs = load_binding() if not args.runtime_env else {
            "runtime_env": args.runtime_env, "dev_env": args.dev_env,
            "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
        runtime_inputs(inputs["runtime_env"], inputs["dev_env"])
        path, before, after, selected = candidate(ROOT, inputs)
        digest = hashlib.sha256(before).hexdigest()
        if not args.dry_run:
            if digest != args.expected_sha256:
                raise ValueError("IDE metadata changed; review a fresh dry run")
            configure_binding(root=ROOT, **inputs)
            for entry in selected:
                prepare(entry["service"], checkout_version(entry["service"]), **inputs)
            # CAS 和受限备份先于替换；IDE 内存中的配置需另外刷新验收。
            if workspace.read_idea_bytes(path) != before:
                raise ValueError("IDE metadata changed during preparation")
            backup = secure_directory(Path(inputs["state_dir"]) / "idea-backup") / f"workspace-{digest}.xml"
            if not backup.exists():
                from observability_runtime import write_private
                write_private(backup, before.decode("utf-8"))
            descriptor, temporary = tempfile.mkstemp(prefix=".observed-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(after)
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        print(json.dumps({"dry_run": args.dry_run, "source_sha256": digest, "matched": selected,
                          "changed": before != after, "private_values": "omitted",
                          "ui_refresh_verified": False}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__, "private_values": "omitted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
