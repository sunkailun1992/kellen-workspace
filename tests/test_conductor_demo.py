"""验证真实调度边界：丢响应不重启、幂等登记、原生日志和 worker 恢复。"""

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import conductor_demo as demo
except ModuleNotFoundError:
    demo = None


class ApiFixture:
    def __init__(self):
        self.calls = []
        self.workflows = []
        self.logs = {}
        self.tasks = {}
        self.lose_start = False
        self.lose_update = False
        self.lose_log = False
        self.hide_workflows = False
        self.schedule = None
        self.next_task = None
        self.lose_poll = False
        self.finish_on_update = False
        self.preview_times = None
        self.search_error = "SYNTHETIC_SCHEDULER_READ_FAILED"
        self.execution_rows = []
        self.start_native_on_resume = False

    def request(self, method, path, payload=None, text=None, timeout=5):
        self.calls.append((method, path, deepcopy(payload), text))
        if path.startswith("/api/scheduler/nextFewSchedules"):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            if len(params["cronExpression"][0].split()) != 6:
                raise demo.DemoError("HTTP_REJECTED", 500)
            return self.preview_times if self.preview_times is not None else [int(params["scheduleStartTime"][0]) + 5000]
        if "/poll/" in path:
            task, self.next_task = self.next_task, None
            if task:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                task.update(status="IN_PROGRESS", workerId=query["workerid"][0], domain=demo.DOMAIN)
                self.workflows[0]["tasks"] = [task]
                self.tasks[task["taskId"]] = task
            if self.lose_poll:
                self.lose_poll = False
                raise demo.DemoError("TRANSPORT_UNKNOWN")
            return [task] if task and "/batch/" in path else [] if "/batch/" in path else task
        if "/correlated/" in path:
            return [] if self.hide_workflows else self.workflows
        if method == "POST" and path == "/api/workflow":
            workflow = {"workflowId": "wf-001", "workflowName": payload["name"], "status": "RUNNING",
                        "correlationId": payload["correlationId"], "input": payload["input"], "tasks": []}
            self.workflows.append(workflow)
            if self.lose_start:
                raise demo.DemoError("TRANSPORT_UNKNOWN")
            return "wf-001"
        if method == "POST" and path == "/api/workflow/wf-001/retry":
            self.workflows[0]["status"] = "RUNNING"
            return None
        if method == "GET" and path.startswith("/api/workflow/"):
            return self.workflows[0]
        if path.endswith("/log"):
            task_id = path.split("/")[-2]
            if method == "POST":
                self.logs.setdefault(task_id, []).append({"log": text, "taskId": task_id})
                if self.lose_log:
                    raise demo.DemoError("TRANSPORT_UNKNOWN")
                return None
            return self.logs.get(task_id, [])
        if method == "POST" and path == "/api/tasks":
            self.tasks[payload["taskId"]] = deepcopy(payload)
            if self.finish_on_update:
                self.workflows[0]["status"] = "COMPLETED"
            if self.lose_update:
                raise demo.DemoError("TRANSPORT_UNKNOWN")
            return payload["taskId"]
        if method == "GET" and path.startswith("/api/tasks/"):
            return self.tasks[path.split("/")[-1]]
        if path.endswith("/pause"):
            self.schedule["paused"] = True
            return None
        if path.endswith("/resume"):
            self.schedule["paused"] = False
            if self.start_native_on_resume:
                request = self.schedule["startWorkflowRequest"]
                self.workflows.append({"workflowId": "wf-scheduled", "correlationId": request["correlationId"],
                                       "input": request["input"], "status": "RUNNING", "tasks": []})
            return None
        if path == "/api/scheduler/schedules" and method == "POST":
            self.schedule = deepcopy(payload)
            return self.schedule
        if path.startswith("/api/scheduler/schedules/"):
            if self.schedule is None:
                raise demo.DemoError("HTTP_REJECTED", 404)
            return self.schedule
        if path.startswith("/api/scheduler/search/executions"):
            if self.search_error:
                raise demo.DemoError(self.search_error)
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            selected = self.execution_rows
            if params.get("query"):
                expression = params["query"][0]
                names = expression[17:-1].split(",") if expression.startswith("scheduleName IN (") and expression.endswith(")") else [expression]
                selected = [row for row in selected if row.get("scheduleName") in names]
            start, size = int(params.get("start", [0])[0]), int(params.get("size", [100])[0])
            return {"totalHits": len(selected), "results": selected[start:start + size]}
        raise AssertionError((method, path))


class ConductorDemoTests(unittest.TestCase):
    def setUp(self):
        if demo is None:
            self.fail("conductor_demo 尚未实现；先验证缺失行为的 RED")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.ledger = demo.Ledger(self.path)
        self.addCleanup(self.ledger.close)
        self.api = ApiFixture()
        self.driver = demo.Driver(self.api, self.ledger)

    def tearDown(self):
        self.ledger.close()

    def start(self, scenario="retry_success"):
        return self.driver.start("fleet_demo_document_test", scenario, 5)

    def task(self, stage, retry=0):
        run = self.api.workflows[0]["input"]
        data = {"runId": run["runId"], "idempotencyKey": run["idempotencyKey"],
                "scenario": run["scenario"], "simulated": True, "document": run["document"]}
        if stage == "ai": data["references"] = ["demo-reference-a", "demo-reference-b"]
        if stage == "record": data["analysis"] = {"provider": "stub", "simulated": True, "summary": "模拟文档分析完成"}
        return {"taskId": f"task-{stage}-{retry}", "workflowInstanceId": "wf-001", "retryCount": retry,
                "taskType": f"fleet_demo_document_{stage}_v1", "referenceTaskName": demo.REFERENCES[stage],
                "inputData": data}

    def test_lost_start_response_is_reconciled_without_a_second_post_after_restart(self):
        self.api.lose_start = True
        self.api.hide_workflows = True
        with self.assertRaisesRegex(demo.DemoError, "START_OUTCOME_UNKNOWN"):
            self.start()
        self.ledger.close()
        self.ledger = demo.Ledger(self.path)
        self.driver = demo.Driver(self.api, self.ledger)
        self.api.hide_workflows = False
        result = self.start()
        self.assertEqual(result["workflowId"], "wf-001")
        self.assertEqual(sum(method == "POST" and path == "/api/workflow" for method, path, _, _ in self.api.calls), 1)

    def test_unknown_start_stays_unknown_when_readback_has_no_match(self):
        self.api.lose_start = True
        self.api.hide_workflows = True
        for _ in range(2):
            with self.assertRaisesRegex(demo.DemoError, "START_OUTCOME_UNKNOWN"):
                self.start()
        self.assertEqual(sum(method == "POST" and path == "/api/workflow" for method, path, _, _ in self.api.calls), 1)

    def test_ai_retry_and_terminal_failure_have_real_explicit_log_events(self):
        self.start()
        worker = demo.Worker(self.driver, "offline-worker")
        worker.process(self.task("ai", 0))
        worker.process(self.task("ai", 1))
        self.assertEqual(self.api.tasks["task-ai-0"]["status"], "FAILED")
        self.assertEqual(self.api.tasks["task-ai-1"]["status"], "COMPLETED")
        events = [json.loads(entry["log"]) for logs in self.api.logs.values() for entry in logs]
        self.assertEqual([e["phase"] for e in events], ["start", "failure", "retry", "start", "success"])
        self.assertTrue(all(e["simulated"] and e["provider"] == "stub" for e in events))

    def test_terminal_failure_does_not_request_retry(self):
        self.start("terminal_failure")
        worker = demo.Worker(self.driver, "offline-worker")
        task = self.task("ai", 0)
        task["taskId"] = "task-terminal"
        task["inputData"]["scenario"] = "terminal_failure"
        worker.process(task)
        self.assertEqual(self.api.tasks["task-terminal"]["status"], "FAILED_WITH_TERMINAL_ERROR")

    def test_result_record_is_unique_across_worker_and_ledger_restart(self):
        self.start()
        demo.Worker(self.driver, "worker-a").process(self.task("record"))
        original = self.api.tasks["task-record-0"]["outputData"]["recordId"]
        self.ledger.close()
        self.ledger = demo.Ledger(self.path)
        self.driver = demo.Driver(self.api, self.ledger)
        demo.Worker(self.driver, "worker-b").process(self.task("record", 1))
        self.assertEqual(self.api.tasks["task-record-1"]["outputData"]["recordId"], original)
        self.assertEqual(self.ledger.record_count(), 1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_record_conflict_cannot_overwrite_the_original_result(self):
        self.start()
        worker = demo.Worker(self.driver, "worker")
        worker.process(self.task("record"))
        task = self.task("record", 1)
        task["inputData"]["analysis"]["summary"] = "冲突的模拟结果"
        worker.process(task)
        self.assertEqual(self.api.tasks["task-record-1"]["status"], "FAILED_WITH_TERMINAL_ERROR")
        self.assertEqual(self.ledger.record_count(), 1)

    def test_lost_log_and_update_responses_are_read_back_not_blindly_reposted(self):
        self.start()
        self.api.lose_log = self.api.lose_update = True
        worker = demo.Worker(self.driver, "worker")
        task = self.task("validate")
        worker.process(task)
        worker.process(task)
        self.assertEqual(sum(method == "POST" and path.endswith("/log") for method, path, _, _ in self.api.calls), 2)
        self.assertEqual(sum(method == "POST" and path == "/api/tasks" for method, path, _, _ in self.api.calls), 1)

    def test_unowned_tasks_and_unexpected_input_are_never_logged_or_processed(self):
        self.start()
        task = self.task("validate")
        task["workflowInstanceId"] = "historical-unowned"
        with self.assertRaisesRegex(demo.DemoError, "UNOWNED_TASK"):
            demo.Worker(self.driver, "worker").process(task)
        self.assertEqual(self.api.logs, {})
        task = self.task("validate")
        task["inputData"]["authorization"] = "synthetic-private-marker"
        with self.assertRaisesRegex(demo.DemoError, "TASK_INPUT_INVALID"):
            demo.Worker(self.driver, "worker").process(task)
        self.assertEqual(self.api.logs, {})

    def test_schedule_uses_one_shot_utc_and_pauses_even_when_readback_fails(self):
        from datetime import datetime, timedelta, timezone
        when = datetime.now(timezone.utc) + timedelta(seconds=45)
        with self.assertRaisesRegex(demo.DemoError, "SYNTHETIC_SCHEDULER_READ_FAILED"):
            self.driver.schedule_once("fleet_demo_document_test", when, max_seconds=1)
        self.assertTrue(self.api.schedule["paused"])
        self.assertEqual(self.api.schedule["zoneId"], "UTC")
        self.assertFalse(self.api.schedule["runCatchupScheduleInstances"])
        self.assertEqual(self.api.schedule["startWorkflowRequest"]["idempotencyStrategy"], "RETURN_EXISTING")
        self.assertEqual(len(self.api.schedule["cronExpression"].split()), 6)

    def test_server_preview_must_confirm_one_fire_before_any_schedule_write(self):
        from datetime import datetime, timedelta, timezone
        self.api.preview_times = []
        with self.assertRaisesRegex(demo.DemoError, "SCHEDULE_PREVIEW_NOT_SINGLE"):
            self.driver.schedule_once("fleet_demo_document_test", datetime.now(timezone.utc) + timedelta(seconds=45))
        self.assertIsNone(self.api.schedule)
        self.assertFalse(any(method in {"POST", "PUT"} for method, path, _, _ in self.api.calls if path.startswith("/api/scheduler/")))

    def test_unfired_expired_owned_schedule_can_be_repaired_without_another_workflow_start(self):
        from datetime import datetime, timedelta, timezone
        when = datetime.now(timezone.utc) + timedelta(seconds=45)
        with self.assertRaises(demo.DemoError):
            self.driver.schedule_once("fleet_demo_document_test", when, max_seconds=1)
        self.api.schedule["scheduleEndTime"] = int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp() * 1000)
        original = deepcopy(self.api.schedule)
        self.api.search_error = None
        with self.assertRaisesRegex(demo.DemoError, "SCHEDULE_EXECUTION_NOT_OBSERVED"):
            self.driver.schedule_once("fleet_demo_document_test", when + timedelta(seconds=15), max_seconds=0.01, repair_unfired=True)
        self.assertTrue(self.api.schedule["paused"])
        self.assertEqual(self.ledger.get("schedule-repair:" + demo.SCHEDULE)["original"], original)
        self.assertFalse(any(method == "POST" and path == "/api/workflow" for method, path, _, _ in self.api.calls))

    def test_schedule_repair_refuses_any_prior_execution(self):
        from datetime import datetime, timedelta, timezone
        when = datetime.now(timezone.utc) + timedelta(seconds=45)
        with self.assertRaises(demo.DemoError):
            self.driver.schedule_once("fleet_demo_document_test", when, max_seconds=1)
        self.api.schedule["scheduleEndTime"] = 1
        self.api.search_error = None
        self.api.execution_rows = [{"scheduleName": demo.SCHEDULE, "state": "EXECUTED", "workflowId": "prior-workflow"}]
        with self.assertRaisesRegex(demo.DemoError, "SCHEDULE_REPAIR_UNSAFE"):
            self.driver.schedule_once("fleet_demo_document_test", when + timedelta(seconds=15), max_seconds=1, repair_unfired=True)
        writes = [1 for method, path, _, _ in self.api.calls if method == "POST" and path == "/api/scheduler/schedules"]
        self.assertEqual(len(writes), 1)

    def test_schedule_history_uses_official_in_filter_instead_of_sql_equality(self):
        self.api.search_error = None
        self.api.execution_rows = [{"scheduleName": "unrelated-demo", "executionId": f"old-{i}"} for i in range(101)]
        expected = {"scheduleName": demo.SCHEDULE, "workflowId": "actual-cron", "state": "EXECUTED"}
        self.api.execution_rows.append(expected)
        self.assertEqual(self.driver.schedule_executions(), [expected])
        searches = [path for method, path, _, _ in self.api.calls if "/search/executions" in path]
        self.assertEqual(len(searches), 1)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(searches[0]).query)
        self.assertEqual(query["query"], [f"scheduleName IN ({demo.SCHEDULE})"])
        self.assertEqual(query["size"], ["2"])

    def test_correlated_workflow_is_adopted_without_waiting_for_history_indexing(self):
        from datetime import datetime, timedelta, timezone
        self.api.search_error = None
        self.api.start_native_on_resume = True
        when = datetime.now(timezone.utc) + timedelta(seconds=45)
        try:
            result = self.driver.schedule_once("fleet_demo_document_test", when, max_seconds=0.02)
        except demo.DemoError:
            self.fail("原workflow已存在时不能因为历史搜索尚空而阻止worker接管")
        self.assertEqual(result["workflowId"], "wf-scheduled")
        self.assertEqual(result["schedulerHistory"], "PENDING_READBACK")
        self.assertEqual(self.ledger.runs()[0]["workflowId"], "wf-scheduled")
        self.assertTrue(self.api.schedule["paused"])

    def test_private_ledger_rejects_missing_descendants_inside_a_git_checkout(self):
        checkout = Path(self.temp.name) / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
        with self.assertRaisesRegex(demo.DemoError, "PRIVATE_PATH_INSIDE_GIT"):
            demo.Ledger(checkout / "missing" / "state.sqlite3")
        self.assertFalse((checkout / "missing").exists())

    def test_wait_is_observed_across_worker_restart_without_duplicate_or_fake_history_logs(self):
        self.start()
        wait = {"taskId": "wait-same-task", "referenceTaskName": "wait_for_registration", "status": "IN_PROGRESS"}
        self.api.workflows[0]["tasks"] = [wait]
        self.assertTrue(demo.Worker(self.driver, "worker-before-restart").observe()[0])
        self.ledger.close()
        self.ledger = demo.Ledger(self.path)
        self.driver = demo.Driver(self.api, self.ledger)
        worker = demo.Worker(self.driver, "worker-after-restart")
        self.assertTrue(worker.observe()[0])
        self.assertEqual(len(self.api.logs["wait-same-task"]), 1)
        wait["status"] = "COMPLETED"
        worker.observe()
        events = [json.loads(x["log"]) for x in self.api.logs["wait-same-task"]]
        self.assertEqual([x["phase"] for x in events], ["start", "success"])
        self.assertTrue(all(x["source"] == "conductor_status_observer" for x in events))
        self.api.workflows[0]["tasks"].append({"taskId": "old-completed", "referenceTaskName": "wait_for_registration", "status": "COMPLETED"})
        worker.observe()
        self.assertNotIn("old-completed", self.api.logs)

    def test_lost_poll_response_recovers_only_the_confirmed_owned_lease(self):
        self.start()
        self.api.next_task = self.task("validate")
        self.api.lose_poll = self.api.finish_on_update = True
        worker = demo.Worker(self.driver, "lease-worker")
        try:
            result = worker.run(max_seconds=1)
        except demo.DemoError:
            self.fail("丢失 poll 响应后不能丢弃已经分配给本 worker 的任务")
        self.assertEqual(result["stopReason"], "ALL_TERMINAL")
        self.assertEqual(self.api.tasks["task-validate-0"]["status"], "COMPLETED")
        polls = [path for method, path, _, _ in self.api.calls if "/poll/" in path]
        self.assertTrue(all("/poll/batch/" in path and "timeout=100" in path and "count=1" in path for path in polls))

    def test_unknown_poll_without_a_confirmed_owned_lease_stops_safely(self):
        self.start()
        self.api.lose_poll = True
        with self.assertRaisesRegex(demo.DemoError, "POLL_OUTCOME_UNKNOWN"):
            demo.Worker(self.driver, "lease-worker").run(max_seconds=1)
        self.assertEqual(self.api.logs, {})

    def test_known_timeout_recovery_is_attempted_once_and_preserves_original_task_evidence(self):
        self.start()
        self.api.workflows[0].update(status="TIMED_OUT", tasks=[{"taskId": "original-timeout", "status": "TIMED_OUT"}])
        self.driver.retry_timed_out("fleet_demo_document_test")
        self.driver.retry_timed_out("fleet_demo_document_test")
        retries = [path for method, path, _, _ in self.api.calls if method == "POST" and path.endswith("/retry")]
        self.assertEqual(retries, ["/api/workflow/wf-001/retry"])
        saved = self.ledger.get("recovery:wf-001")
        self.assertEqual(saved["originalStatus"], "TIMED_OUT")
        self.assertEqual(saved["originalTasks"], [{"taskId": "original-timeout", "status": "TIMED_OUT"}])

    def test_worker_can_wait_boundedly_before_a_known_run_is_recovered(self):
        self.start()
        self.api.workflows[0]["status"] = "TIMED_OUT"
        self.api.finish_on_update = True
        def recover():
            time.sleep(0.05)
            self.api.next_task = self.task("validate")
            self.api.workflows[0]["status"] = "RUNNING"
        thread = threading.Thread(target=recover); thread.start()
        try:
            result = demo.Worker(self.driver, "ready-worker").run(max_seconds=2, await_start=True)
            self.assertEqual(result["stopReason"], "ALL_TERMINAL")
            self.assertEqual(result["processed"], 1)
        finally:
            thread.join(1)

    def test_http_transport_uses_native_text_logs_without_forwarding_credentials_or_redirects(self):
        state = {"workflow": None, "logs": [], "result": None, "redirected": 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def send(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                if "/correlated/" in self.path:
                    self.send([state["workflow"]] if state["workflow"] else [])
                elif self.path == "/api/redirect":
                    self.send_response(302); self.send_header("Location", "/api/unexpected-target"); self.end_headers()
                elif self.path == "/api/unexpected-target":
                    state["redirected"] += 1; self.send({})
                else: self.send({})
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/api/workflow":
                    request = json.loads(raw)
                    state["workflow"] = {"workflowId": "wf-http", "correlationId": request["correlationId"],
                                         "input": request["input"], "status": "RUNNING", "tasks": []}
                    self.send("wf-http")
                elif self.path.endswith("/log"):
                    state["logs"].append((self.headers["Content-Type"], raw.decode()))
                    self.send(None)
                elif self.path == "/api/tasks":
                    state["result"] = json.loads(raw); self.send("task-http")
                else: self.send({}, 404)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            credentials = Path(self.temp.name) / "auth.json"
            credentials.write_text(json.dumps({"ui_username": "synthetic-user", "ui_password": "synthetic-private-password"}))
            os.chmod(credentials, 0o600)
            client = demo.Client(f"http://127.0.0.1:{server.server_port}", credentials)
            driver = demo.Driver(client, self.ledger)
            driver.start("fleet_demo_document_http")
            task = {"taskId": "task-http", "workflowInstanceId": "wf-http", "retryCount": 0,
                    "taskType": "fleet_demo_document_validate_v1", "referenceTaskName": "validate_metadata",
                    "inputData": {k: state["workflow"]["input"][k] for k in ["runId", "idempotencyKey", "scenario", "simulated", "document"]}}
            demo.Worker(driver, "http-worker").process(task)
            self.assertEqual(state["result"]["status"], "COMPLETED")
            self.assertEqual(len(state["logs"]), 2)
            self.assertTrue(all(media.startswith("text/plain") for media, _ in state["logs"]))
            self.assertNotIn("synthetic-private-password", json.dumps(state["logs"]))
            with self.assertRaisesRegex(demo.DemoError, "HTTP_REJECTED"):
                client.request("GET", "/api/redirect")
            self.assertEqual(state["redirected"], 0)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__":
    unittest.main()
