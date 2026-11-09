"""全链验收的纯函数：只生成计数、身份与固定原因码，不保留业务正文。"""

from __future__ import annotations

import base64
import codecs
import json
import re
from dataclasses import dataclass
from typing import Any


class AcceptanceError(Exception):
    """异常文本只能使用固定原因码，不能拼接远端响应。"""


SERVICES = frozenset({"gateway", "user", "ai", "ai-agent", "ai-memory", "mcp", "message", "rag", "rag-engine", "report"})
SAFE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z")
SAFE_ROUTE = re.compile(r"/[a-zA-Z0-9_./{}:*\-]{0,191}\Z")
SAFE_VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.+\-]{0,63}\Z")


def identity(value: Any, width: int, *, empty: bool = False) -> str:
    """OTLP protobuf JSON 使用 base64 bytes；Jaeger 也可能返回十六进制。"""
    if empty and not value:
        return ""
    if not isinstance(value, str):
        raise AcceptanceError("INVALID_OBSERVATION_ID")
    if re.fullmatch(r"[0-9a-fA-F]{%d}" % (width * 2), value):
        raw = bytes.fromhex(value)
    else:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise AcceptanceError("INVALID_OBSERVATION_ID") from None
    if len(raw) != width or not any(raw):
        if empty and len(raw) == width and not any(raw):
            return ""
        raise AcceptanceError("INVALID_OBSERVATION_ID")
    return raw.hex()


def field(value: Any, key: str, default: Any = None) -> Any:
    """兼容 ES 扁平键、OTel Resource 的带点键及嵌套对象。"""
    if not isinstance(value, dict):
        return default
    if key in value:
        return value[key]
    head, sep, tail = key.partition(".")
    return field(value.get(head), tail, default) if sep else default


def attributes(items: Any) -> dict[str, Any]:
    result = {}
    for item in items or []:
        value = item.get("value", {})
        scalar = next((value[k] for k in ("stringValue", "intValue", "doubleValue", "boolValue") if k in value), None)
        if "intValue" in value:
            try:
                scalar = int(scalar)
            except (ValueError, TypeError):
                raise AcceptanceError("BACKEND_ATTRIBUTE_TYPE") from None
        result[item.get("key")] = scalar
    return result


@dataclass(frozen=True)
class Span:
    trace: str
    span: str
    parent: str
    service: str
    version: str
    environment: str
    kind: int
    attrs: dict[str, Any]
    status_error: bool
    sampled: bool | None
    name: str = ""


def decode_stream(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8", errors="strict")
        try:
            document = json.loads(text)
            documents = document if isinstance(document, list) else [document]
        except json.JSONDecodeError:
            documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeError, ValueError):
        raise AcceptanceError("BACKEND_INVALID_JSON") from None
    if any(not isinstance(item, dict) or "error" in item for item in documents):
        raise AcceptanceError("BACKEND_STREAM_ERROR")
    return documents


def parse_traces(raw: bytes) -> list[Span]:
    spans: dict[tuple[str, str], Span] = {}
    kinds = {"SPAN_KIND_INTERNAL": 1, "SPAN_KIND_SERVER": 2, "SPAN_KIND_CLIENT": 3, "SPAN_KIND_PRODUCER": 4, "SPAN_KIND_CONSUMER": 5}
    for envelope in decode_stream(raw):
        document = envelope.get("result", envelope)
        if not isinstance(document, dict) or not any(key in document for key in ("resourceSpans", "resource_spans")):
            raise AcceptanceError("BACKEND_TRACE_SCHEMA")
        for resource in document.get("resourceSpans", document.get("resource_spans", [])):
            attrs = attributes(resource.get("resource", {}).get("attributes"))
            for scope in resource.get("scopeSpans", resource.get("scope_spans", [])):
                for item in scope.get("spans", []):
                    kind = item.get("kind", 0)
                    if isinstance(kind, str):
                        kind = kinds.get(kind, int(kind) if kind.isdigit() else 0)
                    flags = item.get("flags")
                    span = Span(
                        identity(item.get("traceId", item.get("trace_id")), 16),
                        identity(item.get("spanId", item.get("span_id")), 8),
                        identity(item.get("parentSpanId", item.get("parent_span_id")), 8, empty=True),
                        attrs.get("service.name", ""), attrs.get("service.version", ""),
                        attrs.get("deployment.environment.name", attrs.get("deployment.environment", "")),
                        kind, attributes(item.get("attributes")),
                        item.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR", "2"),
                        bool(int(flags) & 1) if flags is not None else None,
                        item.get("name", ""),
                    )
                    key = (span.trace, span.span)
                    if key in spans and spans[key] != span:
                        raise AcceptanceError("BACKEND_CONFLICTING_SPAN")
                    spans[key] = span
    return list(spans.values())


def parse_logs(raw: bytes) -> list[dict]:
    try:
        document = json.loads(raw)
        hits = document["hits"]["hits"]
        total = document["hits"].get("total", len(hits))
        total = total.get("value", 0) if isinstance(total, dict) else total
        if document.get("timed_out") or document.get("_shards", {}).get("failed", 0):
            raise AcceptanceError("LOG_SEARCH_PARTIAL")
        if total > len(hits):
            raise AcceptanceError("LOG_SEARCH_TRUNCATED")
        return [hit["_source"] for hit in hits]
    except (KeyError, TypeError, ValueError):
        raise AcceptanceError("BACKEND_LOG_SCHEMA") from None


def log_field(log: dict, name: str) -> Any:
    return field(log, name, field(log, "Attributes." + name))


def log_identity(log: dict) -> tuple[str, str]:
    return (
        identity(log_field(log, "trace_id") or field(log, "TraceId"), 16),
        identity(log_field(log, "span_id") or field(log, "SpanId"), 8),
    )


def lookup(data: Any, path: list[str | int]) -> Any:
    try:
        for key in path:
            data = data[key]
        return data
    except (KeyError, IndexError, TypeError):
        return None


def matches(value: Any, assertion: dict) -> bool:
    op = assertion["op"]
    if op == "nonempty_string":
        return isinstance(value, str) and bool(value.strip())
    if op == "nonempty_collection":
        return isinstance(value, (list, dict)) and bool(value)
    if op == "present":
        return value is not None
    if op == "absent":
        return value is None
    if op == "true":
        return value is True
    if op == "false":
        return value is False
    if op == "positive_integer":
        return type(value) is int and value > 0
    if op == "equals":
        return type(value) is type(assertion.get("value")) and value == assertion.get("value")
    if op == "one_of":
        return any(type(value) is type(candidate) and value == candidate for candidate in assertion.get("values", []))
    raise AcceptanceError("INVALID_ASSERTION_OPERATOR")


def business_result(raw: bytes, status: int, spec: dict) -> bool:
    """正文仅在内存求布尔；200 必须有语义断言，不能当自动成功。"""
    if status not in spec["statuses"]:
        return False
    assertions = spec.get("json_assertions", [])
    if not assertions:
        # SSE 等不能以 HTTP 正文判断的路径，稍后必须通过真实业务 span 断言。
        return (spec.get("negative") is True and status >= 400) or bool(spec.get("span_assertions"))
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError):
        return False
    return all(matches(lookup(document, item["path"]), item) for item in assertions)


SSE_EVIDENCE_KEYS = frozenset({"data_received", "explicit_error", "explicit_complete", "response_complete", "client_closed"})


def sse_evidence(raw: bytes, transport: dict) -> dict:
    """只保留协议布尔；正文在内存分类后丢弃，不能把 HTTP200 或收到数据当业务成功。"""
    complete = transport.get("response_complete") is True
    result = {"data_received": False, "explicit_error": False, "explicit_complete": False,
              "response_complete": complete, "client_closed": transport.get("client_closed") is True}
    try:
        text = codecs.getincrementaldecoder("utf-8")(errors="strict").decode(raw, final=complete)
    except UnicodeError:
        result["explicit_error"] = True
        return result
    event, data = "message", []

    def inspect():
        value = "\n".join(data).lstrip()
        result["data_received"] |= bool(value)
        result["explicit_error"] |= event.lower() in {"error", "failure", "failed"}
        result["explicit_complete"] |= event.lower() in {"complete", "completed", "done"} or value == "[DONE]"
        result["explicit_error"] |= value.startswith(("[请求参数错误]", "[模型调用失败]"))
        if value.startswith("{"):
            try:
                document = json.loads(value)
            except (ValueError, TypeError):
                return
            if isinstance(document, dict):
                kind = document.get("type")
                result["explicit_error"] |= document.get("error") is not None or isinstance(kind, str) and kind in {"error", "failure", "failed"}

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            inspect()
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].lstrip(" ")
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    inspect()
    return result


def valid_sse_evidence(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == SSE_EVIDENCE_KEYS and all(type(v) is bool for v in value.values())


def sse_business_confirmed(spec: dict, record: dict, actual: dict[str, Span], logs: list[dict], failures: set[str]) -> bool:
    evidence = record.get("sse_evidence")
    if not valid_sse_evidence(evidence) or not evidence["data_received"] or evidence["explicit_error"]:
        return False
    if record.get("http_status") not in spec["expected"]["statuses"]:
        return False
    cancelled = bool(spec.get("cancel_after_bytes"))
    if cancelled:
        if not (record.get("client_cancelled") is True and evidence["client_closed"] and not evidence["response_complete"] and not evidence["explicit_complete"]):
            return False
    elif not evidence["response_complete"] or evidence["client_closed"]:
        return False
    outcome = "cancelled" if cancelled else "success"
    terminal = [a for a in spec["expected"].get("span_assertions", []) if a.get("name") and any(
        a.get("attributes", {}).get(key) == {"op": "equals", "value": outcome} for key in ("outcome", "business.outcome"))]
    if not terminal or any(code.startswith("BUSINESS_") for code in failures):
        return False
    # AI data-only SSE 没有 in-band complete；必须读回同一请求的三层真实完成证据。
    if {"ai", "ai-agent"} <= set(spec["expected"]["services"]):
        for service, name, key in (("ai", "ai.dialogue.stream", "business.outcome"),
                                   ("ai-agent", "ai.agent.execution", "outcome"), ("ai-agent", "ai.model.call", "outcome")):
            matched = [s for s in actual.values() if s.service == service and s.name == name and s.attrs.get(key) == outcome and not s.status_error]
            if len(matched) != 1:
                return False
            if name == "ai.model.call":
                joined = 0
                for log in logs:
                    if log_field(log, "event") != "ai.model.call" or log_field(log, "outcome") != outcome:
                        continue
                    try:
                        joined += log_identity(log) == (matched[0].trace, matched[0].span)
                    except AcceptanceError:
                        return False
                if joined != 1:
                    return False
    return True


def validate_spec(spec: dict) -> None:
    if not SAFE_NAME.fullmatch(spec.get("case", "")) or not SAFE_ROUTE.fullmatch(spec.get("route", "")):
        raise AcceptanceError("INVALID_SAFE_MANIFEST_METADATA")
    expected = spec.get("expected", {})
    services = expected.get("services", [])
    if not services or len(set(services)) != len(services) or not set(services) <= SERVICES or spec.get("entry_service") not in services:
        raise AcceptanceError("INVALID_EXPECTED_SERVICES")
    if not expected.get("statuses") or any(type(x) is not int or not 100 <= x <= 599 for x in expected["statuses"]):
        raise AcceptanceError("INVALID_HTTP_EXPECTATION")
    if not expected.get("json_assertions") and not expected.get("span_assertions") and not expected.get("negative"):
        raise AcceptanceError("BUSINESS_ASSERTION_REQUIRED")
    if expected.get("negative") and any(x < 400 for x in expected["statuses"]):
        raise AcceptanceError("NEGATIVE_SUCCESS_NOT_ALLOWED")
    allowed = {"nonempty_string", "nonempty_collection", "present", "absent", "true", "false", "positive_integer", "equals", "one_of"}
    for path in expected.get("paths", []):
        if not isinstance(path, list) or not 2 <= len(path) <= 12 or path[0] != spec["entry_service"] or not set(path) <= set(services):
            raise AcceptanceError("INVALID_EXPECTED_SERVICE_PATH")
    forbidden = expected.get("forbidden_services", [])
    if not isinstance(forbidden, list) or not set(forbidden) <= SERVICES or set(forbidden) & set(services):
        raise AcceptanceError("INVALID_FORBIDDEN_SERVICES")
    for assertion in expected.get("json_assertions", []):
        if assertion.get("op") not in allowed or not isinstance(assertion.get("path"), list):
            raise AcceptanceError("INVALID_ASSERTION")
    for item in expected.get("span_assertions", []):
        if item.get("service") not in services or not item.get("attributes"):
            raise AcceptanceError("INVALID_SPAN_ASSERTION")
        if any(condition.get("op") not in allowed for condition in item["attributes"].values()):
            raise AcceptanceError("INVALID_SPAN_ASSERTION")
    for item in expected.get("log_assertions", []):
        if item.get("service") not in services or item.get("field") not in ("request_body", "response_body", "request_body_omitted", "response_body_omitted", "request_body_complete", "response_body_complete", "outcome"):
            raise AcceptanceError("INVALID_LOG_ASSERTION")
        if not item.get("json_assertions") and item.get("op") not in allowed:
            raise AcceptanceError("INVALID_LOG_ASSERTION")
        for assertion in item.get("json_assertions", []):
            if assertion.get("op") not in allowed or not isinstance(assertion.get("path"), list):
                raise AcceptanceError("INVALID_LOG_ASSERTION")
    for item in expected.get("event_assertions", []):
        if item.get("service") not in services or item.get("event") != "ai.model.call" or type(item.get("count", 1)) is not int or not 1 <= item.get("count", 1) <= 20:
            raise AcceptanceError("INVALID_EVENT_ASSERTION")
        if any(not key.startswith("ai_") or condition.get("op") not in allowed for key, condition in item.get("fields", {}).items()):
            raise AcceptanceError("INVALID_EVENT_ASSERTION")
    for edge in expected.get("edges", []):
        if edge.get("parent") not in services or edge.get("child") not in services or edge.get("protocol") not in ("http", "dubbo"):
            raise AcceptanceError("INVALID_EXPECTED_EDGE")
    if len(services) > 1:
        reachable = {spec["entry_service"]}
        for _ in services:
            reachable.update(edge["child"] for edge in expected.get("edges", []) if edge["parent"] in reachable)
        if reachable != set(services):
            raise AcceptanceError("EXPECTED_EDGE_COVERAGE_REQUIRED")


def _protocol(span: Span, protocol: str) -> bool:
    if protocol == "dubbo":
        # OTel RPC 语义迁移：只识别旧/新 Dubbo 精确值；双发时两字段都必须一致归属 Dubbo。
        systems = [span.attrs[key] for key in ("rpc.system", "rpc.system.name") if key in span.attrs]
        return bool(systems) and all(type(value) is str and value in ("dubbo", "apache_dubbo") for value in systems)
    return protocol == "http" and ("http.request.method" in span.attrs or "http.method" in span.attrs)


def reconcile(spec: dict, record: dict, spans: list[Span], logs: list[dict], search_ids: set[str], forbidden_values: tuple[str, ...] = ()) -> dict:
    """每个请求独立 fail closed；缺一条 parent、一次终态或版本都不能用其它请求补齐。"""
    failures: set[str] = set()
    expected = spec["expected"]
    if not record.get("sent"):
        failures.add("REQUEST_NOT_SENT")
    if not record.get("completed"):
        failures.add("REQUEST_NOT_COMPLETED")
    if spec.get("response_mode") != "sse" and not record.get("business_ok"):
        failures.add("BUSINESS_ASSERTION_FAILED")
    trace = record["trace_id"]
    actual = {span.span: span for span in spans if span.trace == trace}
    if not actual:
        failures.add("TRACE_MISSING")
    if trace not in search_ids:
        failures.add("TRACE_SEARCH_MISSING")
    roots = [span for span in actual.values() if span.service == spec["entry_service"] and span.kind == 2 and span.parent == record["parent_span_id"]]
    if len(roots) != 1:
        failures.add("ENTRY_PARENT_MISMATCH")
    root_id = roots[0].span if len(roots) == 1 else None
    observed_services = {span.service for span in actual.values()}
    if not set(expected["services"]) <= observed_services:
        failures.add("EXPECTED_SERVICE_MISSING")
    if set(expected.get("forbidden_services", [])) & observed_services:
        failures.add("FORBIDDEN_SERVICE_OBSERVED")
    for span in actual.values():
        if span.status_error and not expected.get("negative"):
            failures.add("TRACE_ERROR_STATUS")
        if span.sampled is False:
            failures.add("UNSAMPLED_STORED_SPAN")
        if span.service not in SERVICES:
            failures.add("UNEXPECTED_SERVICE_IDENTITY")
        elif not SAFE_VERSION.fullmatch(span.version) or span.environment != expected.get("environment", "github-dev"):
            failures.add("RESOURCE_IDENTITY_MISMATCH")
        version = expected.get("versions", {}).get(span.service)
        if version is not None and span.version != version:
            failures.add("RESOURCE_VERSION_MISMATCH")
        seen: set[str] = set()
        cursor = span
        while root_id and cursor.span != root_id:
            if cursor.span in seen:
                failures.add("SPAN_PARENT_CYCLE")
                break
            seen.add(cursor.span)
            if cursor.parent not in actual:
                failures.add("SPAN_PARENT_MISSING")
                break
            cursor = actual[cursor.parent]
    edge_count = 0
    for edge in expected.get("edges", []):
        found = any(child.service == edge["child"] and child.kind == 2 and _protocol(child, edge["protocol"])
                    and child.parent in actual and actual[child.parent].service == edge["parent"]
                    and actual[child.parent].kind == 3 and _protocol(actual[child.parent], edge["protocol"])
                    for child in actual.values())
        edge_count += found
        if not found:
            failures.add("CROSS_SERVICE_PARENT_MISMATCH")
    # Card可指向gateway：必须证实 ai→第二个gateway→agent 的同一祖先链，
    # 不能用同Trace其它分支的gateway→agent边拼成不存在的路径。
    observed_paths = []
    for terminal in actual.values():
        if terminal.kind != 2:
            continue
        cursor, chain, visited = terminal, [], set()
        while cursor.span not in visited:
            visited.add(cursor.span)
            if not chain or chain[-1] != cursor.service:
                chain.append(cursor.service)
            if cursor.span == root_id:
                observed_paths.append(list(reversed(chain)))
                break
            if cursor.parent not in actual:
                break
            cursor = actual[cursor.parent]
    if any(path not in observed_paths for path in expected.get("paths", [])):
        failures.add("SERVICE_ANCESTRY_PATH_MISMATCH")
    for assertion in expected.get("span_assertions", []):
        if not any(span.service == assertion["service"] and (not assertion.get("name") or span.name == assertion["name"])
                   and all(matches(span.attrs.get(key), condition) for key, condition in assertion["attributes"].items())
                   and (assertion.get("allow_error") or not span.status_error) for span in actual.values()):
            failures.add("BUSINESS_SPAN_ASSERTION_FAILED")
    http_spans = [span for span in actual.values() if span.kind == 2 and _protocol(span, "http") and span.service in expected["services"]]
    if not http_spans:
        failures.add("HTTP_SERVER_SPAN_MISSING")
    http_ids = {span.span for span in http_spans}
    for log in logs:
        if log_field(log, "event") != "http.server.exchange":
            continue
        try:
            log_trace, log_span = log_identity(log)
        except AcceptanceError:
            continue
        if log_trace == trace and log_span not in http_ids:
            failures.add("HTTP_EVENT_SPAN_JOIN_FAILED")
    joined = 0
    asserted_services = set()
    for span in http_spans:
        matching = []
        for log in logs:
            if log_field(log, "event") != "http.server.exchange":
                continue
            try:
                same = log_identity(log) == (trace, span.span)
            except AcceptanceError:
                continue
            if same:
                matching.append(log)
        if len(matching) != 1:
            failures.add("HTTP_TERMINAL_LOG_MISSING" if not matching else "HTTP_TERMINAL_LOG_DUPLICATE")
            continue
        log = matching[0]
        try:
            if field(log, "TraceId") and identity(field(log, "TraceId"), 16) != trace:
                failures.add("LOG_OBSERVATION_ID_MISMATCH")
            if field(log, "SpanId") and identity(field(log, "SpanId"), 8) != span.span:
                failures.add("LOG_OBSERVATION_ID_MISMATCH")
        except AcceptanceError:
            failures.add("LOG_OBSERVATION_ID_MISMATCH")
        log_service = field(log, "Resource.service.name", log_field(log, "service.name"))
        log_version = field(log, "Resource.service.version", log_field(log, "service.version"))
        log_env = field(log, "Resource.deployment.environment.name", log_field(log, "deployment.environment.name"))
        if (log_service, log_version, log_env) != (span.service, span.version, span.environment):
            failures.add("LOG_RESOURCE_IDENTITY_MISMATCH")
        status = log_field(log, "http_status")
        duration = log_field(log, "duration_ms")
        if type(status) is not int or not 100 <= status <= 599 or type(duration) not in (int, float) or duration < 0:
            failures.add("HTTP_LOG_CONTRACT_INVALID")
        if span.span == root_id and status != record.get("http_status"):
            failures.add("HTTP_LOG_STATUS_MISMATCH")
        if log_field(log, "outcome") not in ("success", "failure", "cancelled", "timeout", "rejected"):
            failures.add("HTTP_LOG_CONTRACT_INVALID")
        for assertion in expected.get("log_assertions", []):
            if assertion["service"] != span.service:
                continue
            asserted_services.add(span.service)
            value = log_field(log, assertion["field"])
            if "json_assertions" in assertion:
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    failures.add("LOG_PROJECTION_ASSERTION_FAILED")
                    continue
                if not all(matches(lookup(value, item["path"]), item) for item in assertion["json_assertions"]):
                    failures.add("LOG_PROJECTION_ASSERTION_FAILED")
            elif not matches(value, assertion):
                failures.add("LOG_PROJECTION_ASSERTION_FAILED")
        joined += 1
    if any(item["service"] not in asserted_services for item in expected.get("log_assertions", [])):
        failures.add("LOG_PROJECTION_ASSERTION_FAILED")
    for assertion in expected.get("event_assertions", []):
        matching = []
        for log in logs:
            if log_field(log, "event") != assertion["event"] or field(log, "Resource.service.name", log_field(log, "service.name")) != assertion["service"]:
                continue
            try:
                log_trace, log_span = log_identity(log)
            except AcceptanceError:
                continue
            if log_trace == trace:
                matching.append(log)
                if log_span not in actual or actual[log_span].service != assertion["service"]:
                    failures.add("BUSINESS_EVENT_SPAN_JOIN_FAILED")
                else:
                    stored = actual[log_span]
                    if (field(log, "Resource.service.version", log_field(log, "service.version")) != stored.version
                            or field(log, "Resource.deployment.environment.name", log_field(log, "deployment.environment.name")) != stored.environment):
                        failures.add("LOG_RESOURCE_IDENTITY_MISMATCH")
        if len(matching) != assertion.get("count", 1):
            failures.add("BUSINESS_EVENT_COUNT_MISMATCH")
        for log in matching:
            if not all(matches(log_field(log, key), condition) for key, condition in assertion.get("fields", {}).items()):
                failures.add("BUSINESS_EVENT_ASSERTION_FAILED")
    if forbidden_values and any(needle in json.dumps(log, ensure_ascii=False) for needle in forbidden_values for log in logs):
        failures.add("SYNTHETIC_SENTINEL_LEAK")
    business_ok = record.get("business_ok")
    if spec.get("response_mode") == "sse":
        business_ok = sse_business_confirmed(spec, record, actual, logs, failures)
        if not business_ok:
            failures.add("BUSINESS_ASSERTION_FAILED")
    identities = sorted({(s.service, s.version) for s in actual.values() if s.service in SERVICES and SAFE_VERSION.fullmatch(s.version)})
    return {
        **{key: record.get(key) for key in ("request_id", "case", "trace_id", "parent_span_id", "sampled", "sent", "completed", "http_status", "business_ok", "client_cancelled")},
        "business_ok": business_ok,
        **({"sse_evidence": record["sse_evidence"]} if valid_sse_evidence(record.get("sse_evidence")) else {}),
        "route": spec["route"], "service_versions": [{"service": service, "version": version} for service, version in identities],
        "span_count": len(actual), "edges_expected": len(expected.get("edges", [])), "edges_joined": edge_count,
        "http_spans": len(http_spans), "terminal_logs_joined": joined,
        "passed": not failures, "failures": sorted(failures),
    }
