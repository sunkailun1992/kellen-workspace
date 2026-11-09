"""三种启动入口共用的校验、秘密文件和 IDEA 精确迁移边界。"""
import hashlib
import importlib.util
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
                               (self.privacy, "Implementation-Version: 0.1.0\nFleet-Agent-Version: 2.33.1\n")):
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
        self.assertTrue(app.find("./method/option[@name='Gradle.BeforeRunTask']") is not None)
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
