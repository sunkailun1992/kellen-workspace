"""托管启动边界：不停止同名外部 JVM，不执行私有 env 中的 shell 内容。"""
import importlib.util
import sys
import hashlib
import io
import os
import stat
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("managed", ROOT / "scripts/manage-observed.py")
managed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(managed)


class ManagedObservabilityTest(unittest.TestCase):
    @staticmethod
    def write_bootstrap(repo, extension):
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        relative = f"config/application-dev.{extension}"
        path = repo / relative
        path.parent.mkdir(exist_ok=True)
        (repo / ".git/info/exclude").write_text("/" + relative + "\n")
        path.write_text("# synthetic bootstrap\n")
        path.chmod(0o600)
        return path

    def test_private_bootstrap_accepts_yaml_and_properties(self):
        for extension in ("properties", "yml", "yaml"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = root / "service"
                self.write_bootstrap(repo, extension)
                link = root / "workspace-service"
                link.symlink_to(repo, target_is_directory=True)
                managed.validate_dev_bootstrap(link)

    def test_private_bootstrap_rejects_visible_or_tracked_yaml(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            bootstrap = self.write_bootstrap(repo, "yml")
            (repo / ".git/info/exclude").write_text("")
            with self.assertRaisesRegex(ValueError, "ignored and untracked"):
                managed.validate_dev_bootstrap(repo)
            subprocess.run(["git", "-C", str(repo), "add", str(bootstrap)], check=True)
            (repo / ".git/info/exclude").write_text("/config/application-dev.yml\n")
            with self.assertRaisesRegex(ValueError, "ignored and untracked"):
                managed.validate_dev_bootstrap(repo)

    def test_private_bootstrap_rejects_insecure_yaml_alongside_valid_properties(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            properties = self.write_bootstrap(repo, "properties")
            yaml = properties.with_suffix(".yml")
            (repo / ".git/info/exclude").write_text("/config/application-dev.*\n")
            yaml.write_text("# synthetic bootstrap\n")
            yaml.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "owner-only regular file"):
                managed.validate_dev_bootstrap(repo)
            yaml.unlink()
            yaml.symlink_to(properties)
            with self.assertRaisesRegex(ValueError, "owner-only regular file"):
                managed.validate_dev_bootstrap(repo)
            yaml.unlink()
            os.link(properties, yaml)
            with self.assertRaisesRegex(ValueError, "owner-only regular file"):
                managed.validate_dev_bootstrap(repo)

    def test_collector_uses_owner_only_persistent_offsets_and_ignores_env_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "collector"
            binary.write_bytes(b"synthetic pinned fixture")
            state = root / "state"
            state.mkdir(mode=0o700)
            (state / "logs").mkdir(mode=0o700)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            environment = {"FLEET_COLLECTOR_PATH": str(binary), "FLEET_COLLECTOR_SHA256": digest,
                           "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:14318",
                           "FLEET_LOG_STATE_DIR": str(root / "untrusted-override")}
            child = SimpleNamespace(pid=456, poll=lambda: None)
            with patch.object(managed, "ROOT", root), patch.object(managed, "PINNED_COLLECTOR_SHA256", digest), \
                    patch.object(managed, "listener", return_value=[]), patch.object(managed.time, "sleep"), \
                    patch.object(managed.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(managed, "process_command", return_value=""), patch("sys.stdout", io.StringIO()), \
                    patch.object(managed.subprocess, "Popen", return_value=child) as launch:
                managed.collector_start(environment, state)
                offsets = state / "collector-offsets"
                self.assertTrue(offsets.is_dir())
                self.assertEqual(stat.S_IMODE(offsets.stat().st_mode), 0o700)
                self.assertEqual(launch.call_args.kwargs["env"]["FLEET_LOG_STATE_DIR"], str(offsets))
                marker = offsets / "receiver_filelog_runtime"
                marker.write_bytes(b"synthetic persisted offset")
                marker.chmod(0o600)
                managed.collector_start(environment, state)
                self.assertEqual(launch.call_count, 2)
                self.assertEqual(marker.read_bytes(), b"synthetic persisted offset")

    def test_collector_offset_state_rejects_trackable_output_and_foreign_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            state = root / "trackable-state"
            state.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                managed.collector_offsets(state)
            self.assertFalse((state / "collector-offsets").exists())
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(managed.os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaises(ValueError):
                    managed.collector_offsets(Path(temporary))

    def test_collector_rejects_shared_or_symlink_offsets_without_changing_them(self):
        for kind in ("directory", "symlink", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = root / "state"
                state.mkdir(mode=0o700)
                offsets = state / "collector-offsets"
                if kind == "symlink":
                    target = root / "outside"
                    target.mkdir(mode=0o700)
                    offsets.symlink_to(target)
                else:
                    offsets.mkdir(mode=0o700)
                    if kind == "directory":
                        offsets.chmod(0o755)
                    else:
                        marker = offsets / "existing"
                        marker.write_bytes(b"must remain unchanged")
                        marker.chmod(0o644)
                binary = root / "collector"
                binary.write_bytes(b"synthetic pinned fixture")
                digest = hashlib.sha256(binary.read_bytes()).hexdigest()
                environment = {"FLEET_COLLECTOR_PATH": str(binary), "FLEET_COLLECTOR_SHA256": digest,
                               "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:14318"}
                with patch.object(managed, "PINNED_COLLECTOR_SHA256", digest), \
                        patch.object(managed, "listener", return_value=[]), patch.object(managed.time, "sleep"), \
                        patch.object(managed.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                        patch.object(managed.subprocess, "Popen", return_value=SimpleNamespace(pid=456, poll=lambda: None)) as launch:
                    with self.assertRaises(ValueError):
                        managed.collector_start(environment, state)
                    launch.assert_not_called()
                if kind == "directory": self.assertEqual(stat.S_IMODE(offsets.stat().st_mode), 0o755)
                if kind == "symlink": self.assertTrue(offsets.is_symlink())
                if kind == "file": self.assertEqual(marker.read_bytes(), b"must remain unchanged")

    def test_full_preflight_failure_preserves_existing_jvm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_bootstrap(root / "ai-agent", "yml")
            args = SimpleNamespace(service="ai-agent", replace_pid=123, timeout=1)
            environment = {"GITHUB_ACTUATOR_TOKEN": "synthetic", "FLEET_MANAGEMENT_BIND_ADDRESS": "10.0.0.9"}
            with patch.object(managed, "ROOT", root), patch.object(managed, "listener", return_value=[]), \
                    patch.object(managed, "launch_preflight", side_effect=ValueError("rejected full runtime")), \
                    patch.object(managed, "stop_owned") as stop:
                with self.assertRaisesRegex(ValueError, "rejected full runtime"):
                    managed.service_start(args, environment, root / "state")
                stop.assert_not_called()

    def test_foreign_owner_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            path.write_text("FLEET_TEST=synthetic\n")
            path.chmod(0o600)
            original = path.stat()
            foreign = SimpleNamespace(st_mode=original.st_mode, st_uid=os.geteuid() + 1)
            with patch.object(Path, "stat", return_value=foreign):
                with self.assertRaises(ValueError):
                    managed.private_env(path, ("FLEET_",))

    def test_foreign_management_listener_preserves_old_jvm_before_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            args = SimpleNamespace(service="ai-agent", replace_pid=101, timeout=1)
            environment = {"GITHUB_ACTUATOR_TOKEN": "synthetic", "FLEET_MANAGEMENT_BIND_ADDRESS": "10.0.0.9"}
            with patch.object(managed, "ROOT", root), \
                    patch.object(managed, "validate_dev_bootstrap"), \
                    patch.object(managed, "listener", side_effect=lambda port: [999] if port == 17310 else [101]), \
                    patch.object(managed, "stop_owned") as stop, \
                    patch.object(managed, "http_status") as http, \
                    patch.object(managed.subprocess, "Popen", side_effect=AssertionError("must not launch")):
                with self.assertRaisesRegex(ValueError, "management port belongs"):
                    managed.service_start(args, environment, state)
                stop.assert_not_called()
                http.assert_not_called()

    def test_http_success_from_foreign_management_pid_cannot_verify_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            (state / "logs").mkdir(parents=True)
            (state / "logs/ai-agent.json").write_text("{}\n")
            args = SimpleNamespace(service="ai-agent", replace_pid=100, timeout=1)
            environment = {"GITHUB_ACTUATOR_TOKEN": "synthetic", "FLEET_MANAGEMENT_BIND_ADDRESS": "10.0.0.9"}
            management_checks = iter([[], [999]])
            def listeners(port):
                return next(management_checks, [999]) if port == 17310 else [101]
            child = SimpleNamespace(pid=456, poll=lambda: None)
            # Foreign management process would answer every HTTP contract correctly if queried.
            def http_success(url, token=None):
                return 200 if url.endswith("/health") or token == "synthetic" else 401
            with patch.object(managed, "ROOT", root), \
                    patch.object(managed, "validate_dev_bootstrap"), \
                    patch.object(managed.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(managed.subprocess, "Popen", return_value=child), \
                    patch.object(managed, "launch_preflight"), \
                    patch.object(managed, "stop_owned"), \
                    patch.object(managed, "owned_service", return_value=True), \
                    patch.object(managed, "listener", side_effect=listeners), \
                    patch.object(managed, "http_status", side_effect=http_success) as http, \
                    patch.object(managed.time, "monotonic", side_effect=[0, 0, 2]), \
                    patch.object(managed.time, "sleep"):
                with self.assertRaises(TimeoutError):
                    managed.service_start(args, environment, state)
                http.assert_not_called()
            self.assertNotIn('"status": "verified"', (state / "ai-agent.json").read_text())

    def test_private_digest_cannot_authorize_unpinned_collector(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "collector"
            binary.write_bytes(b"not the official pinned collector")
            environment = {"FLEET_COLLECTOR_PATH": str(binary), "FLEET_COLLECTOR_SHA256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch.object(managed.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    managed.collector_start(environment, Path(temporary))
                run.assert_not_called()

    def test_missing_explicit_management_bind_preserves_old_jvm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_bootstrap(root / "ai-agent", "properties")
            args = SimpleNamespace(service="ai-agent", replace_pid=123, timeout=1)
            with patch.object(managed, "ROOT", root), patch.object(managed, "stop_owned") as stop:
                with self.assertRaises(ValueError):
                    managed.service_start(args, {"GITHUB_ACTUATOR_TOKEN": "synthetic"}, root / "state")
                stop.assert_not_called()

    def test_same_main_class_outside_personal_checkout_is_never_stopped(self):
        with patch.object(managed, "process_command", return_value="java -cp /unrelated/user/build/classes com.kellen.UserApplication"), patch.object(managed.os, "kill") as kill:
            with self.assertRaises(ValueError):
                managed.stop_owned(123, "user")
            kill.assert_not_called()

    def test_personal_classpath_without_listening_port_is_not_enough_to_stop(self):
        command = f"java -cp {(ROOT / 'user').resolve()}/build/classes/java/main com.kellen.UserApplication"
        with patch.object(managed, "process_command", return_value=command), patch.object(managed, "listener", return_value=[456]), patch.object(managed.os, "kill") as kill:
            with self.assertRaises(ValueError):
                managed.stop_owned(123, "user")
            kill.assert_not_called()

    def test_private_environment_requires_restricted_file_and_key_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            path.write_text("OTEL_TEST='$(touch forbidden)'\n")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                managed.private_env(path, ("OTEL_",))
            path.chmod(0o600)
            self.assertEqual(managed.private_env(path, ("OTEL_",))["OTEL_TEST"], "$(touch forbidden)")
            self.assertFalse((Path(temporary) / "forbidden").exists())
            path.write_text("JAVA_TOOL_OPTIONS='-javaagent:untrusted.jar'\n")
            with self.assertRaises(ValueError):
                managed.private_env(path, ("OTEL_", "FLEET_"))

    def test_symlink_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "real.env"
            target.write_text("FLEET_TEST=ok\n")
            target.chmod(0o600)
            link = Path(temporary) / "linked.env"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                managed.private_env(link, ("FLEET_",))


if __name__ == "__main__":
    unittest.main()
