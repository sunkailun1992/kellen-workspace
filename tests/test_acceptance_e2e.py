"""持久化后端 fixture；成功 HTTP 响应不能掩盖丢 Trace、断父子或漏日志。"""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from acceptance_core import AcceptanceError, Span, _protocol, business_result, decode_stream, parse_logs, parse_traces, reconcile, validate_spec
from acceptance_run import Backend, ROOT, fetch, headers_for, load_config, manifest, private_bytes, private_json, read_ledger, readback, render, safe_write, send_one


TRACE = "1234567890abcdef1234567890abcdef"
REMOTE = "1111111111111111"


def span(number, service, parent, kind=2, protocol="http", sampled=True):
    attrs = {"http.request.method": "GET"} if protocol == "http" else {"rpc.system": "dubbo"}
    return Span(TRACE, f"{number:016x}", f"{parent:016x}" if isinstance(parent, int) else parent, service, "1.2.3", "github-dev", kind, attrs, False, sampled)


def fixture():
    spec = {"case": "message-rpc", "method": "GET", "path": "/synthetic", "route": "/messages/rpc-test/users/{userId}", "entry_service": "gateway",
            "expected": {"statuses": [200], "services": ["gateway", "message", "user"], "environment": "github-dev",
                         "versions": {name: "1.2.3" for name in ["gateway", "message", "user"]},
                         "edges": [{"parent": "gateway", "child": "message", "protocol": "http"}, {"parent": "message", "child": "user", "protocol": "dubbo"}],
                         "json_assertions": [{"path": ["success"], "op": "true"}, {"path": ["data"], "op": "present"}]}}
    record = {"request_id": "request-001", "case": "message-rpc", "trace_id": TRACE, "parent_span_id": REMOTE,
              "sampled": 1, "sent": True, "completed": True, "http_status": 200, "business_ok": True}
    spans = [span(1, "gateway", REMOTE), span(2, "gateway", 1, 3), span(3, "message", 2),
             span(4, "message", 3, 3, "dubbo"), span(5, "user", 4, 2, "dubbo")]
    logs = [log_for(item) for item in spans if item.kind == 2 and "http.request.method" in item.attrs]
    return spec, record, spans, logs


def log_for(item):
    return {"TraceId": item.trace, "SpanId": item.span,
            "Resource": {"service.name": item.service, "service.version": item.version, "deployment.environment.name": item.environment},
            "event": "http.server.exchange", "trace_id": item.trace, "span_id": item.span,
            "http_status": 200, "duration_ms": 1.5, "outcome": "success"}


def otlp(spans, *, encoded=False):
    def encode(value):
        return base64.b64encode(bytes.fromhex(value)).decode() if encoded else value
    resources = []
    for item in spans:
        resources.append({"resource": {"attributes": [{"key": key, "value": {"stringValue": value}} for key, value in
                          [("service.name", item.service), ("service.version", item.version), ("deployment.environment.name", item.environment)]]},
                          "scopeSpans": [{"spans": [{"traceId": encode(item.trace), "spanId": encode(item.span), "parentSpanId": encode(item.parent),
                          "kind": item.kind, "flags": 1 if item.sampled else 0,
                          "attributes": [{"key": key, "value": {"stringValue": value}} for key, value in item.attrs.items()]}]}]})
    return {"resourceSpans": resources}


class FixtureBackend:
    def __init__(self, spans=None, logs=None, search=None, error=None):
        _, _, defaults, default_logs = fixture()
        self.spans = defaults if spans is None else spans
        self.records = default_logs if logs is None else logs
        self.ids = {TRACE} if search is None else search
        self.error = error

    def trace(self, trace_id):
        if self.error:
            raise AcceptanceError(self.error)
        return [span for span in self.spans if span.trace == trace_id]

    def search(self, *args):
        return self.ids

    def logs(self, *args):
        return self.records


class ReconciliationTests(unittest.TestCase):
    def check(self, spec=None, record=None, spans=None, logs=None, search=None):
        defaults = fixture()
        return reconcile(spec or defaults[0], record or defaults[1], spans if spans is not None else defaults[2],
                         logs if logs is not None else defaults[3], search if search is not None else {TRACE})

    def test_complete_http_and_real_dubbo_parent_join(self):
        result = self.check()
        self.assertTrue(result["passed"], result)
        self.assertEqual((result["edges_joined"], result["terminal_logs_joined"]), (2, 2))

    def test_legacy_and_current_dubbo_semantics_join_the_same_real_edge(self):
        for attributes in ({"rpc.system": "apache_dubbo"}, {"rpc.system.name": "dubbo"},
                           {"rpc.system": "dubbo"}, {"rpc.system.name": "apache_dubbo"},
                           {"rpc.system": "apache_dubbo", "rpc.system.name": "dubbo"}):
            with self.subTest(attributes=attributes):
                _, _, spans, _ = fixture()
                spans[-2] = replace(spans[-2], attrs=attributes)
                spans[-1] = replace(spans[-1], attrs=attributes)
                result = self.check(spans=spans)
                self.assertTrue(result["passed"], result)
                self.assertEqual(result["edges_joined"], 2)

    def test_unrelated_malformed_or_conflicting_rpc_systems_cannot_join_dubbo(self):
        for attributes in ({"rpc.system": "grpc"}, {"rpc.system.name": "jsonrpc"},
                           {"rpc.system": "apache_dubbox"}, {"rpc.system": "DUBBO"},
                           {"rpc.system": ["dubbo"]}, {"rpc.system.name": {"value": "dubbo"}},
                           {"rpc.system": None}, {"rpc.system": "grpc", "rpc.system.name": "dubbo"},
                           {"rpc.system": "dubbo", "rpc.system.name": "grpc"}):
            with self.subTest(attributes=attributes):
                _, _, spans, _ = fixture()
                spans[-1] = replace(spans[-1], attrs=attributes)
                result = self.check(spans=spans)
                self.assertFalse(result["passed"])
                self.assertIn("CROSS_SERVICE_PARENT_MISMATCH", result["failures"])

    def test_unknown_protocol_does_not_fall_back_to_http_evidence(self):
        observed = span(1, "gateway", REMOTE)
        for protocol in ("grpc", "jsonrpc", "apache_dubbo", "", None):
            with self.subTest(protocol=protocol):
                self.assertFalse(_protocol(observed, protocol))

    def test_required_failures_are_not_replaced_by_http_200(self):
        spec, record, spans, logs = fixture()
        cases = [
            ([], logs, {TRACE}, "TRACE_MISSING"),
            (spans[:-1], logs, {TRACE}, "EXPECTED_SERVICE_MISSING"),
            (spans, logs[:-1], {TRACE}, "HTTP_TERMINAL_LOG_MISSING"),
            (spans, logs + [logs[0]], {TRACE}, "HTTP_TERMINAL_LOG_DUPLICATE"),
            (spans, logs, set(), "TRACE_SEARCH_MISSING"),
            (spans + [span(8, "user", 9, 1, "dubbo")], logs, {TRACE}, "SPAN_PARENT_MISSING"),
        ]
        for observed, observed_logs, ids, reason in cases:
            with self.subTest(reason=reason):
                result = self.check(spans=observed, logs=observed_logs, search=ids)
                self.assertFalse(result["passed"])
                self.assertIn(reason, result["failures"])

    def test_wrong_parent_same_services_fails(self):
        spec, record, spans, logs = fixture()
        spans[-1] = span(5, "user", 1, 2, "dubbo")
        result = self.check(spans=spans)
        self.assertIn("CROSS_SERVICE_PARENT_MISMATCH", result["failures"])

    def test_gateway_reentry_requires_same_ancestor_path(self):
        spec, record, _, _ = fixture()
        spec["expected"]["services"] = ["gateway", "ai", "ai-agent", "user"]
        spec["expected"]["versions"] = {s: "1.2.3" for s in spec["expected"]["services"]}
        spec["expected"]["edges"] = [{"parent": "gateway", "child": "ai", "protocol": "http"}, {"parent": "ai", "child": "gateway", "protocol": "http"}, {"parent": "gateway", "child": "ai-agent", "protocol": "http"}, {"parent": "ai-agent", "child": "user", "protocol": "dubbo"}]
        spec["expected"]["paths"] = [["gateway", "ai", "gateway", "ai-agent", "user"]]
        validate_spec(spec)
        spans = [span(1,"gateway",REMOTE),span(2,"gateway",1,3),span(3,"ai",2),span(4,"ai",3,3),span(5,"gateway",4),span(6,"gateway",5,3),span(7,"ai-agent",6),span(8,"ai-agent",7,3,"dubbo"),span(9,"user",8,2,"dubbo")]
        logs = [log_for(s) for s in spans if s.kind == 2 and "http.request.method" in s.attrs]
        self.assertTrue(self.check(spec=spec,spans=spans,logs=logs)["passed"])
        spans[5] = span(6,"gateway",1,3)
        result = self.check(spec=spec,spans=spans,logs=logs)
        self.assertNotIn("CROSS_SERVICE_PARENT_MISMATCH", result["failures"])
        self.assertIn("SERVICE_ANCESTRY_PATH_MISMATCH", result["failures"])

    def test_no_model_case_rejects_unexpected_engine_branch(self):
        spec, record, spans, logs = fixture()
        spec["expected"]["forbidden_services"] = ["rag-engine"]
        self.assertTrue(self.check(spec=spec)["passed"])
        spans.append(span(10,"rag-engine",5,1,"dubbo"))
        self.assertIn("FORBIDDEN_SERVICE_OBSERVED", self.check(spec=spec,spans=spans)["failures"])

    def test_wrong_external_parent_fails(self):
        spec, record, spans, logs = fixture()
        record["parent_span_id"] = "2222222222222222"
        self.assertIn("ENTRY_PARENT_MISMATCH", self.check(record=record)["failures"])

    def test_incoming_unsampled_is_still_required_to_be_stored(self):
        spec, record, spans, logs = fixture()
        record["sampled"] = 0
        self.assertTrue(self.check(record=record)["passed"])
        result = self.check(record=record, spans=[], logs=[], search=set())
        self.assertFalse(result["passed"])
        self.assertIn("TRACE_MISSING", result["failures"])

    def test_stored_unsampled_flag_is_not_accepted(self):
        spec, record, spans, logs = fixture()
        spans[-1] = span(5, "user", 4, 2, "dubbo", False)
        self.assertIn("UNSAMPLED_STORED_SPAN", self.check(spans=spans)["failures"])

    def test_wrong_version_or_log_trace_cannot_join(self):
        spec, record, spans, logs = fixture()
        spec["expected"]["versions"]["user"] = "9.9.9"
        self.assertIn("RESOURCE_VERSION_MISMATCH", self.check(spec=spec)["failures"])
        logs[0]["TraceId"] = "f" * 32
        self.assertIn("LOG_OBSERVATION_ID_MISMATCH", self.check(logs=logs)["failures"])

    def test_zero_missing_status_or_duration_not_fabricated(self):
        spec, record, spans, logs = fixture()
        del logs[0]["http_status"]
        self.assertIn("HTTP_LOG_CONTRACT_INVALID", self.check(logs=logs)["failures"])

    def test_unknown_http_event_span_is_not_silently_ignored(self):
        spec, record, spans, logs = fixture()
        rogue = {**logs[0], "span_id": "8" * 16, "SpanId": "8" * 16}
        self.assertIn("HTTP_EVENT_SPAN_JOIN_FAILED", self.check(logs=logs + [rogue])["failures"])

    def test_safe_projection_allow_value_and_remove_unknown(self):
        spec, record, spans, logs = fixture()
        spec["expected"]["log_assertions"] = [{"service": "message", "field": "response_body", "json_assertions": [
            {"path": ["success"], "op": "true"}, {"path": ["data", "password"], "op": "absent"}]}]
        logs[1]["response_body"] = '{"success":true}'
        self.assertTrue(self.check(spec=spec, logs=logs)["passed"])
        logs[1]["response_body"] = '{"success":true,"data":{"password":"synthetic-secret"}}'
        result = reconcile(spec, record, spans, logs, {TRACE}, ("synthetic-secret",))
        self.assertIn("LOG_PROJECTION_ASSERTION_FAILED", result["failures"])
        self.assertIn("SYNTHETIC_SENTINEL_LEAK", result["failures"])
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_ai_business_event_must_join_real_model_span(self):
        spec, record, spans, logs = fixture()
        spec["expected"]["event_assertions"] = [{"service": "user", "event": "ai.model.call", "fields": {"ai_input": {"op": "absent"}, "ai_usage_available": {"op": "true"}}}]
        event = {**log_for(spans[-1]), "event": "ai.model.call", "ai_usage_available": True}
        self.assertTrue(self.check(spec=spec, logs=logs + [event])["passed"])
        event["span_id"] = event["SpanId"] = "9" * 16
        self.assertIn("BUSINESS_EVENT_SPAN_JOIN_FAILED", self.check(spec=spec, logs=logs + [event])["failures"])

    def test_semantic_model_fallback_and_null_user_are_failures(self):
        expected = {"statuses": [200], "json_assertions": [{"path": ["model"], "op": "nonempty_string"}]}
        self.assertFalse(business_result(b'{"model":""}', 200, expected))
        self.assertTrue(business_result(b'{"model":"fixture-model"}', 200, expected))
        spec = fixture()[0]["expected"]
        self.assertFalse(business_result(b'{"success":true,"data":null}', 200, spec))
        self.assertFalse(business_result(b'{"success":false,"data":{}}', 200, spec))

    def test_manifest_requires_business_expectations_and_edge_coverage(self):
        spec = fixture()[0]
        validate_spec(spec)
        del spec["expected"]["json_assertions"]
        with self.assertRaisesRegex(AcceptanceError, "BUSINESS_ASSERTION_REQUIRED"):
            validate_spec(spec)
        spec = fixture()[0]
        spec["expected"]["edges"] = []
        with self.assertRaisesRegex(AcceptanceError, "EXPECTED_EDGE_COVERAGE_REQUIRED"):
            validate_spec(spec)

    def test_empty_success_status_only_is_rejected(self):
        expected = {"statuses": [200]}
        self.assertFalse(business_result(b'{}', 200, expected))
        self.assertTrue(business_result(b'', 403, {"statuses": [403], "negative": True}))


class BackendTests(unittest.TestCase):
    def test_otlp_hex_base64_and_stream_wrappers(self):
        spans = fixture()[2]
        for encoded in [False, True]:
            with self.subTest(encoded=encoded):
                raw = json.dumps({"result": otlp(spans, encoded=encoded)}).encode()
                self.assertEqual(parse_traces(raw), spans)
        raw = b"\n".join(json.dumps({"result": otlp([item])}).encode() for item in spans)
        self.assertEqual(parse_traces(raw), spans)

    def test_protobuf_int_values_remain_numeric(self):
        document = otlp(fixture()[2])
        document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append({"key": "http.status_code", "value": {"intValue": "200"}})
        self.assertEqual(parse_traces(json.dumps(document).encode())[0].attrs["http.status_code"], 200)

    def test_partial_stream_error_must_not_be_swallowed(self):
        raw = json.dumps({"result": otlp(fixture()[2])}).encode() + b'\n{"error":{"message":"synthetic-provider-secret"}}'
        with self.assertRaisesRegex(AcceptanceError, "BACKEND_STREAM_ERROR"):
            parse_traces(raw)

    def test_sentinel_in_nonprojected_span_event_is_still_a_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "sentinels.json"
            forbidden.write_text('["synthetic-secret-never-export"]')
            forbidden.chmod(0o600)
            config = {"jaeger": {"base_url": "http://example.invalid"}, "elasticsearch": {"base_url": "http://example.invalid"}, "forbidden_values_file": str(forbidden)}
            backend = Backend(config)
            document = otlp(fixture()[2])
            document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["events"] = [{"name": "synthetic-secret-never-export"}]
            with patch("acceptance_run.fetch", return_value=(200, json.dumps(document).encode())):
                with self.assertRaisesRegex(AcceptanceError, "SYNTHETIC_SENTINEL_LEAK"):
                    backend.trace(TRACE)

    def test_truncated_shards_and_timeout_logs_are_failures(self):
        for values, code in [({"timed_out": True}, "LOG_SEARCH_PARTIAL"), ({"_shards": {"failed": 1}}, "LOG_SEARCH_PARTIAL"), ({}, "LOG_SEARCH_TRUNCATED")]:
            document = {"hits": {"total": {"value": 10}, "hits": []}, **values}
            with self.subTest(code=code), self.assertRaisesRegex(AcceptanceError, code):
                parse_logs(json.dumps(document).encode())

    def test_n_of_n_missing_record_is_failure(self):
        spec, record, spans, logs = fixture()
        config = {"requests": [spec], "settle_seconds": 0}
        result = readback(config, [], datetime.now(timezone.utc), FixtureBackend())
        self.assertFalse(result["passed"])
        self.assertIn("REQUEST_LEDGER_MISMATCH", result["backend_failures"])

    def test_duplicate_trace_cannot_account_for_two_requests(self):
        spec, record, spans, logs = fixture()
        second = {**spec, "case": "second"}
        result = readback({"requests": [spec, second]}, [record, {**record, "case": "second"}], datetime.now(timezone.utc), FixtureBackend())
        self.assertFalse(result["passed"])
        self.assertIn("REQUEST_LEDGER_MISMATCH", result["backend_failures"])

    def test_partial_export_and_export_error_fail_readback(self):
        spec, record, spans, logs = fixture()
        config = {"requests": [spec], "settle_seconds": 0}
        for backend in [FixtureBackend(spans=spans[:-1]), FixtureBackend(error="TRACE_READBACK_HTTP_ERROR")]:
            with self.subTest(backend=backend):
                result = readback(config, [record], datetime.now(timezone.utc), backend)
                self.assertFalse(result["passed"])
                self.assertEqual(result["planned"], 1)
                self.assertEqual(result["passed_count"], 0)

    def test_export_recovery_requires_final_readback_without_resending(self):
        spec, record, spans, logs = fixture()

        class RecoveringBackend(FixtureBackend):
            calls = 0

            def trace(self, trace_id):
                self.calls += 1
                return [] if self.calls == 1 else super().trace(trace_id)

        backend = RecoveringBackend()
        with patch("acceptance_run.time.sleep"):
            result = readback({"requests": [spec], "settle_seconds": 1}, [record], datetime.now(timezone.utc), backend)
        self.assertEqual(backend.calls, 2)
        self.assertTrue(result["passed"])

    def test_fixture_reads_full_trace_and_es_per_request(self):
        spec, record, spans, logs = fixture()
        result = readback({"requests": [spec], "settle_seconds": 0}, [record], datetime.now(timezone.utc), FixtureBackend())
        self.assertTrue(result["passed"])
        self.assertEqual((result["planned"], result["sent"], result["completed"], result["passed_count"]), (1, 1, 1, 1))
        self.assertNotIn("tenantId", render(result))

    def test_v3_http_binding_and_es_get_body_with_fixture_server(self):
        captured = []
        spans = fixture()[2]
        logs = fixture()[3]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                captured.append((self.path, body))
                payload = {"hits": {"total": len(logs), "hits": [{"_source": item} for item in logs]}} if self.path.endswith("/_search") else {"result": otlp(spans)}
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            backend = Backend({"jaeger": {"base_url": url}, "elasticsearch": {"base_url": url + "/index"}})
            now = datetime.now(timezone.utc)
            self.assertEqual(backend.search("gateway", now, now), {TRACE})
            self.assertEqual(backend.trace(TRACE), spans)
            self.assertEqual(backend.logs([TRACE], now, now), logs)
            self.assertTrue(captured[0][0].startswith("/api/v3/traces?query.serviceName=gateway&"))
            self.assertIn("query.startTimeMin=", captured[0][0])
            self.assertTrue(captured[1][0].startswith("/api/v3/traces/" + TRACE))
            self.assertIn(TRACE, captured[2][1].decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class PrivateExecutionTests(unittest.TestCase):
    def test_readback_ledger_does_not_repeat_request_or_keep_unknown_keys(self):
        spec, record, spans, logs = fixture()
        directory = ROOT / "build"
        directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="acceptance-fixture-", dir=directory) as temporary:
            path = Path(temporary) / "report.json"
            report = {"started_at": "2026-11-09T00:00:00Z", "requests": [{**record, "arbitrary_body": "synthetic-never-save-again"}]}
            safe_write(path, json.dumps(report))
            records, start = read_ledger(path, {"requests": [spec]})
            self.assertEqual(len(records), 1)
            self.assertNotIn("arbitrary_body", records[0])
            self.assertIsNotNone(start.tzinfo)
            report["requests"][0]["case"] = "different-case"
            safe_write(path, json.dumps(report))
            with self.assertRaisesRegex(AcceptanceError, "REQUEST_LEDGER_MISMATCH"):
                read_ledger(path, {"requests": [spec]})
            report["requests"][0]["case"] = spec["case"]
            report["requests"][0]["sampled"] = "synthetic-must-not-reach-report"
            safe_write(path, json.dumps(report))
            with self.assertRaisesRegex(AcceptanceError, "LEDGER_SCHEMA_INVALID"):
                read_ledger(path, {"requests": [spec]})

    def test_private_reference_permissions_symlink_and_git_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "private.json"
            file.write_text('{"token":"synthetic-token"}')
            file.chmod(0o600)
            self.assertEqual(private_json(file)["token"], "synthetic-token")
            file.chmod(0o644)
            with self.assertRaisesRegex(AcceptanceError, "PRIVATE_FILE_OWNER_MODE"):
                private_json(file)
            file.chmod(0o600)
            link = Path(directory) / "link.json"
            link.symlink_to(file)
            with self.assertRaisesRegex(AcceptanceError, "PRIVATE_FILE_INVALID"):
                private_json(link)
            subprocess.run(["git", "init", "-q", directory], check=True)
            with self.assertRaisesRegex(AcceptanceError, "PRIVATE_FILE_INSIDE_GIT"):
                private_json(file)

    def test_payload_and_auth_never_enter_ledger_or_report(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "auth.json"
            auth.write_text('{"data":{"token":"synthetic-credential-never-print"}}')
            auth.chmod(0o600)
            payload = Path(directory) / "payload.json"
            payload.write_text('{"query":"synthetic-body-never-print"}')
            payload.chmod(0o600)
            spec = fixture()[0]
            spec["payload_file"] = str(payload)
            config = {"business": {"base_url": "http://example.invalid", "auth_file": str(auth)}, "requests": [spec]}
            record = manifest(config)[0]
            with patch("acceptance_run.fetch", return_value=(200, b'{"success":true,"data":{"secret":"synthetic-reply-never-print"}}')) as remote:
                result = send_one(config, spec, record)
                self.assertIn("synthetic-body-never-print", remote.call_args.kwargs["body"].decode())
                self.assertIn("synthetic-credential-never-print", remote.call_args.kwargs["headers"]["Authorization"])
            serialized = json.dumps(result)
            for value in ["synthetic-credential-never-print", "synthetic-body-never-print", "synthetic-reply-never-print"]:
                self.assertNotIn(value, serialized)
            self.assertTrue(result["business_ok"])

    def test_transport_failure_preserves_sent_ledger(self):
        spec = fixture()[0]
        config = {"business": {"base_url": "http://example.invalid"}, "requests": [spec]}
        with patch("acceptance_run.fetch", side_effect=AcceptanceError("TRANSPORT_FAILED")):
            result = send_one(config, spec, manifest(config)[0])
        self.assertTrue(result["sent"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["transport_reason"], "TRANSPORT_FAILED")

    def test_early_stream_close_still_needs_business_span_and_terminal_log(self):
        spec, record, spans, logs = fixture()
        spec.update(response_mode="sse", cancel_after_bytes=32)
        spec["expected"].pop("json_assertions")
        spec["expected"]["span_assertions"] = [{"service": "message", "attributes": {"business.outcome": {"op": "equals", "value": "cancelled"}}}]
        spec["expected"]["log_assertions"] = [{"service": "message", "field": "outcome", "op": "equals", "value": "cancelled"}]
        config = {"business": {"base_url": "http://example.invalid"}, "requests": [spec]}
        with patch("acceptance_run.fetch", return_value=(200, b'data: synthetic-only\n\n')) as transport:
            result = send_one(config, spec, record)
        self.assertEqual(transport.call_args.kwargs["cancel_after_bytes"], 32)
        self.assertTrue(result["client_cancelled"])
        failed = reconcile(spec, result, spans, logs, {TRACE})
        self.assertFalse(failed["passed"])
        self.assertIn("BUSINESS_SPAN_ASSERTION_FAILED", failed["failures"])
        self.assertIn("LOG_PROJECTION_ASSERTION_FAILED", failed["failures"])


if __name__ == "__main__":
    unittest.main()
