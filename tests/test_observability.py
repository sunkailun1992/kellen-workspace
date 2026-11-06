"""实际执行观测启动器，确认身份校验、摘要检查和秘密不回显。"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ObservedLauncherTest(unittest.TestCase):
    def run_launcher(self, service="rag", digest=None):
        with tempfile.TemporaryDirectory(prefix="observability 空格 ") as temp:
            agent = Path(temp) / "agent.jar"
            agent.write_bytes(b"fixture agent")
            env = dict(os.environ, OTEL_JAVAAGENT_PATH=str(agent),
                       OTEL_JAVAAGENT_SHA256=digest or hashlib.sha256(agent.read_bytes()).hexdigest(),
                       OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:14318",
                       OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer%20SYNTHETIC_PRIVATE_TOKEN")
            return subprocess.run(["python3", str(ROOT / "scripts/run-observed.py"), service, "--dry-run"],
                                  cwd=ROOT, env=env, text=True, capture_output=True)

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


if __name__ == "__main__":
    unittest.main()
