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

from observability_runtime import (ROOT, SERVICES, checkout_version, configure_binding, ide_controlled_env_name,
                                   failure_hint, failure_reason, idea_reference, idea_vm_arguments, idea_service_environment, load_binding,
                                   prepare, runtime_inputs, RuntimeValidationError,
                                   secure_directory, validate_idea_application_args)
import workspace
from idea_external_tools import (apply_external_tools, assert_plan_current, plan_external_tools, tool_action_id)


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
            raise RuntimeValidationError("IDE_MODULE_MISMATCH")
        repo = root / service
        result = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], text=True, capture_output=True)
        if result.returncode or workspace.github_identity(result.stdout.strip()) != workspace.github_identity(manifest[service]["url"]):
            raise RuntimeValidationError("IDE_REPOSITORY_MISMATCH")
        receipt = prepare(service, checkout_version(service, root), **inputs, write=False, local_repo=repo)
        prior = config.find("option[@name='VM_PARAMETERS']")
        arguments = idea_vm_arguments(prior.get("value", "")) if prior is not None else []
        retained = []
        references = set()
        for argument in arguments:
            reference = idea_reference(argument, receipt, root)
            if reference is not None:
                if reference in references:
                    raise RuntimeValidationError("IDE_REFERENCE_DUPLICATE")
                references.add(reference)
            elif argument.startswith("-D") and ide_controlled_env_name(argument[2:].split("=", 1)[0], receipt):
                raise RuntimeValidationError("IDE_VM_OVERRIDE")
            # 等价的 IDEA 宏/URI 拼写原样保留，避免每次保存又被迁移器改回绝对路径。
            retained.append(argument)
        for argument in receipt["jvm_arguments"]:
            if idea_reference(argument, receipt, root) not in references:
                retained.append(argument)
        program = config.find("option[@name='PROGRAM_PARAMETERS']")
        validate_idea_application_args(shlex.split(program.get("value", "")) if program is not None else [], receipt)
        if any(ide_controlled_env_name(item.get("name", ""), receipt) for item in config.findall("./envs/env")):
            raise RuntimeValidationError("IDE_ENV_OVERRIDE")
        idea_service_environment(config,receipt,root,update=True)
        # IntelliJ 的 VM 参数语法支持双引号；XML 中只有路径，无令牌或连接配置正文。
        encoded = " ".join('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
                           for value in retained)
        option(config, "VM_PARAMETERS", encoded)
        working = config.find("option[@name='WORKING_DIRECTORY']")
        if working is None:
            option(config, "WORKING_DIRECTORY", f"$PROJECT_DIR$/{service}")
        method = config.find("method")
        if method is None:
            method = ET.SubElement(config, "method", {"v": "2"})
        legacy_task = "prepareObserved" + "".join(part.capitalize() for part in service.split("-"))
        for item in list(method.findall("option")):
            if item.get("name") == "Gradle.BeforeRunTask":
                external = item.get("externalProjectPath", "").replace("$PROJECT_DIR$", str(root))
                if item.get("tasks") != legacy_task or Path(external).resolve() != root.resolve():
                    # 用户自定义任务不自动删除；保留原文件，避免悄悄改变其运行流程。
                    raise RuntimeValidationError("IDE_PREFLIGHT_CONFLICT")
                method.remove(item)
        action = tool_action_id(service)
        hooks = [item for item in method.findall("option")
                 if item.get("name") == "ToolBeforeRunTask" and item.get("actionId") == action]
        if len(hooks) > 1:
            raise RuntimeValidationError("IDE_PREFLIGHT_CONFLICT")
        hook = hooks[0] if hooks else ET.SubElement(method, "option")
        hook.attrib.update({"name": "ToolBeforeRunTask", "enabled": "true", "actionId": action})
        selected.append({"service": service, "name": config.get("name"), "module": module.get("name"),
                         "preflight_action": action})
    if len({entry["service"] for entry in selected}) != len(SERVICES):
        raise RuntimeValidationError("IDE_APPLICATION_MISSING")
    after = workspace.idea_xml_bytes(tree)
    workspace.parse_idea_xml_bytes(after)
    return path, before, after, selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--dev-env", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--service-env",action="append",help="service=/absolute/private/environment.json; values are never arguments")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--idea-config-dir", type=Path, required=True)
    parser.add_argument("--expected-tools-sha256")
    args = parser.parse_args()
    try:
        inputs = load_binding() if not args.runtime_env else {
            "runtime_env": args.runtime_env, "dev_env": args.dev_env,
            "state_dir": args.state_dir or ROOT / "build/observability-runtime"}
        if args.service_env is not None:
            from service_runtime_environment import parse_references
            inputs["service_envs"]={**inputs.get("service_envs",{}),**parse_references(args.service_env)}
        runtime_inputs(inputs["runtime_env"], inputs["dev_env"])
        path, before, after, selected = candidate(ROOT, inputs)
        tools = plan_external_tools(ROOT, args.idea_config_dir)
        digest = hashlib.sha256(before).hexdigest()
        if not args.dry_run:
            if digest != args.expected_sha256 or tools["before_sha256"] != args.expected_tools_sha256:
                raise RuntimeValidationError("IDE_METADATA_CHANGED")
            assert_plan_current(tools)
            configure_binding(root=ROOT, **inputs)
            for entry in selected:
                service = entry["service"]
                prepare(service, checkout_version(service), **inputs, local_repo=ROOT / service)
            # CAS 和受限备份先于替换；IDE 内存中的配置需另外刷新验收。
            if workspace.read_idea_bytes(path) != before:
                raise RuntimeValidationError("IDE_METADATA_CHANGED")
            backup = secure_directory(Path(inputs["state_dir"]) / "idea-backup") / f"workspace-{digest}.xml"
            if not backup.exists():
                from observability_runtime import write_private
                write_private(backup, before.decode("utf-8"))
            assert_plan_current(tools)
            apply_external_tools(tools, Path(inputs["state_dir"]) / "idea-backup")
            if workspace.read_idea_bytes(path) != before:
                raise RuntimeValidationError("IDE_METADATA_CHANGED")
            descriptor, temporary = tempfile.mkstemp(prefix=".observed-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(after)
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        print(json.dumps({"dry_run": args.dry_run, "source_sha256": digest, "matched": selected,
                          "tools_path": str(tools["path"]), "tools_source_sha256": tools["before_sha256"],
                          "tools_target_sha256": tools["after_sha256"],
                          "changed": before != after, "private_values": "omitted",
                          "ui_refresh_verified": False}, ensure_ascii=False))
        return 0
    except Exception as error:
        reason = failure_reason(error)
        print(json.dumps({"status": "failed", "error_class": type(error).__name__, "reason_code": reason,
                          "hint": failure_hint(reason), "private_values": "omitted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
