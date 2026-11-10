#!/usr/bin/env python3
"""Conductor 原生 SIMPLE worker 演示；默认 plan，无真实模型或文档调用。"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/examples/conductor_document_demo.json"
WORKFLOW = "fleet_demo_document_analysis_v1"
SCHEDULE = "fleet_demo_document_utc_once_20261110"
DOMAIN = "fleet_demo_document_20261110"
REFERENCES = {"validate": "validate_metadata", "retrieve": "retrieve_context",
              "ai": "analyze_document", "record": "register_result"}
TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "TERMINATED"}


class DemoError(RuntimeError):
    """仅包含固定错误码和 HTTP 状态；不保留原始响应、地址或凭据。"""
    def __init__(self, code, status=None, operation=None):
        super().__init__(code)
        self.status = status
        self.operation = operation


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def segment(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise DemoError("IDENTIFIER_INVALID")
    return value


def private_path(path):
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise DemoError("PRIVATE_PATH_INVALID")
    existing = candidate.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    result = subprocess.run(["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
                            capture_output=True, timeout=5)
    if result.returncode == 0:
        raise DemoError("PRIVATE_PATH_INSIDE_GIT")
    return candidate


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Basic 身份只发给显式私网 origin；不跟随重定向，也不经环境代理转交凭据。"""
    def __init__(self, base_url, credentials):
        parsed = urllib.parse.urlsplit(base_url)
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except (ValueError, TypeError):
            raise DemoError("PRIVATE_ORIGIN_REQUIRED") from None
        if (parsed.scheme not in {"http", "https"} or not (address.is_private or address.is_loopback)
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise DemoError("PRIVATE_ORIGIN_REQUIRED")
        path = private_path(credentials)
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "r") as handle:
                info = os.fstat(handle.fileno())
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
                    raise DemoError("CREDENTIAL_OWNER_MODE")
                data = json.load(handle)
            username, password = data["ui_username"], data["ui_password"]
            if (not isinstance(username, str) or not isinstance(password, str) or not username or not password
                    or ":" in username or any(c in username + password for c in "\r\n")):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise DemoError("CREDENTIAL_INVALID") from None
        self.base = base_url.rstrip("/")
        self.auth = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, payload=None, text=None, timeout=5):
        if not path.startswith("/api/") or "\r" in path or "\n" in path:
            raise DemoError("API_PATH_INVALID")
        body = canonical(payload).encode() if payload is not None else text.encode() if text is not None else None
        headers = {"Authorization": self.auth, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "text/plain; charset=UTF-8" if text is not None else "application/json"
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        operation = method + " " + ("/api/tasks/poll/batch/{taskType}" if "/poll/batch/" in path
                                    else "/api/tasks/{taskId}/log" if path.endswith("/log")
                                    else "/api/workflow/{workflowId}" if path.startswith("/api/workflow/")
                                    else "/api/tasks/{taskId}" if path.startswith("/api/tasks/")
                                    else path.split("?", 1)[0])
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise DemoError("RESPONSE_TOO_LARGE")
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError):
                    return raw.decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise DemoError("HTTP_REJECTED", status, operation) from None
        except (OSError, ValueError, http.client.HTTPException):
            raise DemoError("TRANSPORT_UNKNOWN", operation=operation) from None


class Ledger:
    """本机私有启动意图、任务回执和唯一登记；同键不同结果必须拒绝。"""
    def __init__(self, path):
        path = private_path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.stat().st_uid != os.getuid() or stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise DemoError("LEDGER_DIRECTORY_OWNER_MODE")
        with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise DemoError("LEDGER_OWNER_MODE")
            new = info.st_size == 0
        self.db = sqlite3.connect(path, timeout=5)
        if not new and self.db.execute("PRAGMA application_id").fetchone()[0] != 1128549711:
            self.db.close()
            raise DemoError("LEDGER_IDENTITY_INVALID")
        with self.db:
            self.db.execute("PRAGMA application_id=1128549711")
            self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, sha TEXT NOT NULL, value TEXT NOT NULL)")

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, canonical(value)))

    def runs(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT value FROM state WHERE key LIKE 'run:%'")]

    def reserve(self, request, source):
        key = "run:" + request["correlationId"]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.get(key)
            if prior:
                if prior["requestSha"] != digest(request) or prior["source"] != source:
                    raise DemoError("RUN_IDEMPOTENCY_CONFLICT")
                return prior, False
            if len(self.runs()) >= 3:
                raise DemoError("MAXIMUM_THREE_RUNS")
            value = {"request": request, "requestSha": digest(request), "source": source,
                     "workflowId": None, "state": "INTENT"}
            self.db.execute("INSERT INTO state VALUES (?,?)", (key, canonical(value)))
            return value, True
        finally:
            self.db.commit()

    def record(self, key, value):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT sha,value FROM records WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] != digest(value):
                    raise DemoError("RECORD_IDEMPOTENCY_CONFLICT")
                return json.loads(row[1])
            result = {"recordId": "demo-record-" + digest([key, value])[:20], "simulated": True}
            self.db.execute("INSERT INTO records VALUES (?,?,?)", (key, digest(value), canonical(result)))
            return result
        finally:
            self.db.commit()

    def record_count(self):
        return self.db.execute("SELECT count(*) FROM records").fetchone()[0]


def matches(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and matches(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(matches(a, b) for a, b in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


class Driver:
    def __init__(self, api, ledger):
        self.api, self.ledger = api, ledger

    def request_for(self, run_key, scenario, wait_seconds):
        if not segment(run_key).startswith("fleet_demo_document_") or scenario not in {"retry_success", "terminal_failure"}:
            raise DemoError("RUN_INPUT_INVALID")
        if type(wait_seconds) is not int or not 1 <= wait_seconds <= 60:
            raise DemoError("WAIT_DURATION_INVALID")
        key = "demo-idempotency-" + digest(run_key)[:24]
        return {"name": WORKFLOW, "version": 1, "correlationId": run_key, "idempotencyKey": key,
                "idempotencyStrategy": "RETURN_EXISTING", "taskToDomain": {"*": DOMAIN},
                "input": {"runId": run_key, "idempotencyKey": key, "scenario": scenario, "simulated": True,
                          "waitDuration": str(wait_seconds) + "s",
                          "document": {"id": "demo-document-001", "mimeType": "text/plain", "bytes": 128}}}

    def correlated(self, request):
        rows = self.api.request("GET", f"/api/workflow/{WORKFLOW}/correlated/{segment(request['correlationId'])}"
                                "?includeClosed=true&includeTasks=true")
        if not isinstance(rows, list) or len(rows) > 1:
            raise DemoError("CORRELATION_NOT_UNIQUE")
        if rows and (rows[0].get("correlationId") != request["correlationId"]
                     or not matches(request["input"], rows[0].get("input"))):
            raise DemoError("CORRELATION_INPUT_CONFLICT")
        return rows

    def adopt(self, run):
        rows = self.correlated(run["request"])
        if not rows:
            return None
        run.update(workflowId=segment(rows[0]["workflowId"]), state="KNOWN")
        self.ledger.put("run:" + run["request"]["correlationId"], run)
        return {"workflowId": run["workflowId"], "adopted": True}

    def start(self, run_key, scenario="retry_success", wait_seconds=5):
        request = self.request_for(run_key, scenario, wait_seconds)
        run, fresh = self.ledger.reserve(request, "direct")
        adopted = self.adopt(run)
        if adopted:
            return adopted
        if not fresh:
            raise DemoError("START_OUTCOME_UNKNOWN")
        # 意图已持久化；从此不论响应是否收到，该 correlation 都不再自动 POST。
        try:
            workflow_id = self.api.request("POST", "/api/workflow", request, timeout=15)
            run.update(workflowId=segment(workflow_id), state="KNOWN")
            self.ledger.put("run:" + run_key, run)
            return {"workflowId": workflow_id, "adopted": False}
        except DemoError:
            adopted = self.adopt(run)
            if adopted:
                return adopted
            raise DemoError("START_OUTCOME_UNKNOWN") from None

    def retry_timed_out(self, run_key):
        run = self.ledger.get("run:" + segment(run_key))
        if not run or not run.get("workflowId"):
            raise DemoError("OWNED_WORKFLOW_REQUIRED")
        workflow_id = segment(run["workflowId"])
        path = "/api/workflow/" + workflow_id
        workflow = self.api.request("GET", path + "?includeTasks=true")
        key = "recovery:" + workflow_id
        prior = self.ledger.get(key)
        if prior:
            if workflow.get("status") in {"RUNNING", "COMPLETED"}:
                return {"workflowId": workflow_id, "recovery": "READBACK_ONLY", "status": workflow["status"]}
            raise DemoError("RECOVERY_OUTCOME_UNKNOWN")
        if workflow.get("status") != "TIMED_OUT":
            raise DemoError("KNOWN_TIMEOUT_REQUIRED")
        evidence = {"originalStatus": "TIMED_OUT", "originalTasks": [
            {"taskId": task["taskId"], "status": task["status"]} for task in workflow.get("tasks", [])], "state": "INTENT"}
        self.ledger.put(key, evidence)
        try:
            self.api.request("POST", path + "/retry", {}, timeout=15)
        except DemoError:
            pass
        current = self.api.request("GET", path + "?includeTasks=true")
        if current.get("status") not in {"RUNNING", "COMPLETED"}:
            raise DemoError("RECOVERY_OUTCOME_UNKNOWN")
        evidence["state"] = "KNOWN"
        self.ledger.put(key, evidence)
        return {"workflowId": workflow_id, "recovery": "EXPLICIT_SINGLE_RETRY", "status": current["status"]}

    def register(self):
        spec = json.loads(SPEC.read_text())
        for definition in spec["taskDefinitions"] + [spec["workflow"]]:
            workflow = definition["name"] == WORKFLOW
            path = "/api/metadata/workflow/" + WORKFLOW + "?version=1" if workflow else "/api/metadata/taskdefs/" + definition["name"]
            try:
                current = self.api.request("GET", path)
            except DemoError as error:
                if error.status != 404:
                    raise
                current = None
            if current is not None:
                if not matches(definition, current):
                    raise DemoError("EXISTING_DEFINITION_DIFFERS")
                continue
            marker = "definition:" + definition["name"]
            if self.ledger.get(marker):
                raise DemoError("DEFINITION_OUTCOME_UNKNOWN")
            self.ledger.put(marker, {"sha": digest(definition), "state": "INTENT"})
            target = "/api/metadata/workflow" if workflow else "/api/metadata/taskdefs"
            try:
                self.api.request("POST", target, definition if workflow else [definition])
            except DemoError:
                pass
            if not matches(definition, self.api.request("GET", path)):
                raise DemoError("DEFINITION_READBACK_FAILED")
            self.ledger.put(marker, {"sha": digest(definition), "state": "KNOWN"})
        return {"workflow": WORKFLOW, "taskDefinitions": 4, "registered": True}

    def event(self, task_id, stage, phase, attempt=0, source="worker"):
        segment(task_id)
        if (stage not in {*REFERENCES, "wait"} or phase not in {"start", "retry", "success", "failure"}
                or source not in {"worker", "conductor_status_observer"} or type(attempt) is not int or not 0 <= attempt <= 1):
            raise DemoError("LOG_EVENT_INVALID")
        event_id = digest([task_id, phase])[:24]
        event = {"eventId": event_id, "stage": stage, "phase": phase, "attempt": attempt,
                 "simulated": True, "provider": "stub", "source": source,
                 "message": {"start": "实际开始", "retry": "原生重试开始", "success": "实际完成", "failure": "受控失败已回执"}[phase]}
        if stage == "wait":
            event["message"] = "观察到 Conductor WAIT " + ("执行中" if phase == "start" else "已完成")
        key = "event:" + event_id
        prior = self.ledger.get(key)
        if prior and prior.get("state") == "CONFIRMED":
            return
        path = f"/api/tasks/{task_id}/log"
        def readback():
            entries = self.api.request("GET", path) or []
            for entry in entries:
                try:
                    value = json.loads(entry["log"])
                except (KeyError, ValueError, TypeError):
                    continue
                if isinstance(value, dict) and value.get("eventId") == event_id:
                    if value != event:
                        raise DemoError("LOG_EVENT_CONFLICT")
                    self.ledger.put(key, {"state": "CONFIRMED"})
                    return True
            return False
        if prior:
            if readback():
                return
            raise DemoError("TASK_LOG_OUTCOME_UNKNOWN")
        self.ledger.put(key, {"state": "INTENT"})
        try:
            self.api.request("POST", path, text=canonical(event))
        except DemoError:
            if readback():
                return
            raise DemoError("TASK_LOG_OUTCOME_UNKNOWN") from None
        self.ledger.put(key, {"state": "CONFIRMED"})

    def schedule_executions(self):
        # 3.33.0 SchedulerSearchQuery 接受 IN 子句；SQL式等号/单引号会被当作完整字面日程名。
        query = urllib.parse.urlencode({"query": "scheduleName IN (" + SCHEDULE + ")", "size": 2})
        result = self.api.request("GET", "/api/scheduler/search/executions?" + query)
        if (not isinstance(result, dict) or not isinstance(result.get("results"), list)
                or type(result.get("totalHits")) is not int or result["totalHits"] < 0):
            raise DemoError("SCHEDULE_HISTORY_INVALID")
        if result["totalHits"] > 1:
            raise DemoError("SCHEDULE_MORE_THAN_ONE_EXECUTION")
        rows = result["results"]
        if len(rows) != result["totalHits"]:
            raise DemoError("SCHEDULE_HISTORY_TRUNCATED")
        if not all(isinstance(row, dict) and row.get("scheduleName") == SCHEDULE for row in rows):
            raise DemoError("SCHEDULE_HISTORY_INVALID")
        return rows

    def schedule_once(self, run_key, when, max_seconds=90, repair_unfired=False):
        if when.tzinfo is None or not 10 <= (when - datetime.now(timezone.utc)).total_seconds() <= 180:
            raise DemoError("SCHEDULE_WINDOW_INVALID")
        when = when.astimezone(timezone.utc).replace(microsecond=0)
        request = self.request_for(run_key, "retry_success", 5)
        definition = {"name": SCHEDULE, "description": "文档模拟业务流：UTC 单次测试，执行后暂停，无真实模型调用",
                      "cronExpression": when.strftime("%S %M %H %d %m *"), "zoneId": "UTC",
                      "runCatchupScheduleInstances": False, "paused": True,
                      "scheduleStartTime": int((when - timedelta(seconds=5)).timestamp() * 1000),
                      "scheduleEndTime": int((when + timedelta(seconds=20)).timestamp() * 1000),
                      "startWorkflowRequest": request}
        # 固定版使用 Spring 六字段解析器；创建返回200不代表cron有效，必须由服务器预览证明只触发一次。
        preview_query = urllib.parse.urlencode({"cronExpression": definition["cronExpression"],
                                               "scheduleStartTime": definition["scheduleStartTime"],
                                               "scheduleEndTime": definition["scheduleEndTime"], "limit": 2})
        preview = self.api.request("GET", "/api/scheduler/nextFewSchedules?" + preview_query)
        if preview != [int(when.timestamp() * 1000)]:
            raise DemoError("SCHEDULE_PREVIEW_NOT_SINGLE")
        run, fresh = self.ledger.reserve(request, "scheduler")
        if not fresh and not repair_unfired:
            raise DemoError("SCHEDULE_ALREADY_ATTEMPTED")
        path = "/api/scheduler/schedules/" + SCHEDULE
        try:
            existing = self.api.request("GET", path)
        except DemoError as error:
            if error.status != 404:
                raise
            existing = None
        repair_key = "schedule-repair:" + SCHEDULE
        if existing is not None:
            if (not repair_unfired or fresh or run["workflowId"] or existing.get("paused") is not True
                    or not isinstance(existing.get("scheduleEndTime"), int)
                    or existing["scheduleEndTime"] >= int(datetime.now(timezone.utc).timestamp() * 1000)
                    or not matches(request, existing.get("startWorkflowRequest"))
                    or self.schedule_executions() or self.correlated(request)
                    or self.ledger.get(repair_key)):
                raise DemoError("SCHEDULE_REPAIR_UNSAFE" if repair_unfired else "SCHEDULE_NAME_EXISTS")
            self.ledger.put(repair_key, {"original": existing, "replacement": definition, "state": "INTENT"})
            if digest(self.api.request("GET", path)) != digest(existing):
                raise DemoError("SCHEDULE_CONCURRENT_CHANGE")
        elif not fresh or repair_unfired:
            raise DemoError("SCHEDULE_OUTCOME_UNKNOWN")
        try:
            self.api.request("POST", "/api/scheduler/schedules", definition)
        except DemoError:
            if not matches(definition, self.api.request("GET", path)):
                raise DemoError("SCHEDULE_OUTCOME_UNKNOWN") from None
        if not matches(definition, self.api.request("GET", path)):
            raise DemoError("SCHEDULE_READBACK_FAILED")
        if repair_unfired:
            evidence = self.ledger.get(repair_key)
            evidence["state"] = "KNOWN"
            self.ledger.put(repair_key, evidence)
        try:
            self.api.request("PUT", path + "/resume")
            deadline = time.monotonic() + max_seconds
            while time.monotonic() < deadline:
                # correlation 的原ID一出现即让已就绪worker接管，不等待UI历史索引/归档。
                adopted = self.adopt(run)
                rows = self.schedule_executions()
                if rows and rows[0].get("state") == "EXECUTED":
                    if adopted and rows[0].get("workflowId") == adopted["workflowId"]:
                        return {"schedule": SCHEDULE, "executions": 1, "schedulerHistory": "CONFIRMED", **adopted}
                    if adopted:
                        raise DemoError("SCHEDULE_WORKFLOW_CONFLICT")
                if rows and rows[0].get("state") == "FAILED":
                    raise DemoError("SCHEDULE_EXECUTION_FAILED")
                if adopted:
                    return {"schedule": SCHEDULE, "schedulerHistory": "PENDING_READBACK", **adopted}
                time.sleep(0.5)
            raise DemoError("SCHEDULE_EXECUTION_NOT_OBSERVED")
        finally:
            self.api.request("PUT", path + "/pause")
            if self.api.request("GET", path).get("paused") is not True:
                raise DemoError("SCHEDULE_PAUSE_NOT_CONFIRMED")

    def status(self):
        workflows = []
        for run in self.ledger.runs():
            if not run["workflowId"]:
                self.adopt(run)
            if not run["workflowId"]:
                workflows.append({"state": "START_OUTCOME_UNKNOWN"})
                continue
            workflow = self.api.request("GET", "/api/workflow/" + run["workflowId"] + "?includeTasks=true")
            tasks = []
            for task in workflow.get("tasks", []):
                logs = self.api.request("GET", "/api/tasks/" + segment(task["taskId"]) + "/log") or []
                tasks.append({"taskId": task["taskId"], "reference": task.get("referenceTaskName"),
                              "status": task.get("status"), "retryCount": task.get("retryCount"), "logs": len(logs)})
            workflows.append({"workflowId": run["workflowId"], "correlationId": run["request"]["correlationId"],
                              "status": workflow.get("status"), "tasks": tasks})
        return {"workflows": workflows, "uniqueRecords": self.ledger.record_count()}


class Worker:
    def __init__(self, driver, worker_id):
        self.driver, self.worker_id = driver, segment(worker_id)

    def poll(self, stage):
        query = urllib.parse.urlencode({"workerid": self.worker_id, "domain": DOMAIN, "count": 1, "timeout": 100})
        path = f"/api/tasks/poll/batch/fleet_demo_document_{stage}_v1?" + query
        marker = "poll:" + self.worker_id + ":" + stage
        self.driver.ledger.put(marker, {"workerId": self.worker_id, "stage": stage, "state": "INTENT"})
        try:
            tasks = self.driver.api.request("GET", path, timeout=2)
            if not isinstance(tasks, list) or len(tasks) > 1:
                raise DemoError("POLL_RESPONSE_INVALID")
        except DemoError as error:
            if str(error) != "TRANSPORT_UNKNOWN" and not (error.status and error.status >= 500):
                raise
            # poll 会分配租约；响应丢失后只能依据本次 workerId、domain 和自有 workflow 恢复。
            tasks = []
            self.driver.ledger.put(marker, {"workerId": self.worker_id, "stage": stage, "state": "UNKNOWN"})
            for run in self.driver.ledger.runs():
                if not run["workflowId"]:
                    continue
                workflow = self.driver.api.request("GET", "/api/workflow/" + run["workflowId"] + "?includeTasks=true")
                for task in workflow.get("tasks", []):
                    if (task.get("status") == "IN_PROGRESS" and task.get("workerId") == self.worker_id
                            and task.get("domain") == DOMAIN
                            and task.get("taskDefName", task.get("taskType")) == f"fleet_demo_document_{stage}_v1"):
                        tasks.append(task)
            if len(tasks) != 1:
                raise DemoError("POLL_OUTCOME_UNKNOWN", operation="GET /api/tasks/poll/batch/{taskType}") from None
        self.driver.ledger.put(marker, {"workerId": self.worker_id, "stage": stage, "state": "CONFIRMED",
                                        "taskIds": [task["taskId"] for task in tasks]})
        return tasks

    def process(self, task):
        task_id = segment(task.get("taskId"))
        runs = [run for run in self.driver.ledger.runs() if run["workflowId"] == task.get("workflowInstanceId")]
        if len(runs) != 1:
            raise DemoError("UNOWNED_TASK")
        stage = next((key for key in REFERENCES if task.get("taskDefName", task.get("taskType")) == f"fleet_demo_document_{key}_v1"), None)
        data, attempt = task.get("inputData"), task.get("retryCount", 0)
        expected = runs[0]["request"]["input"]
        if (stage is None or task.get("referenceTaskName") != REFERENCES[stage] or not isinstance(data, dict)
                or set(data) - {"runId", "idempotencyKey", "scenario", "simulated", "document", "references", "analysis"}
                or any(data.get(key) != expected[key] for key in ("runId", "idempotencyKey", "scenario", "simulated", "document"))
                or type(attempt) is not int or not 0 <= attempt <= 1):
            raise DemoError("TASK_INPUT_INVALID")
        marker = "attempt:" + task_id
        state = self.driver.ledger.get(marker)
        if state is None:
            if attempt:
                self.driver.event(task_id, stage, "retry", attempt)
            self.driver.event(task_id, stage, "start", attempt)
            status, reason = "COMPLETED", None
            output = {"simulated": True}
            if stage == "validate":
                doc = data["document"]
                if doc.get("mimeType") != "text/plain" or type(doc.get("bytes")) is not int or not 0 < doc["bytes"] <= 1_048_576:
                    status, reason = "FAILED_WITH_TERMINAL_ERROR", "DEMO_METADATA_INVALID"
                else:
                    output["document"] = doc
            elif stage == "retrieve":
                output["references"] = ["demo-reference-a", "demo-reference-b"]
            elif stage == "ai":
                if data["scenario"] == "terminal_failure":
                    status, reason = "FAILED_WITH_TERMINAL_ERROR", "DEMO_ANALYSIS_TERMINAL_FAILURE"
                elif attempt == 0:
                    status, reason = "FAILED", "DEMO_ANALYSIS_TRANSIENT_FAILURE"
                else:
                    output["analysis"] = {"provider": "stub", "simulated": True, "summary": "模拟文档分析完成"}
            else:
                try:
                    output = self.driver.ledger.record(data["idempotencyKey"], {"document": data["document"], "analysis": data["analysis"]})
                except DemoError:
                    status, reason = "FAILED_WITH_TERMINAL_ERROR", "DEMO_RECORD_IDEMPOTENCY_CONFLICT"
            result = {"taskId": task_id, "workflowInstanceId": task["workflowInstanceId"], "workerId": self.worker_id,
                      "status": status, "outputData": output}
            if reason:
                result["reasonForIncompletion"] = reason
            state = {"result": result, "ack": "NOT_SENT"}
            self.driver.ledger.put(marker, state)
        result = state["result"]
        if state["ack"] != "CONFIRMED":
            if state["ack"] == "NOT_SENT":
                state["ack"] = "UNKNOWN"
                self.driver.ledger.put(marker, state)
                try:
                    self.driver.api.request("POST", "/api/tasks", result)
                    state["ack"] = "CONFIRMED"
                except DemoError:
                    pass
            if state["ack"] != "CONFIRMED":
                current = self.driver.api.request("GET", "/api/tasks/" + task_id)
                if current.get("status") != result["status"] or current.get("outputData") != result["outputData"]:
                    raise DemoError("TASK_UPDATE_OUTCOME_UNKNOWN")
                state["ack"] = "CONFIRMED"
            self.driver.ledger.put(marker, state)
        phase = "success" if result["status"] == "COMPLETED" else "failure"
        self.driver.event(task_id, stage, phase, attempt)
        return result

    def observe(self):
        waiting, active = False, False
        for run in self.driver.ledger.runs():
            if not run["workflowId"]:
                continue
            workflow = self.driver.api.request("GET", "/api/workflow/" + run["workflowId"] + "?includeTasks=true")
            active |= workflow.get("status") not in TERMINAL
            for task in workflow.get("tasks", []):
                if task.get("referenceTaskName") != "wait_for_registration":
                    continue
                task_id = segment(task["taskId"])
                if task.get("status") == "IN_PROGRESS":
                    waiting = True
                    self.driver.event(task_id, "wait", "start", source="conductor_status_observer")
                elif task.get("status") == "COMPLETED" and self.driver.ledger.get("event:" + digest([task_id, "start"])[:24]):
                    self.driver.event(task_id, "wait", "success", source="conductor_status_observer")
        return waiting, active

    def run(self, max_seconds=90, stop_on_wait=False, await_start=False):
        deadline = time.monotonic() + max_seconds
        processed = 0
        seen_active = False
        while time.monotonic() < deadline:
            waiting, active = self.observe()
            seen_active |= active
            if waiting and stop_on_wait:
                return {"workerId": self.worker_id, "processed": processed, "stopReason": "WAIT_OBSERVED"}
            if not active:
                if await_start and not seen_active:
                    time.sleep(0.25)
                    continue
                return {"workerId": self.worker_id, "processed": processed, "stopReason": "ALL_TERMINAL"}
            for stage in REFERENCES:
                for task in self.poll(stage):
                    self.process(task)
                    processed += 1
            time.sleep(0.25)
        return {"workerId": self.worker_id, "processed": processed, "stopReason": "BOUNDED_TIMEOUT"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "register", "start", "worker", "status", "schedule-once", "pause-schedule", "retry-known-timeout"])
    parser.add_argument("--base-url")
    parser.add_argument("--credentials")
    parser.add_argument("--ledger")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-key")
    parser.add_argument("--scenario", default="retry_success", choices=["retry_success", "terminal_failure"])
    parser.add_argument("--wait-seconds", type=int, default=5)
    parser.add_argument("--max-seconds", type=int, default=90)
    parser.add_argument("--stop-on-wait", action="store_true")
    parser.add_argument("--await-start", action="store_true")
    parser.add_argument("--at-utc")
    parser.add_argument("--repair-unfired", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "plan" or (args.command != "status" and not args.execute):
        print(canonical({"workflow": WORKFLOW, "tasks": list(REFERENCES), "schedule": SCHEDULE,
                         "maximumStarts": 3, "simulated": True, "mutations": False}))
        return 0
    if not args.base_url or not args.credentials or not args.ledger or not 1 <= args.max_seconds <= 180:
        raise DemoError("RUNTIME_ARGUMENTS_REQUIRED")
    api = Client(args.base_url, args.credentials)
    ledger = Ledger(args.ledger)
    try:
        driver = Driver(api, ledger)
        if args.command == "register": result = driver.register()
        elif args.command == "start": result = driver.start(args.run_key, args.scenario, args.wait_seconds)
        elif args.command == "retry-known-timeout": result = driver.retry_timed_out(args.run_key)
        elif args.command == "worker":
            worker = Worker(driver, "demo-worker-" + uuid.uuid4().hex[:12])
            if args.await_start:
                definition = api.request("GET", "/api/metadata/workflow/" + WORKFLOW + "?version=1")
                if definition.get("name") != WORKFLOW:
                    raise DemoError("WORKER_PREFLIGHT_FAILED")
                print(canonical({"workerId": worker.worker_id, "ready": True, "boundedSeconds": args.max_seconds}), flush=True)
            result = worker.run(args.max_seconds, args.stop_on_wait, args.await_start)
        elif args.command == "schedule-once":
            when = datetime.fromisoformat(args.at_utc.replace("Z", "+00:00")) if args.at_utc else datetime.now(timezone.utc) + timedelta(seconds=45)
            result = driver.schedule_once(args.run_key, when, args.max_seconds, args.repair_unfired)
        elif args.command == "pause-schedule":
            api.request("PUT", "/api/scheduler/schedules/" + SCHEDULE + "/pause")
            result = {"schedule": SCHEDULE, "paused": api.request("GET", "/api/scheduler/schedules/" + SCHEDULE).get("paused")}
        else: result = driver.status()
        print(canonical(result))
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoError as error:
        print(canonical({"error": str(error), "httpStatus": error.status, "operation": error.operation}), file=sys.stderr)
        raise SystemExit(1)
