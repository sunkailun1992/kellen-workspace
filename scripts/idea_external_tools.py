"""为个人 Spring Boot 入口准备独立的 IDEA 原生运行前校验工具。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from observability_runtime import SERVICES, write_private
import workspace

GROUP = "KellenWorkspaceObservability"
FILENAME = GROUP + ".xml"
OWNER_PREFIX = "Managed by kellen-workspace observability v1; root-sha256="
FLAGS = {"showInMainMenu": "false", "showInEditor": "false", "showInProject": "false",
         "showInSearchPopup": "false", "disabled": "false", "useConsole": "true",
         "showConsoleOnStdOut": "false", "showConsoleOnStdErr": "true", "synchronizeAfterRun": "false"}


def tool_action_id(service):
    if service not in SERVICES:
        raise ValueError("native tool service is not declared")
    return f"Tool_{GROUP}_Prepare {service}"


def _directory(path):
    for item in (path, *path.parents):
        if item.is_symlink() and item.lstat().st_uid != 0:
            raise ValueError("IDE tool directory must not traverse a user symlink")
    if not path.is_dir() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
        raise ValueError("IDE tool directory ownership or permissions invalid")


def _python_program(value):
    # 保留稳定的 Homebrew/bin symlink，不将 Cellar 的版本目录写入 IDE 配置。
    value = value or shutil.which("python3")
    if not value:
        raise ValueError("Python 3.10 or newer is required")
    program = Path(value).absolute()
    if not program.is_file() or not os.access(program, os.X_OK):
        raise ValueError("Python program is not an executable file")
    result = subprocess.run([str(program), "-I", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                            capture_output=True, text=True, timeout=5)
    try:
        version = tuple(int(part) for part in result.stdout.strip().split("."))
    except ValueError:
        version = ()
    if result.returncode or len(version) != 2 or version < (3, 10):
        raise ValueError("Python 3.10 or newer is required")
    return program


def _quote(value):
    text = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("IDE tool path contains a control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tool_set(root, program, root_id):
    element = ET.Element("toolSet", {"name": GROUP})
    for service in SERVICES:
        tool = ET.SubElement(element, "tool", {"name": f"Prepare {service}",
                                               "description": OWNER_PREFIX + root_id, **FLAGS})
        command = ET.SubElement(tool, "exec")
        values = {"COMMAND": str(program),
                  "PARAMETERS": f"{_quote(root / 'scripts/prepare-observed.py')} prepare {service} --check-idea --quiet",
                  "WORKING_DIRECTORY": str(root)}
        for name, value in values.items():
            ET.SubElement(command, "option", {"name": name, "value": value})
    return element


def _expand_home(value):
    value = value.replace("$USER_HOME$", str(Path.home()))
    if "$" in value:
        raise ValueError("unknown macro in owned IDE tool")
    return value


def _validate_owned(data, root, program, root_id):
    tree = workspace.parse_idea_xml_bytes(data)
    top = tree.getroot()
    if top.tag != "toolSet" or top.attrib != {"name": GROUP}:
        raise ValueError("existing IDE tools file belongs to another owner")
    entries = top.findall("tool")
    expected = {f"Prepare {service}": service for service in SERVICES}
    if (any(isinstance(child.tag, str) and child.tag != "tool" for child in top)
            or len(entries) != len(expected) or {entry.get("name") for entry in entries} != set(expected)):
        raise ValueError("existing IDE tools group has unknown entries")
    for entry in entries:
        if entry.get("description") != OWNER_PREFIX + root_id:
            raise ValueError("existing IDE tools file belongs to another workspace")
        expected_attributes = {"name": entry.get("name"), "description": OWNER_PREFIX + root_id, **FLAGS}
        if entry.attrib != expected_attributes or any(isinstance(child.tag, str) and child.tag != "exec" for child in entry):
            raise ValueError("owned IDE tool settings were changed; preserve user setting")
        execution = entry.findall("exec")
        if len(execution) != 1 or execution[0].attrib or len(execution[0]) != 3:
            raise ValueError("owned IDE tool command was changed")
        if any(child.tag != "option" or set(child.attrib) != {"name", "value"} or len(child)
               for child in execution[0]):
            raise ValueError("owned IDE tool command was changed")
        options = {v.get("name"): v.get("value", "") for v in execution[0].findall("option")}
        service = expected[entry.get("name")]
        import shlex
        parameters = shlex.split(_expand_home(options.get("PARAMETERS", "")))
        if (Path(_expand_home(options.get("COMMAND", ""))).absolute() != program
                or Path(_expand_home(options.get("WORKING_DIRECTORY", ""))).resolve() != root
                or parameters != [str(root / "scripts/prepare-observed.py"), "prepare", service, "--check-idea", "--quiet"]):
            raise ValueError("owned IDE tool command was changed")


def _current(path):
    if path.is_symlink():
        raise ValueError("IDE tools file must not be a symlink")
    if not path.exists():
        return None
    info = path.stat()
    if not path.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o022 or info.st_size > 1024 * 1024:
        raise ValueError("IDE tools file ownership, permissions or size invalid")
    return path.read_bytes()


def plan_external_tools(root, config_dir, python=None):
    """只生成候选和摘要；不创建目录、不改写 IDEA 文件。"""
    root = Path(root).resolve()
    if not (root / "scripts/prepare-observed.py").is_file():
        raise ValueError("personal preflight script is missing")
    config = Path(config_dir).absolute()
    _directory(config)
    directory = config / "tools"
    if directory.exists() or directory.is_symlink():
        _directory(directory)
    program = _python_program(python)
    root_id = hashlib.sha256(str(root).encode()).hexdigest()
    path = directory / FILENAME
    before = _current(path)
    if before is not None:
        _validate_owned(before, root, program, root_id)
    after = workspace.idea_xml_bytes(ET.ElementTree(_tool_set(root, program, root_id)))
    return {"path": path, "before": before, "after": after, "root_id": root_id,
            "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
            "after_sha256": hashlib.sha256(after).hexdigest(), "python": program}


def assert_plan_current(plan):
    if _current(plan["path"]) != plan["before"]:
        raise ValueError("IDE tools changed; review a fresh plan")


def apply_external_tools(plan, backup_dir):
    """只发布自有工具组；CAS 失败保留现有文件，其它工具组不读取、不改写。"""
    assert_plan_current(plan)
    path = plan["path"]
    _directory(path.parent.parent)
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700)
    _directory(path.parent)
    if plan["before"] == plan["after"]:
        return {"path": str(path), "changed": False, "sha256": plan["after_sha256"], "ui_loaded": False}
    backup = None
    if plan["before"] is not None:
        backup = Path(backup_dir) / ("external-tools-" + plan["before_sha256"] + ".xml")
        if backup.exists() or backup.is_symlink():
            from observability_runtime import private_file
            if private_file(backup).read_bytes() != plan["before"]:
                raise ValueError("existing IDE tools backup differs")
        else:
            write_private(backup, plan["before"].decode("utf-8"))
    descriptor, temporary = tempfile.mkstemp(prefix=".kellen-observability-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(plan["after"])
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        assert_plan_current(plan)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(path), "changed": True, "sha256": plan["after_sha256"],
            "backup": str(backup) if backup else None, "ui_loaded": False}
