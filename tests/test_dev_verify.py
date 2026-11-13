"""Exercise the local verification CLI with only temporary repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class DevVerifyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="dev-verify fixture ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace"
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True)
        for name in ("workspace.py", "dev-verify.py"):
            shutil.copy2(SOURCE / "scripts" / name, scripts)
        shutil.copy2(SOURCE / "repos.json", self.root / "repos.json")
        (self.root / ".gitignore").write_text("/build/\n/.workspace-local/\n")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(self.base / "gitconfig"),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.commit_empty(self.root)
        self.git(self.root, "add", ".gitignore")
        self.git(self.root, "commit", "-qm", "Ignore local artifacts")
        self.reference = self.base / "outside"
        self.reference.mkdir()
        self.commit_empty(self.reference)
        origin = json.loads((self.root / "repos.json").read_text())["repositories"][0]["url"]
        prefix = origin.rsplit("/", 2)[0]
        self.origin = prefix + "/different-owner/reference.git"
        self.git(self.reference, "remote", "add", "origin", self.origin)
        self.map = self.base / "mapping.json"
        self.map.write_text(json.dumps({"schemaVersion": 1, "repositories": {
            "reference": {"path": str(self.reference), "origin": self.origin}
        }}))
        self.scenario = self.base / "scenario.json"
        self.write_scenario(self.root / "dev-scenarios.json", "fixture")

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], env=self.env, text=True, stderr=subprocess.PIPE,
        ).strip()

    def commit_empty(self, root):
        self.git(root, "init", "-q")
        self.git(root, "commit", "--allow-empty", "-qm", "Fixture")

    def write_scenario(self, path, name, repo="workspace"):
        path.write_text(json.dumps({"schemaVersion": 1, "scenarios": {name: {
            "description": "Synthetic verification",
            "references": ["reference"],
            "checks": [{"name": "check", "repo": repo, "argv": [sys.executable, "-c", "print('verified')"],
                        "timeoutSeconds": 5}]
        }}}))

    def cli(self, *args, exit_code=0):
        process = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "dev-verify.py"), *args],
            cwd=self.root, env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(process.returncode, exit_code, (process.stdout, process.stderr))
        return json.loads(process.stdout)

    def test_local_scenario_records_reference_and_private_receipt(self):
        self.write_scenario(self.scenario, "new-task")
        result = self.cli("--repo-map", str(self.map), "--local-scenarios", str(self.scenario),
                          "run", "new-task")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["repositories"]["reference"]["access"], "reference")
        receipt = Path(result["receiptPath"])
        self.assertEqual(json.loads(receipt.read_text()), result)
        self.assertTrue(receipt.is_relative_to(self.root / "build" / "dev-verify"))

    def test_reference_does_not_execute_and_unsafe_output_is_rejected(self):
        self.write_scenario(self.scenario, "new-task", "reference")
        result = self.cli("--repo-map", str(self.map), "--local-scenarios", str(self.scenario),
                          "run", "new-task", exit_code=1)
        self.assertIn("read-only", result["reason"])
        result = self.cli("run", "fixture", "--output-dir", "tracked", exit_code=1)
        self.assertIn("ignored and untracked", result["reason"])
        self.assertFalse((self.root / "tracked").exists())

    def test_read_only_inspection_does_not_invoke_git_status_filter(self):
        self.git(self.reference, "config", "filter.fixture.clean", "false")
        result = self.cli("--repo-map", str(self.map), "inspect", "reference")
        self.assertEqual(result["status"], "inspected")
        self.assertIsNone(result["dirty"])


if __name__ == "__main__":
    unittest.main()
