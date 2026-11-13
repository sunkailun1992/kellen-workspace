"""独立 Java21 进程核对 Jar 入口的配置传递；不替代正式 OTel/Boot 端到端验收。"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

import test_observability_runtime as runtime_tests

ROOT = runtime_tests.ROOT
spec = importlib.util.spec_from_file_location("observed_jar", ROOT / "scripts/run-observed-jar.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ImmutableJarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="immutable jar ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "application.jar"
        with zipfile.ZipFile(self.source, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Start-Class: com.kellen.RagApplication\nImplementation-Version: 1.2.3\n")
            jar.writestr("fixture.txt", "SYNTHETIC_ARTIFACT_CONTENT")
        self.original = self.source.read_bytes()
        self.digest = hashlib.sha256(self.original).hexdigest()
        self.state = self.root / "state"
        self.target = self.state / "immutable-jars" / f"rag-1.2.3-{self.digest}.jar"

    def freeze(self, **kwargs):
        return launcher.freeze_jar(self.source, "rag", "1.2.3", self.state, **kwargs)

    def test_snapshot_has_independent_bytes_and_identical_content_is_reused_without_overwrite(self):
        target, digest = self.freeze()
        self.assertEqual((target, digest), (self.target, self.digest))
        self.assertEqual(target.read_bytes(), self.original)
        self.assertEqual(target.stat().st_mode & 0o777, 0o400)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(target.stat().st_uid, os.geteuid())
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertNotEqual(target.stat().st_ino, self.source.stat().st_ino)
        before = target.stat()
        self.freeze()
        self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.source.unlink()
        self.assertEqual(target.read_bytes(), self.original)

    def test_dry_run_checks_the_content_but_creates_no_state_directory(self):
        self.assertEqual(self.freeze(write=False), (self.target, self.digest))
        self.assertFalse(self.state.exists())

    def test_existing_bad_digest_permissions_symlink_or_hardlink_are_never_overwritten(self):
        self.state.mkdir(mode=0o700)
        self.target.parent.mkdir(mode=0o700)
        for shape in ("digest", "permissions", "symlink", "hardlink"):
            with self.subTest(shape=shape):
                if shape == "symlink":
                    self.target.symlink_to(self.source)
                elif shape == "hardlink":
                    os.link(self.source, self.target)
                    self.target.chmod(0o400)
                else:
                    self.target.write_bytes(b"ALTERED" if shape == "digest" else self.original)
                    self.target.chmod(0o400 if shape == "digest" else 0o644)
                before = self.target.lstat()
                with self.assertRaises(ValueError):
                    self.freeze()
                self.assertEqual(self.target.lstat().st_ino, before.st_ino)
                self.target.unlink()
                self.source.chmod(0o600)

    def test_source_link_unsafe_mode_or_wrong_version_are_rejected(self):
        alias = self.root / "alias.jar"
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            launcher.freeze_jar(alias, "rag", "1.2.3", self.state)
        alias.unlink()
        os.link(self.source, alias)
        with self.assertRaises(ValueError):
            self.freeze()
        alias.unlink()
        self.source.chmod(0o666)
        with self.assertRaises(ValueError):
            self.freeze()
        self.source.chmod(0o600)
        with self.assertRaises(ValueError):
            launcher.freeze_jar(self.source, "rag", "1.2.4", self.state)
        self.assertFalse(self.state.exists())

    def test_source_drift_during_copy_rejects_and_removes_only_its_incomplete_copy(self):
        original = launcher.copy_and_hash
        def mutate_source(stream, output=None):
            digest = original(stream, output)
            if output is not None:
                self.source.write_bytes(self.original + b"REBUILT_DURING_COPY")
            return digest
        with patch.object(launcher, "copy_and_hash", side_effect=mutate_source):
            with self.assertRaises(ValueError):
                self.freeze()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_destination_readback_rejects_extra_bytes_even_when_manifest_and_source_digest_match(self):
        original = launcher.copy_and_hash
        def corrupt_destination(stream, output=None):
            digest = original(stream, output)
            if output is not None:
                output.write(b"EXTRA_BYTES_WITH_READABLE_ZIP_MANIFEST")
            return digest
        with patch.object(launcher, "copy_and_hash", side_effect=corrupt_destination):
            with self.assertRaises(ValueError):
                self.freeze()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_unknown_target_race_preserves_the_other_file_and_does_not_replace_it(self):
        original = launcher.os.open
        def race(path, flags, *args, **kwargs):
            if Path(path) == self.target and flags & os.O_EXCL:
                self.target.write_bytes(b"OTHER_WRITER")
                self.target.chmod(0o400)
            return original(path, flags, *args, **kwargs)
        with patch.object(launcher.os, "open", side_effect=race):
            with self.assertRaises(ValueError):
                self.freeze()
        self.assertEqual(self.target.read_bytes(), b"OTHER_WRITER")


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "Java21 fixture requires javac/java")
class ObservedJarProcessTests(unittest.TestCase):
    def setUp(self):
        runtime_tests.RuntimePreparationTests.setUp(self)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("observability_runtime.py", "service_runtime_environment.py", "run-observed-jar.py"):
            shutil.copy2(ROOT / "scripts" / name, self.scripts / name)
        source = self.root / "Fixture.java"
        source.write_text(r'''
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import com.sun.net.httpserver.*;
public class Fixture {
  public static void premain(String ignored) throws Exception {
    Properties p = new Properties();
    try (InputStream input = Files.newInputStream(Path.of(System.getProperty("otel.javaagent.configuration-file")))) { p.load(input); }
    if (!"0".equals(p.getProperty("otel.span.event.count.limit"))) throw new IllegalStateException();
    if (!Files.isRegularFile(Path.of(System.getProperty("otel.javaagent.extensions")))) throw new IllegalStateException();
    HttpURLConnection c = (HttpURLConnection) URI.create(p.getProperty("otel.exporter.otlp.endpoint") + "/fixture-trace").toURL().openConnection();
    c.setConnectTimeout(2000); c.setReadTimeout(2000); c.setRequestMethod("POST"); c.setDoOutput(true);
    String[] header = p.getProperty("otel.exporter.otlp.headers").split("=", 2);
    c.setRequestProperty(header[0], URLDecoder.decode(header[1], StandardCharsets.UTF_8));
    try (OutputStream out = c.getOutputStream()) { out.write((p.getProperty("otel.service.name") + ";" + p.getProperty("otel.resource.attributes")).getBytes(StandardCharsets.UTF_8)); }
    if (c.getResponseCode() != 200) throw new IllegalStateException();
    System.setProperty("fixture.agent.loaded", "true");
  }
  public static void main(String[] ignored) throws Exception {
    if (!"true".equals(System.getProperty("fixture.agent.loaded"))) throw new IllegalStateException();
    Properties p = new Properties();
    try (InputStream input = Files.newInputStream(Path.of(URI.create(System.getProperty("spring.config.additional-location"))))) { p.load(input); }
    if (!"ecs".equals(p.getProperty("logging.structured.format.file"))) throw new IllegalStateException();
    if (!"27200".equals(p.getProperty("dubbo.protocol.port"))) throw new IllegalStateException();
    if (System.getenv("DUBBO_PROTOCOL_PORT") != null) throw new IllegalStateException();
    if (System.getenv("AI_STORAGE_KEK_B64") != null || System.getenv("AI_MODELCONTROLPLANE_APIKEY") != null || System.getenv("GITHUB_MINIO_SECRET_KEY") != null) throw new IllegalStateException("FOREIGN_ENV_REACHED_RAG");
    if (System.getProperty("GITHUB_RAG_QWEN_API_KEY") != null) throw new IllegalStateException("OS_SECRET_BECAME_PROPERTY");
    if (System.getProperty("fixture.expect.os") != null && !"SYNTHETIC_BOUND_RAG_KEY".equals(System.getenv("GITHUB_RAG_QWEN_API_KEY"))) throw new IllegalStateException("BOUND_OS_ENV_MISSING");
    Path log = Path.of(p.getProperty("logging.file.name"));
    Files.writeString(log, "{\"event\":\"fixture_started\",\"service\":\"rag\",\"version\":\"1.2.3\"}\n");
    HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/actuator/prometheus", exchange -> {
      boolean allowed = p.getProperty("security.actuator.token").equals(exchange.getRequestHeaders().getFirst("actuatorReq"));
      byte[] data = (allowed ? "fixture_metric 1\n" : "").getBytes(StandardCharsets.UTF_8);
      exchange.sendResponseHeaders(allowed ? 200 : 401, data.length);
      exchange.getResponseBody().write(data); exchange.close();
    });
    server.createContext("/delayed-class", exchange -> {
      int status = 200;
      String result;
      try {
        result = (String) Class.forName("DelayedFixture").getMethod("message").invoke(null)
          + "|" + Path.of(Fixture.class.getProtectionDomain().getCodeSource().getLocation().toURI());
      } catch (Throwable error) {
        status = 500; result = "DELAYED_CLASS_UNAVAILABLE";
      }
      byte[] data = result.getBytes(StandardCharsets.UTF_8);
      exchange.sendResponseHeaders(status, data.length);
      exchange.getResponseBody().write(data); exchange.close();
    });
    server.start();
    Files.writeString(Path.of(log + ".port"), Integer.toString(server.getAddress().getPort()));
  }
}
''')
        delayed = self.root / "DelayedFixture.java"
        delayed.write_text('public class DelayedFixture { public static String message() { return "DELAYED_CLASS_OK"; } }')
        subprocess.run(["javac", "--release", "21", str(source), str(delayed)], check=True, capture_output=True)
        with zipfile.ZipFile(self.agent, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nImplementation-Version: 2.33.1\nPremain-Class: Fixture\n\n")
            jar.write(self.root / "Fixture.class", "Fixture.class")
        digest = hashlib.sha256(self.agent.read_bytes()).hexdigest()
        text = self.env.read_text().replace(self.agent_digest, digest)
        self.env.write_text(text)
        helper = self.scripts / "observability_runtime.py"
        helper.write_text(helper.read_text().replace("e43a270df53c6f423e0ab157be9c27ccc01d5dc3232e162fe54777573db2f149", digest))
        self.artifact = self.root / "application.jar"
        with zipfile.ZipFile(self.artifact, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nMain-Class: Fixture\nStart-Class: com.kellen.RagApplication\nImplementation-Version: 1.2.3\n\n")
            jar.write(self.root / "Fixture.class", "Fixture.class")
            jar.write(self.root / "DelayedFixture.class", "DelayedFixture.class")
        self.command = ["python3", str(self.scripts / "run-observed-jar.py"), "rag", str(self.artifact),
                        "--runtime-env", str(self.env), "--dev-env", str(self.dev), "--state-dir", str(self.root / "state")]

    def test_jar_jvm_receives_private_files_and_protects_metrics_without_secret_output(self):
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append({"auth_valid": self.headers.get("Authorization") == "Bearer SYNTHETIC_OTLP_SECRET", "body": body})
                self.send_response(200); self.end_headers()
            def log_message(self, *args):
                pass
        collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=collector.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(collector.server_close)
        self.addCleanup(collector.shutdown)
        self.env.write_text(self.env.read_text().replace("http://127.0.0.1:14318", f"http://127.0.0.1:{collector.server_port}"))
        source=self.root.resolve()/"service-rag.json";source.write_text(json.dumps({"GITHUB_RAG_QWEN_API_KEY":"SYNTHETIC_BOUND_RAG_KEY"}));source.chmod(0o600)
        command=self.command+["--service-env-file",str(source)]
        # 夹具自身只用非秘密布尔 JVM 标记要求检查；真实 key 只能经目标 OS 环境传入。
        java_wrapper=self.root/"java-wrapper"
        java_wrapper.write_text("#!/bin/sh\nexec java -Dfixture.expect.os=true \"$@\"\n");java_wrapper.chmod(0o700)
        command.extend(["--java",str(java_wrapper)])
        environment = dict(os.environ, DUBBO_PROTOCOL_PORT="1", DUBBO_UNRELATED_TOOL_METADATA="keep",AI_STORAGE_KEK_B64="SYNTHETIC_FOREIGN_AI",AI_MODELCONTROLPLANE_APIKEY="SYNTHETIC_FOREIGN_CATALOG",GITHUB_MINIO_SECRET_KEY="SYNTHETIC_RETIRED_STORAGE")
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
        try:
            signal = self.root / "state/logs/rag.json.port"
            deadline = time.monotonic() + 12
            while not signal.is_file() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(signal.is_file(), "fixture JVM did not reach its config-checked HTTP listener")
            address = f"http://127.0.0.1:{signal.read_text()}/actuator/prometheus"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for token, expected in ((None, 401), ("wrong", 401), ("SYNTHETIC_ACTUATOR_SECRET", 200)):
                request = urllib.request.Request(address, headers={"actuatorReq": token} if token else {})
                try:
                    with opener.open(request, timeout=3) as response:
                        status = response.status
                except urllib.error.HTTPError as error:
                    status = error.code
                    error.close()
                self.assertEqual(status, expected)
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0]["auth_valid"])
            self.assertIn(b"service.version=1.2.3", received[0]["body"])
            log = (self.root / "state/logs/rag.json").read_text()
            self.assertEqual(json.loads(log)["event"], "fixture_started")
            self.assertNotIn("SECRET", log)
        finally:
            proc.terminate()
            output = proc.communicate(timeout=5)[0]
        self.assertNotIn("SYNTHETIC_", output)

    def test_dry_run_writes_nothing_and_rejects_wrong_application_identity(self):
        result = subprocess.run([*self.command, "--dry-run"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["rpc_port"], 27200)
        self.assertFalse((self.root / "state").exists())

        result = subprocess.run([*self.command, "--dry-run", "--", "--logging.structured.json.include=message"], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "state").exists())

    def test_source_jar_overwrite_cannot_break_delayed_class_loading(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200); self.end_headers()
            def log_message(self, *args):
                pass
        collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=collector.serve_forever, daemon=True).start()
        self.addCleanup(collector.server_close)
        self.addCleanup(collector.shutdown)
        self.env.write_text(self.env.read_text().replace("http://127.0.0.1:14318", f"http://127.0.0.1:{collector.server_port}"))
        original_digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        expected = self.root / "state/immutable-jars" / f"rag-1.2.3-{original_digest}.jar"
        proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            signal = self.root / "state/logs/rag.json.port"
            deadline = time.monotonic() + 12
            while not signal.is_file() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(signal.is_file(), "fixture JVM did not reach its HTTP listener")
            with zipfile.ZipFile(self.artifact, "w") as jar:
                jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
                jar.writestr("rebuilt.txt", "REBUILT_SOURCE_WITHOUT_DELAYED_CLASS")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                response = opener.open(f"http://127.0.0.1:{signal.read_text()}/delayed-class", timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read().decode()
                self.assertEqual(response.status, 200, body)
                self.assertEqual(body, f"DELAYED_CLASS_OK|{expected.resolve()}")
            self.assertEqual(hashlib.sha256(expected.read_bytes()).hexdigest(), original_digest)
            self.assertEqual(expected.stat().st_mode & 0o777, 0o400)
            self.assertEqual(expected.stat().st_nlink, 1)
            self.assertNotEqual(expected.stat().st_ino, self.artifact.stat().st_ino)
        finally:
            proc.terminate()
            output = proc.communicate(timeout=5)[0]
        self.assertNotIn("SYNTHETIC_", output)

    def test_provider_cli_port_override_is_rejected_before_preparation_or_jvm(self):
        for argument in ("--dubbo.protocol.port=1", "--TRI_DUBBO_PORT_TO_BIND=1"):
            result = subprocess.run([*self.command, "--dry-run", "--", argument], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / "state").exists())
            self.assertNotIn("SYNTHETIC_", result.stdout + result.stderr)
        self.assertNotIn("SYNTHETIC_", result.stdout + result.stderr)
        command = list(self.command)
        command[2] = "gateway"
        result = subprocess.run([*command, "--dry-run"], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "state").exists())


if __name__ == "__main__":
    unittest.main()
