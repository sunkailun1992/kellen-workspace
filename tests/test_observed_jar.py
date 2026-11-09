"""独立 Java17 进程核对 Jar 入口的配置传递；不替代正式 OTel/Boot 端到端验收。"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

import test_observability_runtime as runtime_tests

ROOT = runtime_tests.ROOT


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "Java17 fixture requires javac/java")
class ObservedJarProcessTests(unittest.TestCase):
    def setUp(self):
        runtime_tests.RuntimePreparationTests.setUp(self)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("observability_runtime.py", "run-observed-jar.py"):
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
    Path log = Path.of(p.getProperty("logging.file.name"));
    Files.writeString(log, "{\"event\":\"fixture_started\",\"service\":\"rag\",\"version\":\"1.2.3\"}\n");
    HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/actuator/prometheus", exchange -> {
      boolean allowed = p.getProperty("security.actuator.token").equals(exchange.getRequestHeaders().getFirst("actuatorReq"));
      byte[] data = (allowed ? "fixture_metric 1\n" : "").getBytes(StandardCharsets.UTF_8);
      exchange.sendResponseHeaders(allowed ? 200 : 401, data.length);
      exchange.getResponseBody().write(data); exchange.close();
    });
    server.start();
    Files.writeString(Path.of(log + ".port"), Integer.toString(server.getAddress().getPort()));
  }
}
''')
        subprocess.run(["javac", "--release", "17", str(source)], check=True, capture_output=True)
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
        environment = dict(os.environ, DUBBO_PROTOCOL_PORT="1", DUBBO_UNRELATED_TOOL_METADATA="keep")
        proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
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
