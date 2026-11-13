"""默认使用临时合成数据；显式 NAS 测试变量只启用专用隔离库回归。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/database/migrate.py"


class MigrationLauncherTest(unittest.TestCase):
    def test_launcher_exists(self):
        self.assertTrue(LAUNCHER.is_file())

    def test_rejects_private_config_permissions_without_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "migration.json"
            path.write_text('{"secret":"SYNTHETIC_PASSWORD_MUST_NOT_ESCAPE"}')
            path.chmod(0o644)
            result = subprocess.run(["python3", str(LAUNCHER), "--config", str(path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SYNTHETIC_PASSWORD", result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["reason"], "PRIVATE_FILE_PERMISSION")

    def test_synthetic_java_contract(self):
        spec = importlib.util.spec_from_file_location("database_migration_launcher", LAUNCHER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            cp, java, javac = module.build_runtime()
        except module.Rejected as error:
            if str(error) in {"JAVA21_UNAVAILABLE", "JAVA21_REQUIRED", "PINNED_DEPENDENCY_UNAVAILABLE"}:
                self.skipTest(str(error))
            raise
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([str(javac), "--release", "21", "-cp", cp, "-d", temp,
                                     str(ROOT / "tests/database/MigrationContractTest.java")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(java), "-cp", temp + os.pathsep + cp, "MigrationContractTest"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = json.loads(result.stdout)
            self.assertEqual(first["failures"], 0)
            self.assertGreaterEqual(first["tests"], 30)
            result = subprocess.run([str(java), "-cp", temp + os.pathsep + cp, "MigrationContractTest"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(first["canonical_probe"], json.loads(result.stdout)["canonical_probe"])

    @unittest.skipUnless(os.environ.get("FLEET_PG_TEST_URL"), "NAS isolated PostgreSQL fixture requires explicit private environment")
    def test_nas_postgresql_roundtrip(self):
        spec = importlib.util.spec_from_file_location("database_migration_launcher", LAUNCHER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cp, java, javac = module.build_runtime()
        with tempfile.TemporaryDirectory() as temp:
            compiled = subprocess.run([str(javac), "--release", "21", "-cp", cp, "-d", temp,
                                       str(ROOT / "tests/database/MigrationPostgresqlTest.java")], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(java), "-cp", temp + os.pathsep + cp, "MigrationPostgresqlTest"],
                                    capture_output=True, text=True, env=module.java_environment())
            # 真实 JDBC 失败只显示工具固定 JSON，绝不透传驱动原文。
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, report)
            self.assertEqual(report["status"], "verified")
            self.assertEqual(report["tests"], 6)
            self.assertTrue(report["schema_cleaned"])


if __name__ == "__main__":
    unittest.main()
