"""三种启动入口共用的校验、秘密文件和 IDEA 精确迁移边界。"""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import observability_runtime as runtime
import workspace

spec = importlib.util.spec_from_file_location("idea_observed", ROOT / "scripts/configure-idea-observed.py")
idea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idea)


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="观测 启动 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.agent = self.root / "agent.jar"
        self.privacy = self.root / "privacy extension.jar"
        for path, manifest in ((self.agent, "Implementation-Version: 2.33.1\n"),
                               (self.privacy, "Implementation-Version: 0.1.1\nFleet-Agent-Version: 2.33.1\n")):
            with zipfile.ZipFile(path, "w") as jar:
                jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n" + manifest)
        self.agent_digest = hashlib.sha256(self.agent.read_bytes()).hexdigest()
        self.addCleanup(patch.stopall)
        patch.object(runtime, "PINNED_AGENT_SHA256", self.agent_digest).start()
        self.dev = self.root / "dev.env"
        self.dev.write_text("GITHUB_ACTUATOR_TOKEN='SYNTHETIC_ACTUATOR_SECRET'\nGITHUB_NACOS_DEV_NAMESPACE=github-dev\n")
        self.dev.chmod(0o600)
        self.env = self.root / "runtime.env"
        values = {"OTEL_JAVAAGENT_PATH": str(self.agent), "OTEL_JAVAAGENT_SHA256": self.agent_digest,
                  "FLEET_PRIVACY_EXTENSION_PATH": str(self.privacy),
                  "FLEET_PRIVACY_EXTENSION_SHA256": hashlib.sha256(self.privacy.read_bytes()).hexdigest(),
                  "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:14318",
                  "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer%20SYNTHETIC_OTLP_SECRET",
                  "FLEET_MANAGEMENT_BIND_ADDRESS": "10.0.0.9"}
        self.env.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
        self.env.chmod(0o600)
        self.inputs = {"runtime_env": self.env, "dev_env": self.dev, "state_dir": self.root / "state"}

    def test_prepare_keeps_secrets_in_owner_only_properties_and_matches_version(self):
        result = runtime.prepare("gateway", "1.1.0", **self.inputs)
        public = json.dumps(result)
        self.assertNotIn("SYNTHETIC_", public)
        app = Path(result["app_config"])
        agent = Path(result["agent_config"])
        self.assertEqual(app.stat().st_mode & 0o777, 0o600)
        self.assertEqual(app.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn("SYNTHETIC_ACTUATOR_SECRET", app.read_text())
        self.assertIn("SYNTHETIC_OTLP_SECRET", agent.read_text())
        self.assertIn("management.server.port=17100", app.read_text())
        self.assertIn("spring.application.version=1.1.0", app.read_text())
        self.assertIn("service.version\\=1.1.0", agent.read_text())
        self.assertIn("otel.span.event.count.limit=0", agent.read_text())
        self.assertIn("otel.metrics.exporter=none", agent.read_text())
        self.assertIn("logging.structured.format.file=ecs", app.read_text())
        self.assertNotIn("message,", app.read_text())

    def test_dry_prepare_validates_without_writing(self):
        result = runtime.prepare("rag", "1.1.6", **self.inputs, write=False)
        self.assertFalse(self.inputs["state_dir"].exists())
        self.assertEqual(result["version"], "1.1.6")

    def test_development_runtime_selects_non_parent_sampling_for_every_service(self):
        # 旧私有比例不能让个人开发环境继续漏掉入站sampled=0请求。
        with self.env.open("a") as stream:
            stream.write("FLEET_TRACE_RATIO=0.1\n")
        for service in runtime.SERVICES:
            with self.subTest(service=service):
                result = runtime.prepare(service, "1.0.0", **self.inputs)
                agent = Path(result["agent_config"]).read_text()
                app = Path(result["app_config"]).read_text()
                self.assertIn("otel.traces.sampler=always_on\n", agent)
                self.assertNotIn("otel.traces.sampler.arg=", agent)
                self.assertIn("otel.propagators=tracecontext\n", agent)
                self.assertIn("fleet.observability.enabled=true\n", app)
                self.assertIn("fleet.observability.max-body-bytes=8192\n", app)
                self.assertIn("fleet.observability.ai-content-enabled=false\n", app)

    def test_structured_io_fields_preserve_safe_events_without_raw_messages(self):
        result = runtime.prepare("rag", "1.0.0", **self.inputs)
        fields = set(runtime.SAFE_FIELDS.split(","))
        self.assertTrue({"summary", "event", "http_method", "http_route", "http_status", "request_body",
                         "response_body", "request_body_complete", "response_body_complete", "error_type",
                         "ai_operation", "ai_model", "ai_input_tokens", "ai_output_tokens",
                         "ai_reasoning_tokens", "ai_usage_available", "trace_id", "span_id"} <= fields)
        self.assertTrue({"message", "error.message", "error.stack_trace", "password", "authorization"}.isdisjoint(fields))
        self.assertNotIn("SYNTHETIC_", json.dumps(result))

    def test_mutated_artifact_rejected_on_every_prepare(self):
        runtime.prepare("rag", "1.1.6", **self.inputs)
        self.privacy.write_bytes(b"replaced after prior successful preparation")
        with self.assertRaises(ValueError):
            runtime.prepare("rag", "1.1.6", **self.inputs)

    def test_invalid_namespace_bind_and_executable_environment_rejected(self):
        for address in ("0.0.0.0", "127.0.0.1", "8.8.8.8"):
            text = self.env.read_text().replace("10.0.0.9", address)
            with patch.object(runtime, "private_env", wraps=runtime.private_env):
                original = self.env.read_text()
                self.env.write_text(text)
                with self.assertRaises(ValueError):
                    runtime.prepare("ai-agent", "0.2.1", **self.inputs)
                self.env.write_text(original)
        self.dev.write_text("GITHUB_ACTUATOR_TOKEN=synthetic\nGITHUB_NACOS_DEV_NAMESPACE=company\n")
        with self.assertRaises(ValueError):
            runtime.prepare("rag", "1.1.6", **self.inputs)
        self.env.write_text("JAVA_TOOL_OPTIONS='-javaagent:untrusted.jar'\n")
        with self.assertRaises(ValueError):
            runtime.private_env(self.env, ("OTEL_", "FLEET_"))

    def test_private_inputs_and_outputs_reject_symlinks_or_open_permissions(self):
        self.env.chmod(0o644)
        with self.assertRaises(ValueError):
            runtime.runtime_inputs(self.env, self.dev)
        self.env.chmod(0o600)
        self.inputs["state_dir"].symlink_to(self.root)
        with self.assertRaises(ValueError):
            runtime.prepare("rag", "1.1.6", **self.inputs)

    def test_properties_escape_prevents_new_key_injection(self):
        text = runtime.properties({"key": " token\nsecret=value\\文😀"})
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("\\nsecret\\=value\\\\", text)
        self.assertIn("\\u6587\\ud83d\\ude00", text)

    def test_controlled_environment_and_application_arguments_cannot_override_files(self):
        source = {"OTEL_RESOURCE_ATTRIBUTES": "wrong", "MANAGEMENT_SERVER_PORT": "9999", "LOGGING_FILE_NAME": "wrong",
                  "GITHUB_ACTUATOR_TOKEN": "wrong", "SPRING_APPLICATION_VERSION": "wrong", "JAVA_TOOL_OPTIONS": "wrong",
                  "PATH": "keep", "BUSINESS_OPTION": "keep"}
        self.assertEqual(runtime.controlled_environment(source), {"PATH": "keep", "BUSINESS_OPTION": "keep"})
        for argument in ("--spring.config.additional-location=wrong", "--management-server-port=9999", "--logging.file.name=wrong",
                         "--security.actuator.token=wrong", "--SPRING_APPLICATION_VERSION=wrong", "--otel.service.name=wrong"):
            with self.assertRaises(ValueError):
                runtime.validate_application_args([argument])
        runtime.validate_application_args(["--server.port=9876", "--business.feature=true"])

    def create_idea_fixture(self):
        manifest = json.loads((ROOT / "repos.json").read_text())
        (self.root / "repos.json").write_text(json.dumps(manifest))
        directory = self.root / ".idea"
        directory.mkdir()
        configs = []
        for service, main in runtime.SERVICES.items():
            repo = self.root / service
            repo.mkdir()
            (repo / "build.gradle").write_text("version = '1.2.3'\n")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                            f"https://github.com/sunkailun1992/{service}.git"], check=True)
            configs.append(f'<configuration name="{service}" type="SpringBootApplicationConfigurationType">'
                           f'<option name="SPRING_BOOT_MAIN_CLASS" value="{main}" />'
                           f'<module name="{service}.main" /><method v="2"><option name="Make" enabled="true" /></method>'
                           '</configuration>')
        configs.append('<configuration name="custom" type="Application"><option name="MAIN_CLASS_NAME" value="custom.Keep" />'
                       '<option name="VM_PARAMETERS" value="-Xmx111m" /></configuration>')
        path = directory / "workspace.xml"
        path.write_text('<project><component name="RunManager">' + ''.join(configs) + '</component></project>')
        return path

    def test_idea_migration_is_narrow_secret_free_and_idempotent(self):
        path = self.create_idea_fixture()
        _, before, after, selected = idea.candidate(self.root, self.inputs)
        self.assertEqual(len(selected), 9)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.inputs["state_dir"].exists())
        self.assertNotIn(b"SYNTHETIC_", after)
        tree = workspace.parse_idea_xml_bytes(after)
        custom = tree.getroot().find("./component/configuration[@name='custom']")
        self.assertEqual(custom.find("option[@name='VM_PARAMETERS']").get("value"), "-Xmx111m")
        app = tree.getroot().find("./component/configuration[@name='gateway']")
        args = shlex.split(app.find("option[@name='VM_PARAMETERS']").get("value"))
        self.assertEqual(sum(a.startswith("-javaagent:") for a in args), 1)
        self.assertIsNone(app.find("./method/option[@name='Gradle.BeforeRunTask']"))
        self.assertEqual(app.find("./method/option[@name='ToolBeforeRunTask']").get("actionId"),
                         "Tool_KellenWorkspaceObservability_Prepare gateway")
        path.write_bytes(after)
        self.assertEqual(idea.candidate(self.root, self.inputs)[2], after)

    def test_idea_foreign_repository_custom_agent_and_xml_entity_are_preserved(self):
        path = self.create_idea_fixture()
        original = path.read_bytes()
        text = original.decode().replace('name="gateway" type=', 'name="gateway" type=')
        text = text.replace('<module name="gateway.main" />', '<module name="gateway.main" /><option name="VM_PARAMETERS" value="-javaagent:/custom.jar" />')
        path.write_text(text)
        with self.assertRaises(ValueError):
            idea.candidate(self.root, self.inputs)
        self.assertEqual(path.read_text(), text)
        path.write_bytes(original)
        subprocess.run(["git", "-C", str(self.root / "gateway"), "remote", "set-url", "origin", "https://example.com/company/gateway.git"], check=True)
        with self.assertRaises(Exception):
            idea.candidate(self.root, self.inputs)
        self.assertEqual(path.read_bytes(), original)
        path.write_text('<!DOCTYPE x [<!ENTITY s SYSTEM "file:///etc/passwd">]><project>&s;</project>')
        with self.assertRaises(Exception):
            idea.candidate(self.root, self.inputs)

    def test_native_idea_migration_removes_only_owned_gradle_hook_and_preserves_other_before_run(self):
        path = self.create_idea_fixture()
        tree = workspace.parse_idea_xml(path)
        method = tree.getroot().find("./component/configuration[@name='gateway']/method")
        import xml.etree.ElementTree as ET
        ET.SubElement(method, "option", {"name": "Gradle.BeforeRunTask", "enabled": "true",
                                        "tasks": "prepareObservedGateway", "externalProjectPath": "$PROJECT_DIR$"})
        ET.SubElement(method, "option", {"name": "ToolBeforeRunTask", "enabled": "true", "actionId": "Tool_User_Keep"})
        path.write_bytes(workspace.idea_xml_bytes(tree))
        _, _, after, _ = idea.candidate(self.root, self.inputs)
        parsed = workspace.parse_idea_xml_bytes(after)
        method = parsed.getroot().find("./component/configuration[@name='gateway']/method")
        self.assertIsNone(method.find("option[@name='Gradle.BeforeRunTask']"))
        self.assertIsNotNone(method.find("option[@name='Make']"))
        self.assertEqual({item.get("actionId") for item in method.findall("option[@name='ToolBeforeRunTask']")},
                         {"Tool_User_Keep", "Tool_KellenWorkspaceObservability_Prepare gateway"})
        path.write_text(path.read_text().replace("prepareObservedGateway", "userCustomTask"))
        preserved = path.read_bytes()
        with self.assertRaises(runtime.RuntimeValidationError) as result:
            idea.candidate(self.root, self.inputs)
        self.assertEqual(result.exception.reason_code, "IDE_PREFLIGHT_CONFLICT")
        self.assertEqual(path.read_bytes(), preserved)

    def test_idea_preflight_rejects_later_vm_or_environment_overrides(self):
        path = self.create_idea_fixture()
        _, _, after, _ = idea.candidate(self.root, self.inputs)
        path.write_bytes(after)
        receipt = runtime.prepare("gateway", "1.2.3", **self.inputs)
        with patch.dict(os.environ, {}, clear=True):
            runtime.check_idea_runtime("gateway", receipt, self.root)
            with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://wrong"}):
                with self.assertRaises(ValueError):
                    runtime.check_idea_runtime("gateway", receipt, self.root)
            tree = workspace.parse_idea_xml(path)
            option = tree.getroot().find("./component/configuration[@name='gateway']/option[@name='VM_PARAMETERS']")
            option.set("value", option.get("value") + " -Dlogging.file.name=wrong")
            path.write_bytes(workspace.idea_xml_bytes(tree))
            with self.assertRaises(ValueError):
                runtime.check_idea_runtime("gateway", receipt, self.root)

    def test_idea_environment_scope_uses_project_names_and_retains_identity_protection(self):
        self.dev.write_text(self.dev.read_text() + "GITHUB_CUSTOM_SOURCE_URL=SYNTHETIC_PROJECT_PRIVATE_VALUE\n")
        receipt = runtime.prepare("rag", "1.2.3", **self.inputs, write=False)
        self.assertIn("GITHUB_CUSTOM_SOURCE_URL", receipt["project_environment_names"])
        self.assertNotIn("SYNTHETIC_PROJECT_PRIVATE_VALUE", json.dumps(receipt))
        for name in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_ACTIONS", "GITHUB_WORKFLOW", "GITHUB_UNRELATED_FUTURE_METADATA"):
            self.assertFalse(runtime.ide_controlled_env_name(name, receipt), name)
        for name in ("GITHUB_CUSTOM_SOURCE_URL", "github.custom-source.url", "GITHUB_ACTUATOR_TOKEN",
                     "github.actuator.token", "GITHUB_NACOS_DEV_PASSWORD", "github.nacos.dev.server-addr",
                     "OTEL_EXPORTER_OTLP_ENDPOINT", "otel.exporter.otlp.headers", "MANAGEMENT_SERVER_PORT",
                     "LOGGING_FILE_NAME", "security.actuator.token", "SPRING_APPLICATION_VERSION",
                     "SPRING_CONFIG_IMPORT", "JAVA_TOOL_OPTIONS"):
            self.assertTrue(runtime.ide_controlled_env_name(name, receipt), name)
        # Jar/Gradle cleanup remains broad; IDE scope is deliberately narrower.
        self.assertTrue(runtime.controlled_env_name("GITHUB_PERSONAL_ACCESS_TOKEN"))

    def test_interactive_shell_metadata_replay_passes_but_project_and_empty_overrides_fail(self):
        if not Path('/bin/zsh').is_file():
            self.skipTest('interactive shell fixture requires zsh')
        path = self.create_idea_fixture()
        self.dev.write_text(self.dev.read_text() + "GITHUB_CUSTOM_SOURCE_URL=SYNTHETIC_PROJECT_PRIVATE_VALUE\n")
        _, _, after, _ = idea.candidate(self.root, self.inputs)
        path.write_bytes(after)
        receipt = runtime.prepare("gateway", "1.2.3", **self.inputs, write=False)
        shell = self.root / 'shell'
        shell.mkdir()
        (shell / '.zshrc').write_text('export GITHUB_PERSONAL_ACCESS_TOKEN=SYNTHETIC_TOOL_TOKEN\nexport GITHUB_ANOTHER_TOOL_METADATA=SYNTHETIC_TOOL_METADATA\n')
        environment = dict(runtime.controlled_environment(os.environ), ZDOTDIR=str(shell))
        result = subprocess.run(['/bin/zsh', '-ilc', '/usr/bin/env -0'], env=environment,
                                capture_output=True, check=True)
        captured = dict(item.split('=', 1) for item in result.stdout.decode().split('\0') if '=' in item)
        self.assertIn('GITHUB_PERSONAL_ACCESS_TOKEN', captured)
        self.assertEqual([key for key in captured if runtime.ide_controlled_env_name(key, receipt)], [])
        with patch.dict(os.environ, captured, clear=True):
            runtime.check_idea_runtime("gateway", receipt, self.root)
            for key in ('GITHUB_CUSTOM_SOURCE_URL', 'GITHUB_ACTUATOR_TOKEN', 'OTEL_EXPORTER_OTLP_ENDPOINT'):
                for value in ('', 'SYNTHETIC_OVERRIDE_VALUE'):
                    with patch.dict(os.environ, {key: value}):
                        with self.assertRaises(runtime.RuntimeValidationError) as failure:
                            runtime.check_idea_runtime("gateway", receipt, self.root)
                        self.assertEqual(failure.exception.reason_code, 'IDE_ENV_OVERRIDE')
                        self.assertNotIn('SYNTHETIC_', str(failure.exception))

    def test_generator_and_preflight_share_project_environment_scope_and_aliases(self):
        path = self.create_idea_fixture()
        self.dev.write_text(self.dev.read_text() + "GITHUB_CUSTOM_SOURCE_URL=SYNTHETIC_PROJECT_PRIVATE_VALUE\n")
        tree = workspace.parse_idea_xml(path)
        config = tree.getroot().find("./component/configuration[@name='gateway']")
        import xml.etree.ElementTree as ET
        envs = ET.SubElement(config, 'envs')
        ET.SubElement(envs, 'env', name='GITHUB_PERSONAL_ACCESS_TOKEN', value='SYNTHETIC_TOOL_TOKEN')
        path.write_bytes(workspace.idea_xml_bytes(tree))
        _, _, after, _ = idea.candidate(self.root, self.inputs)
        path.write_bytes(after)
        receipt = runtime.prepare('gateway', '1.2.3', **self.inputs, write=False)
        with patch.dict(os.environ, {}, clear=True):
            runtime.check_idea_runtime('gateway', receipt, self.root)
        tree = workspace.parse_idea_xml(path)
        config = tree.getroot().find("./component/configuration[@name='gateway']")
        ET.SubElement(config.find('envs'), 'env', name='github.custom-source.url', value='SYNTHETIC_PROJECT_OVERRIDE')
        path.write_bytes(workspace.idea_xml_bytes(tree))
        before = path.read_bytes()
        with self.assertRaises(runtime.RuntimeValidationError) as failure:
            idea.candidate(self.root, self.inputs)
        self.assertEqual(failure.exception.reason_code, 'IDE_ENV_OVERRIDE')
        self.assertEqual(path.read_bytes(), before)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(runtime.RuntimeValidationError) as failure:
                runtime.check_idea_runtime('gateway', receipt, self.root)
        self.assertEqual(failure.exception.reason_code, 'IDE_ENV_OVERRIDE')

    def test_idea_saved_macro_paths_validate_and_remain_idempotent_across_two_saves(self):
        path = self.create_idea_fixture()
        home = self.root / "测试 home"
        home.mkdir(mode=0o700)
        prior_agent, prior_privacy = self.agent, self.privacy
        self.agent, self.privacy = home / "agent.jar", home / "privacy extension.jar"
        prior_agent.rename(self.agent)
        prior_privacy.rename(self.privacy)
        self.env.write_text(self.env.read_text().replace(str(prior_agent), str(self.agent)).replace(str(prior_privacy), str(self.privacy)))
        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            _, _, after, _ = idea.candidate(self.root, self.inputs)
            path.write_bytes(after)
            receipts = {service: runtime.prepare(service, "1.2.3", **self.inputs) for service in runtime.SERVICES}
            for round_number in (1, 2):
                tree = workspace.parse_idea_xml(path)
                for config in tree.getroot().findall("./component/configuration"):
                    service = config.get("name")
                    if service not in runtime.SERVICES:
                        continue
                    # 实际 IDEA 形态：URI 中宏在 file:// 后，中文百分号与 ../ 同时存在。
                    back = "../" if round_number == 1 else "../../" + quote(self.root.parent.name, safe="") + "/"
                    app = f"file://$PROJECT_DIR$/{back}{quote(self.root.name, safe='')}/state/prepared/{service}/application.properties"
                    arguments = ["-javaagent:$USER_HOME$/agent.jar",
                                 "-Dotel.javaagent.extensions=$USER_HOME$/./privacy extension.jar",
                                 f"-Dotel.javaagent.configuration-file=$PROJECT_DIR$/./state/prepared/{service}/../{service}/agent.properties",
                                 "-Dspring.config.additional-location=" + app]
                    config.find("option[@name='VM_PARAMETERS']").set("value", " ".join('"' + item + '"' for item in arguments))
                saved = workspace.idea_xml_bytes(tree)
                path.write_bytes(saved)
                for service, receipt in receipts.items():
                    runtime.check_idea_runtime(service, receipt, self.root)
                self.assertEqual(idea.candidate(self.root, self.inputs)[2], saved)
                self.assertNotIn(b"SYNTHETIC_", saved)

    def test_idea_controlled_references_reject_unknown_macros_and_nonlocal_or_invalid_uris(self):
        receipt = runtime.prepare("rag", "1.2.3", **self.inputs)
        for argument, code in (
            ("-javaagent:$UNKNOWN$/agent.jar", "IDE_PATH_MACRO_UNSUPPORTED"),
            ("-javaagent:$PROJECT_DIR", "IDE_PATH_MACRO_UNSUPPORTED"),
            ("-Dotel.javaagent.configuration-file=relative.properties", "IDE_PATH_INVALID"),
            ("-Dspring.config.additional-location=https://example.invalid/app.properties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file://remote.invalid/app.properties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app.properties?SYNTHETIC_SECRET", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app.properties#", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/%ZZ", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/%FF", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/%00", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app.proper\tties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app.proper%09ties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app%1fties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/app%7Fties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location= file:///tmp/app.properties", "IDE_FILE_URI_INVALID"),
            ("-Dspring.config.additional-location=file:///tmp/%24UNKNOWN%24", "IDE_PATH_MACRO_UNSUPPORTED"),
            ("-Dotel.javaagent.configuration-file=$PROJECT_DIR$/other.properties", "IDE_REFERENCE_MISMATCH"),
        ):
            with self.subTest(code=code, kind=argument.split("=", 1)[0]):
                with self.assertRaises(runtime.RuntimeValidationError) as result:
                    runtime.idea_reference(argument, receipt, self.root)
                self.assertEqual(result.exception.reason_code, code)
                self.assertNotIn("SYNTHETIC_SECRET", str(result.exception))

    def test_idea_file_identity_cannot_hide_agent_options_or_multiple_locations(self):
        receipt = {"agent": "/tmp/controlled/agent.jar", "privacy": "/tmp/controlled/privacy.jar",
                   "agent_config": "/tmp/controlled/agent.properties", "app_config": "/tmp/controlled/application.properties"}
        for argument in (
            "-javaagent:/tmp/ROGUE.jar=/../../tmp/controlled/agent.jar",
            "-Dotel.javaagent.extensions=/tmp/ROGUE.jar,/../../tmp/controlled/privacy.jar",
            "-Dspring.config.additional-location=file:///tmp/OTHER.properties,file:///x/../../../../tmp/controlled/application.properties",
            "-Dspring.config.additional-location=file:///tmp/OTHER.properties;file:///x/../../../../tmp/controlled/application.properties",
            "-Dspring.config.additional-location=file:///tmp/OTHER.properties%2Cfile:///x/../../../../tmp/controlled/application.properties",
        ):
            with self.assertRaises(runtime.RuntimeValidationError) as result:
                runtime.idea_reference(argument, receipt, self.root)
            self.assertEqual(result.exception.reason_code, "IDE_REFERENCE_SYNTAX_INVALID")

    def test_idea_duplicate_agent_is_rejected_even_when_two_spellings_target_same_jar(self):
        path = self.create_idea_fixture()
        _, _, after, _ = idea.candidate(self.root, self.inputs)
        path.write_bytes(after)
        tree = workspace.parse_idea_xml(path)
        option = tree.getroot().find("./component/configuration[@name='gateway']/option[@name='VM_PARAMETERS']")
        option.set("value", option.get("value") + ' "-javaagent:$PROJECT_DIR$/agent.jar"')
        path.write_bytes(workspace.idea_xml_bytes(tree))
        before = path.read_bytes()
        receipt = runtime.prepare("gateway", "1.2.3", **self.inputs)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(runtime.RuntimeValidationError) as result:
                runtime.check_idea_runtime("gateway", receipt, self.root)
            self.assertEqual(result.exception.reason_code, "IDE_REFERENCE_DUPLICATE")
        with self.assertRaises(runtime.RuntimeValidationError) as result:
            idea.candidate(self.root, self.inputs)
        self.assertEqual(result.exception.reason_code, "IDE_REFERENCE_DUPLICATE")
        self.assertEqual(path.read_bytes(), before)

    def test_prepare_diagnostics_distinguish_idea_reference_and_binding_failures_without_values(self):
        spec = importlib.util.spec_from_file_location("prepare_observed", ROOT / "scripts/prepare-observed.py")
        command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(command)
        for error, code in ((runtime.RuntimeValidationError("IDE_REFERENCE_MISMATCH"), "IDE_REFERENCE_MISMATCH"),
                            (runtime.RuntimeValidationError("LAUNCH_BINDING_MISSING"), "LAUNCH_BINDING_MISSING"),
                            (ValueError("SYNTHETIC_SECRET"), "RUNTIME_PREFLIGHT_FAILED")):
            output = io.StringIO()
            with patch.object(sys, "argv", ["prepare-observed.py", "prepare", "rag", "--check-idea"]), \
                    patch.object(command, "load_binding", side_effect=error), contextlib.redirect_stdout(output):
                self.assertEqual(command.main(), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result["reason_code"], code)
            self.assertNotIn("SYNTHETIC_SECRET", output.getvalue())
            if code.startswith("IDE_"):
                self.assertNotIn("binding", result["hint"])
        with self.assertRaises(runtime.RuntimeValidationError) as missing:
            runtime.load_binding(self.root / "no-binding")
        self.assertEqual(missing.exception.reason_code, "LAUNCH_BINDING_MISSING")

    def test_native_quiet_preflight_is_silent_on_success_and_reports_safe_failure_on_stderr(self):
        spec = importlib.util.spec_from_file_location("quiet_prepare", ROOT / "scripts/prepare-observed.py")
        command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(command)
        for failure in (None, runtime.RuntimeValidationError("IDE_REFERENCE_MISSING")):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(sys, "argv", ["prepare-observed.py", "prepare", "rag", "--check-idea", "--quiet"]), \
                    patch.object(command, "load_binding", return_value=self.inputs), \
                    patch.object(command, "checkout_version", return_value="1.2.3"), \
                    patch.object(command, "prepare", return_value={"service": "rag", "private": "SYNTHETIC_SECRET"}), \
                    patch.object(command, "check_idea_runtime", side_effect=failure), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = command.main()
            self.assertEqual(stdout.getvalue(), "")
            if failure is None:
                self.assertEqual(status, 0)
                self.assertEqual(stderr.getvalue(), "")
            else:
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(stderr.getvalue())["reason_code"], "IDE_REFERENCE_MISSING")
                self.assertNotIn("SYNTHETIC_SECRET", stderr.getvalue())

    def test_git_trackable_private_output_is_rejected_before_writing(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        with self.assertRaises(ValueError):
            runtime.prepare("rag", "1.1.6", **self.inputs)
        self.assertFalse(self.inputs["state_dir"].exists())
        (self.root / ".gitignore").write_text("/state/\n/build/\n")
        result = runtime.prepare("rag", "1.1.6", **self.inputs)
        self.assertTrue(Path(result["agent_config"]).is_file())


if __name__ == "__main__":
    unittest.main()
