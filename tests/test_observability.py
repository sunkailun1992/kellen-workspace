"""实际执行观测启动器，确认身份校验、摘要检查和秘密不回显。"""
import hashlib
import os
from pathlib import Path
import subprocess
import json
import shlex
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ObservedLauncherTest(unittest.TestCase):
    def run_launcher(self, service="rag", digest=None, extension=True, extension_digest=None, agent_version="2.33.1", extension_version="0.1.1", binding=True):
        with tempfile.TemporaryDirectory(prefix="observability 空格 ") as temp:
            agent = Path(temp) / "agent.jar"
            with zipfile.ZipFile(agent, 'w') as jar:
                jar.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nImplementation-Version: ' + agent_version + '\n')
            privacy = Path(temp) / "privacy extension.jar"
            with zipfile.ZipFile(privacy, 'w') as jar:
                jar.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nImplementation-Version: ' + extension_version + '\nFleet-Agent-Version: 2.33.1\n')
            values = dict(OTEL_JAVAAGENT_PATH=str(agent),
                          OTEL_JAVAAGENT_SHA256=digest or hashlib.sha256(agent.read_bytes()).hexdigest(),
                          OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:14318",
                          OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer%20SYNTHETIC_PRIVATE_TOKEN")
            if extension:
                values.update(FLEET_PRIVACY_EXTENSION_PATH=str(privacy),
                              FLEET_PRIVACY_EXTENSION_SHA256=extension_digest or hashlib.sha256(privacy.read_bytes()).hexdigest())
            runtime_env = Path(temp) / "runtime.env"
            runtime_env.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
            runtime_env.chmod(0o600)
            dev_env = Path(temp) / "dev.env"
            dev_env.write_text("GITHUB_ACTUATOR_TOKEN=SYNTHETIC_ACTUATOR\n")
            dev_env.chmod(0o600)
            # 只将官方制品信任摘要替换为本临时合成 Jar 摘要；其余启动器源码原样执行。
            fixture_root = Path(temp) / "launcher"
            (fixture_root / "scripts").mkdir(parents=True)
            source = (ROOT / "scripts/run-observed.py").read_text().replace(
                'e43a270df53c6f423e0ab157be9c27ccc' + '01d5dc3232e162fe54777573db2f149',
                hashlib.sha256(agent.read_bytes()).hexdigest())
            (fixture_root / "scripts/run-observed.py").write_text(source)
            helper = (ROOT / "scripts/observability_runtime.py").read_text().replace(
                'e43a270df53c6f423e0ab157be9c27ccc' + '01d5dc3232e162fe54777573db2f149',
                hashlib.sha256(agent.read_bytes()).hexdigest())
            (fixture_root / "scripts/observability_runtime.py").write_text(helper)
            (fixture_root / "repos.json").write_text((ROOT / "repos.json").read_text())
            (fixture_root / "rag").mkdir()
            (fixture_root / "rag/build.gradle").write_text("version = '1.1.6'\n")
            state = fixture_root / "build/observability-runtime"
            state.mkdir(parents=True, mode=0o700)
            if binding:
                binding_file = state / "launch.json"
                binding_file.write_text(json.dumps({"runtime_env": str(runtime_env), "dev_env": str(dev_env), "state_dir": str(state)}))
                binding_file.chmod(0o600)
            return subprocess.run(["python3", str(fixture_root / "scripts/run-observed.py"), service, "--dry-run"],
                                  cwd=ROOT, env=os.environ, text=True, capture_output=True)

    def test_valid_service_uses_runtime_init_script_without_secret_output(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(":rag:bootRun", result.stdout)
        self.assertIn("observability.init.gradle", result.stdout)
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN", result.stdout + result.stderr)

    def test_public_library_cannot_be_started_as_service(self):
        result = self.run_launcher("utils")
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_agent_digest_fails_before_gradle(self):
        result = self.run_launcher(digest="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN", result.stdout + result.stderr)

    def test_missing_privacy_extension_fails_closed(self):
        self.assertNotEqual(self.run_launcher(extension=False).returncode, 0)

    def test_environment_jars_without_full_launch_binding_fail_closed(self):
        self.assertNotEqual(self.run_launcher(binding=False).returncode, 0)

    def test_wrong_privacy_digest_fails_closed(self):
        self.assertNotEqual(self.run_launcher(extension_digest='0' * 64).returncode, 0)

    def test_unpinned_agent_or_extension_version_fails_closed(self):
        self.assertNotEqual(self.run_launcher(agent_version='2.32.0').returncode, 0)
        self.assertNotEqual(self.run_launcher(extension_version='0.0.9').returncode, 0)


if __name__ == "__main__":
    unittest.main()
