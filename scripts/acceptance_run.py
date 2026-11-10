#!/usr/bin/env python3
"""显式执行私有请求清单并逐项读回 Jaeger v3 / ES；默认仅生成计划。

用法：python3 scripts/acceptance_run.py --config /secure/run.json [--execute]
所有 URL、认证引用、实际路径和正文只来自 Git 外 0600 JSON；报告不保存这些内容。
Jaeger 合同：https://github.com/jaegertracing/jaeger-idl/blob/main/proto/api_v3/query_service.proto
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import http.client
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from acceptance_core import (AcceptanceError, business_result, decode_stream, identity, parse_logs, parse_traces,
                             reconcile, sse_evidence, valid_sse_evidence, validate_spec)


ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE = 32 * 1024 * 1024


def private_bytes(path: str | Path) -> bytes:
    """不执行 env/shell，不跟随最终 symlink，不接受任一 Git 工作树内文件。"""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise AcceptanceError("PRIVATE_REFERENCE_NOT_ABSOLUTE")
    try:
        with os.fdopen(os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise AcceptanceError("PRIVATE_FILE_OWNER_MODE")
            git = subprocess.run(["git", "-C", str(candidate.resolve().parent), "rev-parse", "--show-toplevel"], capture_output=True, timeout=5)
            if git.returncode == 0:
                raise AcceptanceError("PRIVATE_FILE_INSIDE_GIT")
            raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AcceptanceError("PRIVATE_FILE_TOO_LARGE")
            return raw
    except AcceptanceError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise AcceptanceError("PRIVATE_FILE_INVALID") from None


def private_json(path: str | Path) -> object:
    try:
        return json.loads(private_bytes(path))
    except (ValueError, UnicodeError):
        raise AcceptanceError("PRIVATE_JSON_INVALID") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def headers_for(config: dict) -> dict[str, str]:
    reference = config.get("auth_file")
    if reference is None:
        return {}
    auth = private_json(reference)
    if not isinstance(auth, dict):
        raise AcceptanceError("AUTH_REFERENCE_SCHEMA")
    mode = config.get("auth_mode", "session")
    if mode == "session":
        payload = auth.get("data", auth)
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or any(ch in token for ch in "\r\n"):
            raise AcceptanceError("SESSION_TOKEN_MISSING")
        return {"Authorization": "Bearer " + token}
    if mode == "basic":
        username, password = auth.get("username"), auth.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or ":" in username:
            raise AcceptanceError("BASIC_REFERENCE_SCHEMA")
        value = base64.b64encode((username + ":" + password).encode()).decode()
        return {"Authorization": "Basic " + value}
    if mode == "bearer":
        token = auth.get("token")
        if not isinstance(token, str) or not token or any(ch in token for ch in "\r\n"):
            raise AcceptanceError("BEARER_REFERENCE_SCHEMA")
        return {"Authorization": "Bearer " + token}
    raise AcceptanceError("AUTH_MODE_INVALID")


def checked_base(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise AcceptanceError("ENDPOINT_INVALID")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AcceptanceError("ENDPOINT_INVALID")
    return value.rstrip("/")


def fetch(url: str, *, method: str = "GET", headers: dict | None = None, body: bytes | None = None, timeout: float = 15,
          cancel_after_bytes: int | None = None, response_state: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        try:
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if cancel_after_bytes:
                chunks, remaining, eof = [], cancel_after_bytes, False
                while remaining:
                    chunk = response.read1(remaining)
                    if not chunk:
                        eof = True
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                eof = eof or response.length == 0 or response.isclosed()
            else:
                raw, eof = response.read(MAX_RESPONSE + 1), True
            if response_state is not None:
                response_state.update(response_complete=eof, client_closed=bool(cancel_after_bytes and not eof))
            if len(raw) > MAX_RESPONSE:
                raise AcceptanceError("RESPONSE_TOO_LARGE")
            return response.code, raw
    except AcceptanceError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise AcceptanceError("TRANSPORT_FAILED") from None


def rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Backend:
    def __init__(self, config: dict):
        self.jaeger = checked_base(config["jaeger"]["base_url"])
        self.es = checked_base(config["elasticsearch"]["base_url"])
        self.jaeger_headers = headers_for(config["jaeger"])
        self.es_headers = {"Content-Type": "application/json", **headers_for(config["elasticsearch"])}
        self.forbidden = tuple(private_json(config["forbidden_values_file"])) if config.get("forbidden_values_file") else ()

    def _traces(self, raw: bytes):
        if self.forbidden and any(needle in json.dumps(decode_stream(raw), ensure_ascii=False) for needle in self.forbidden):
            raise AcceptanceError("SYNTHETIC_SENTINEL_LEAK")
        try:
            return parse_traces(raw)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise AcceptanceError("BACKEND_TRACE_SCHEMA") from None

    def trace(self, trace_id: str):
        status, raw = fetch(self.jaeger + "/api/v3/traces/" + trace_id + "?rawTraces=true", headers=self.jaeger_headers)
        if status == 404:
            return []
        if status != 200:
            raise AcceptanceError("TRACE_READBACK_HTTP_ERROR")
        return self._traces(raw)

    def search(self, service: str, start: datetime, end: datetime) -> set[str]:
        # FindTraces 的 query 使用 protobuf JSON 的 camelCase；不是旧 /api/traces。
        query = urllib.parse.urlencode({"query.serviceName": service, "query.startTimeMin": rfc3339(start),
                                       "query.startTimeMax": rfc3339(end), "query.searchDepth": "1000", "query.rawTraces": "true"})
        status, raw = fetch(self.jaeger + "/api/v3/traces?" + query, headers=self.jaeger_headers)
        if status != 200:
            raise AcceptanceError("TRACE_SEARCH_HTTP_ERROR")
        return {span.trace for span in self._traces(raw)}

    def logs(self, trace_ids: list[str], start: datetime, end: datetime) -> list[dict]:
        query = {"size": 10000, "track_total_hits": True, "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": rfc3339(start), "lte": rfc3339(end)}}},
            {"bool": {"should": [{"terms": {key: trace_ids}} for key in ("trace_id", "TraceId", "Attributes.trace_id")], "minimum_should_match": 1}},
        ]}}}
        # Grafana datasource proxy 同样支持 GET body；不需要开放 ES 写入接口。
        status, raw = fetch(self.es + "/_search", method="GET", headers=self.es_headers, body=json.dumps(query).encode())
        if status != 200:
            raise AcceptanceError("LOG_READBACK_HTTP_ERROR")
        return parse_logs(raw)


def request_accept(spec: dict) -> str:
    """请求媒体类型与响应断言分开，负例可请求 SSE 入口并判读空 HTTP 错误响应。"""
    value = spec.get("accept", "text/event-stream" if spec.get("response_mode") == "sse" else "application/json")
    if type(value) is not str or value not in ("application/json", "text/event-stream"):
        raise AcceptanceError("REQUEST_ACCEPT_INVALID")
    return value


def load_config(path: str) -> dict:
    config = private_json(path)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise AcceptanceError("CONFIG_SCHEMA_INVALID")
    requests = config.get("requests")
    if not isinstance(requests, list) or not 1 <= len(requests) <= 100:
        raise AcceptanceError("BOUNDED_REQUESTS_REQUIRED")
    if len({item.get("case") for item in requests}) != len(requests):
        raise AcceptanceError("DUPLICATE_CASE")
    checked_base(config["business"]["base_url"])
    for spec in requests:
        validate_spec(spec)
        if spec.get("method") not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            raise AcceptanceError("METHOD_INVALID")
        path_value = spec.get("path", "")
        if not isinstance(path_value, str) or not path_value.startswith("/") or path_value.startswith("//") or "#" in path_value or any(ord(c) < 32 for c in path_value):
            raise AcceptanceError("REQUEST_PATH_INVALID")
        if spec.get("sampled", 1) not in (0, 1):
            raise AcceptanceError("SAMPLED_FLAG_INVALID")
        if spec.get("auth", "configured") not in ("configured", "none", "wrong"):
            raise AcceptanceError("REQUEST_AUTH_INVALID")
        if spec.get("response_mode", "json") not in ("json", "sse"):
            raise AcceptanceError("RESPONSE_MODE_INVALID")
        request_accept(spec)
        if spec.get("cancel_after_bytes") is not None:
            count = spec["cancel_after_bytes"]
            if type(count) is not int or not 1 <= count <= 8192 or not spec["expected"].get("span_assertions"):
                raise AcceptanceError("CANCELLATION_EXPECTATION_REQUIRED")
        if spec.get("payload_file"):
            private_bytes(spec["payload_file"])
    for key, default, lower, upper in (("concurrency", 1, 1, 8), ("settle_seconds", 60, 0, 180), ("timeout_seconds", 30, 1, 120)):
        value = config.get(key, default)
        if type(value) not in (int, float) or not lower <= value <= upper:
            raise AcceptanceError("BOUNDED_EXECUTION_REQUIRED")
    Backend(config)  # 计划阶段同样验证后端与凭据引用；不发网络请求。
    headers_for(config["business"])
    if config.get("forbidden_values_file"):
        values = private_json(config["forbidden_values_file"])
        if not isinstance(values, list) or not 1 <= len(values) <= 100 or any(not isinstance(v, str) or len(v) < 8 or len(v) > 256 for v in values):
            raise AcceptanceError("SYNTHETIC_SENTINEL_SCHEMA")
    return config


def manifest(config: dict) -> list[dict]:
    return [{"request_id": f"request-{i + 1:03d}", "case": item["case"], "route": item["route"],
             "trace_id": secrets.token_hex(16), "parent_span_id": secrets.token_hex(8),
             "sampled": item.get("sampled", 1), "sent": False, "completed": False, "business_ok": False}
            for i, item in enumerate(config["requests"])]


def send_one(config: dict, spec: dict, record: dict) -> dict:
    headers = {"Accept": request_accept(spec), **headers_for(config["business"])}
    if spec.get("auth") == "none":
        headers.pop("Authorization", None)
    elif spec.get("auth") == "wrong":
        headers["Authorization"] = "Bearer acceptance-invalid-token"
    headers["traceparent"] = f"00-{record['trace_id']}-{record['parent_span_id']}-0{record['sampled']}"
    payload = None
    if spec.get("payload_file"):
        # 原样透传私有有界测试体，才能覆盖重复键/无效 JSON/超限体；从不打印。
        payload = private_bytes(spec["payload_file"])
        headers["Content-Type"] = "application/json"
    record = {**record, "sent": True}
    try:
        transport = {}
        status, raw = fetch(checked_base(config["business"]["base_url"]) + spec["path"], method=spec["method"],
                            headers=headers, body=payload, timeout=config.get("timeout_seconds", 30),
                            cancel_after_bytes=spec.get("cancel_after_bytes"), response_state=transport)
        record.update(completed=True, http_status=status, business_ok=False)
        if spec.get("response_mode") == "sse":
            evidence = sse_evidence(raw, transport)
            record["sse_evidence"] = evidence
            record["client_cancelled"] = evidence["client_closed"] and not evidence["explicit_error"] and not evidence["explicit_complete"]
        else:
            record["business_ok"] = business_result(raw, status, spec["expected"])
    except AcceptanceError as error:
        record["transport_reason"] = str(error)
    return record


def readback(config: dict, records: list[dict], start: datetime, backend=None) -> dict:
    if (len(records) != len(config["requests"]) or len({r["trace_id"] for r in records}) != len(records)
            or any(record.get("case") != spec["case"] for spec, record in zip(config["requests"], records))):
        return {"schema_version": 1, "mode": "executed", "planned": len(config["requests"]),
                "sent": sum(bool(r.get("sent")) for r in records), "completed": sum(bool(r.get("completed")) for r in records),
                "passed_count": 0, "passed": False, "backend_failures": ["REQUEST_LEDGER_MISMATCH"], "requests": []}
    backend = backend or Backend(config)
    forbidden = tuple(private_json(config["forbidden_values_file"])) if config.get("forbidden_values_file") else ()
    deadline = time.monotonic() + config.get("settle_seconds", 60)
    results, backend_errors = [], set()
    while True:
        end = datetime.now(timezone.utc) + timedelta(seconds=2)
        spans, logs, search_ids = [], [], set()
        iteration_errors = set()
        for record in records:
            try:
                spans.extend(backend.trace(record["trace_id"]))
            except AcceptanceError as error:
                iteration_errors.add(str(error))
        for service in sorted({spec["entry_service"] for spec in config["requests"]}):
            try:
                search_ids.update(backend.search(service, start - timedelta(seconds=2), end))
            except AcceptanceError as error:
                iteration_errors.add(str(error))
        try:
            logs = backend.logs([record["trace_id"] for record in records], start - timedelta(seconds=2), end)
        except AcceptanceError as error:
            iteration_errors.add(str(error))
        results = [reconcile(spec, record, spans, logs, search_ids, forbidden) for spec, record in zip(config["requests"], records)]
        if not iteration_errors and all(result["passed"] for result in results):
            backend_errors = set()
            break
        backend_errors = iteration_errors
        if time.monotonic() >= deadline:
            break
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return {"schema_version": 1, "mode": "executed", "planned": len(config["requests"]),
            "sent": sum(bool(r.get("sent")) for r in records), "completed": sum(bool(r.get("completed")) for r in records),
            "passed_count": sum(bool(r["passed"]) for r in results), "passed": not backend_errors and all(r["passed"] for r in results),
            "backend_failures": sorted(backend_errors), "requests": results}


def safe_write(path: Path, content: str) -> None:
    path = path.absolute()
    build = (ROOT / "build").resolve()
    if not path.resolve().is_relative_to(build) or path.is_symlink():
        raise AcceptanceError("REPORT_MUST_BE_IN_BUILD")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".acceptance-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def render(report: dict) -> str:
    lines = ["# 全链观测验收报告", "", f"模式：`{report['mode']}`；实际验收通过：`{str(report.get('passed', False)).lower()}`。", "",
             f"计划 {report['planned']}；发送 {report.get('sent', 0)}；响应完成 {report.get('completed', 0)}；完整对账通过 {report.get('passed_count', 0)}。", "",
             "| 请求 | 类型 | Trace | 状态 | Span | 跨服务边 | HTTP终态join | 失败原因 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in report["requests"]:
        lines.append(f"| {item['request_id']} | {item['case']} | `{item['trace_id']}` | {item.get('http_status', '未执行')} | {item.get('span_count', 0)} | {item.get('edges_joined', 0)}/{item.get('edges_expected', 0)} | {item.get('terminal_logs_joined', 0)}/{item.get('http_spans', 0)} | {', '.join(item.get('failures', [])) or ('通过' if item.get('passed') else '未执行')} |")
    lines.extend(["", "后端原因码：" + (", ".join(report.get("backend_failures", [])) or "无"), "",
                  "此报告仅包含请求编号、合成类型、路由模板、观测身份与计数。请求/响应正文、认证值、实际资源身份和模型回包不保存。",
                  "HTTP成功、exporter响应或单服务探针不等于全链通过。仅对本次有界清单逐项对账，不能保证未来网络/进程故障下永久零丢失。", ""])
    return "\n".join(lines)


def read_ledger(path: Path, config: dict) -> tuple[list[dict], datetime]:
    """仅重读安全 ledger，不重复模型或业务请求；拒绝其它路径、权限和清单错配。"""
    if not path.resolve().is_relative_to((ROOT / "build").resolve()) or path.is_symlink():
        raise AcceptanceError("LEDGER_MUST_BE_IN_BUILD")
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 2 * 1024 * 1024:
                raise AcceptanceError("LEDGER_OWNER_MODE")
            report = json.load(handle)
        start = datetime.fromisoformat(report["started_at"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise AcceptanceError("LEDGER_TIMESTAMP_INVALID")
        records = report["requests"]
        if len(records) != len(config["requests"]):
            raise AcceptanceError("REQUEST_LEDGER_MISMATCH")
        allowed = ("request_id", "case", "trace_id", "parent_span_id", "sampled", "sent", "completed", "http_status", "business_ok", "client_cancelled", "sse_evidence")
        result = []
        for index, (record, spec) in enumerate(zip(records, config["requests"]), 1):
            if record.get("case") != spec["case"] or record.get("request_id") != f"request-{index:03d}":
                raise AcceptanceError("REQUEST_LEDGER_MISMATCH")
            trace_id = identity(record["trace_id"], 16)
            parent_id = identity(record["parent_span_id"], 8)
            if any(type(record.get(key)) is not bool for key in ("sent", "completed", "business_ok")):
                raise AcceptanceError("LEDGER_SCHEMA_INVALID")
            if type(record.get("sampled")) is not int or record["sampled"] not in (0, 1):
                raise AcceptanceError("LEDGER_SCHEMA_INVALID")
            if record.get("client_cancelled") is not None and type(record["client_cancelled"]) is not bool:
                raise AcceptanceError("LEDGER_SCHEMA_INVALID")
            if record.get("sse_evidence") is not None and not valid_sse_evidence(record["sse_evidence"]):
                raise AcceptanceError("LEDGER_SCHEMA_INVALID")
            status = record.get("http_status")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise AcceptanceError("LEDGER_SCHEMA_INVALID")
            result.append({**{key: record.get(key) for key in allowed}, "trace_id": trace_id, "parent_span_id": parent_id})
        return result, start
    except AcceptanceError:
        raise
    except (OSError, KeyError, ValueError, TypeError):
        raise AcceptanceError("LEDGER_SCHEMA_INVALID") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true", help="显式发送清单；默认只计划")
    parser.add_argument("--readback-ledger", type=Path, help="重读已有安全清单，只查持久化证据、不重发请求")
    parser.add_argument("--report", type=Path, default=ROOT / "build/observability-acceptance-report.md")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.execute and args.readback_ledger:
            raise AcceptanceError("EXECUTION_MODE_CONFLICT")
        if args.readback_ledger:
            records, start = read_ledger(args.readback_ledger, config)
            report = readback(config, records, start)
            report["started_at"] = rfc3339(start)
            safe_write(args.report.with_suffix(".json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            safe_write(args.report, render(report))
            print(json.dumps({key: report.get(key) for key in ("mode", "planned", "sent", "completed", "passed_count", "passed")}, ensure_ascii=False))
            return 0 if report["passed"] else 1
        records = manifest(config)
        start = datetime.now(timezone.utc)
        report = {"schema_version": 1, "mode": "planned", "started_at": rfc3339(start), "planned": len(records), "passed": False, "requests": records}
        # 先保存 N 项未发送清单；进程崩溃不能把已丢的请求当作成功。
        ledger = args.report.with_suffix(".json")
        safe_write(ledger, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        safe_write(args.report, render(report))
        if args.execute:
            with ThreadPoolExecutor(max_workers=int(config.get("concurrency", 1))) as executor:
                futures = [executor.submit(send_one, config, spec, record) for spec, record in zip(config["requests"], records)]
                for index, future in enumerate(futures):
                    records[index] = future.result()
                    report["requests"] = records
                    safe_write(ledger, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            report = readback(config, records, start)
            report["started_at"] = rfc3339(start)
            safe_write(ledger, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            safe_write(args.report, render(report))
        print(json.dumps({key: report.get(key) for key in ("mode", "planned", "sent", "completed", "passed_count", "passed")}, ensure_ascii=False))
        return 0 if not args.execute or report["passed"] else 1
    except (AcceptanceError, KeyError, TypeError, ValueError) as error:
        reason = str(error) if isinstance(error, AcceptanceError) else "CONFIG_SCHEMA_INVALID"
        print(json.dumps({"passed": False, "reason_code": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
