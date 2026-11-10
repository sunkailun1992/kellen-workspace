# Conductor 文档模拟业务流

这个演示用真实 Conductor 3.33.0 保存工作流、队列、重试、WAIT、日程和任务日志；
文档、检索和 AI 结果均为固定模拟数据，AI 的 `provider=stub`、`simulated=true`。
它不接收真实文档、不调用模型或检索服务、不发送消息，也不新增入站 HTTP 服务。

流程是：文档元数据校验 → 模拟检索 → 模拟 AI 分析 → Conductor WAIT → 私有 SQLite 幂等登记。
AI 第一次尝试返回受控 `FAILED`，由 Conductor 原生重试后完成；终态失败场景则返回
`FAILED_WITH_TERMINAL_ERROR`，不能变成成功结果。

## 文件与固定名称

- `scripts/conductor_demo.py`：标准库 CLI、原生 SIMPLE worker 和私有 ledger。
- `docs/examples/conductor_document_demo.json`：四个 task definition 与 workflow 定义。
- `tests/test_conductor_demo.py`：丢响应、重复登记、恢复、真实回环 HTTP、日志与日程暂停回归。
- Workflow：`fleet_demo_document_analysis_v1`。
- Tasks：`fleet_demo_document_validate_v1`、`fleet_demo_document_retrieve_v1`、
  `fleet_demo_document_ai_v1`、`fleet_demo_document_record_v1`。
- WAIT reference：`wait_for_registration`。
- 日程：`fleet_demo_document_utc_once_20261110`；worker domain：`fleet_demo_document_20261110`。

新定义均使用独立演示前缀。注册前 GET 比较，同名非等价定义拒绝覆盖，不复用旧验收定义。
这个 worker 不消费 ledger 未记录的 workflow，也不给旧任务补写演示日志。

## 默认计划与显式执行

```bash
python3 scripts/conductor_demo.py plan
python3 -m unittest discover -s tests -p test_conductor_demo.py -v
```

除只读 `status` 外，命令缺少 `--execute` 时只输出无网络、无写入的计划。
真实 origin、认证文件和 ledger 路径由操作者提供，不进入工作流输入或 Git：

```bash
python3 scripts/conductor_demo.py register --execute \
  --base-url '<PRIVATE_CONDUCTOR_ORIGIN>' \
  --credentials /secure/conductor-ui.json --ledger /secure/demo/state.sqlite3
```

认证文件必须在 Git 外、当前用户持有、普通文件、0600，包含 `ui_username` 和 `ui_password`。
客户端只接受显式私网 IP origin，不经环境代理转交 Basic 身份，不跟随重定向。
ledger 同样在 Git 外、文件0600和父目录0700，已有未知 SQLite 文件拒绝接管。

以下命令都使用同一组运行参数：

```bash
# 终端 A：先启动有界 worker，收到 ready:true 后才在终端 B 发 start。
python3 scripts/conductor_demo.py worker --execute <运行参数> \
  --max-seconds 90 --await-start --stop-on-wait
# 终端 B：成功/原生重试；WAIT 中第一个 worker 自动退出，再启动新进程恢复。
python3 scripts/conductor_demo.py start --execute <运行参数> \
  --run-key fleet_demo_document_20261110_retry_recovery --wait-seconds 20
python3 scripts/conductor_demo.py worker --execute <运行参数> --max-seconds 90

# 明确终态失败，不执行结果登记；同样先在终端 A 启动 --await-start worker。
python3 scripts/conductor_demo.py start --execute <运行参数> \
  --run-key fleet_demo_document_20261110_terminal_failure --scenario terminal_failure

# UTC 一次性 Cron：先在终端 A 启动 --await-start --max-seconds 180 worker。
# 再建立 paused 定义，有限时间启用，观察一条真实调度记录后 finally 暂停。
python3 scripts/conductor_demo.py schedule-once --execute <运行参数> \
  --run-key fleet_demo_document_20261110_utc_once --max-seconds 90
python3 scripts/conductor_demo.py status <运行参数>
```

`<运行参数>` 是示意占位符，实际展开为 `--base-url`、`--credentials` 和 `--ledger`。
本批 ledger 最多保留三个启动意图，不能通过重复命令无限启动；日程创建和重新启用也不会
自动重试。最终保留暂停日程供 UI 查看，不安装 launchd 或常驻 worker。

固定版 Scheduler 使用 Spring `CronExpression` 的 **6 字段**格式；不能添加 Quartz 的第7个
year 字段。单次测试由 UTC cron 加毫秒单位的 `scheduleStartTime` / `scheduleEndTime` 窗口
限定。每次创建或更新定义前，工具必须调用服务端 `nextFewSchedules`，确认唯一返回值
与窗口内的预期时间完全相同；预览失败（包括 HTTP 500）时不写入、不启用、不重试。

首次现场的7字段定义虽然被创建接口以200保存，实际没有产生任何执行记录。其原定义快照
已保留；修正只通过显式 `--repair-unfired`，要求本批拥有、已暂停、窗口过期、无调度记录、
correlation 下也无 workflow，且更新前当前定义摘要未变。修正仍沿用原 correlation / idempotency。
这些检查是本工具的防护，没有修改服务端 UI，不能宣称整个 Conductor 平台已拒绝所有无效 cron 输入。

调度历史按该版本 `SchedulerSearchQuery` 的正式语法查询：
`scheduleName IN (fleet_demo_document_utc_once_20261110)`，名称不加 SQL 单引号。
早期工具误用了 `scheduleName='name'`；解析器会把整段当作日程名字面量，因此查询为0，
这不是已确认的上游过滤缺陷。工具现使用正确 IN 查询，并核对返回名称、总数和 workflow ID；
不能仅凭一个错误过滤条件的空结果判断 Cron 未执行。

correlation 下的原 workflow 一出现，工具就写入 ledger 供已就绪 worker 接管，不等待历史
索引或归档。若历史仍空，返回 `PENDING_READBACK` 并暂停日程，后续只读核对；不能因此
重新触发或改用新 correlation。

## 丢响应与幂等边界

启动先把完整合成请求摘要、correlation 和 idempotency 写入私有 ledger，再发一次 POST。
Conductor 同时使用原生 `idempotencyKey` 与 `RETURN_EXISTING`；若响应不确定，后续只按
correlation GET 查回并采用原 workflow ID。查不到仍报 unknown，不自动重发启动 POST。

任务输出在回执前保存。Task update 或 task log 回应丢失时，只读查询对应 task 或事件 ID
确认，不能盲目重发。结果登记以固定幂等键和输入摘要唯一约束；相同结果跨重试／重启返回
原 record ID，不同结果拒绝覆盖。该保证限本机 SQLite 演示登记，不是对未知外部业务的
分布式 exactly-once 保证。

poll 也会分配任务，不能当作没有副作用的查询。worker 使用原生 batch poll（count=1，
server timeout=100 毫秒，client timeout=2 秒），请求前保存意图。响应丢失时只读自有
workflow，只有 `IN_PROGRESS`、本进程 `workerId`、domain 与 task type 都确认一致才
恢复处理；无法确认即停止。首次现场单 poll 丢响应曾导致一个校验 task 超时且没有业务日志，
其历史保持；修复后在同一已知 TIMED_OUT workflow ID 上按明确授权只 retry 一次，
恢复意图和原 task 状态在 ledger 保留，没有另建 workflow 或回填假日志。

SIMPLE worker 在每次真实开始、重试、成功或失败时调用
`POST /api/tasks/{taskId}/log`。日志只包含固定事件 ID、stage、phase、attempt、中文状态、
`provider=stub` 和 `simulated=true`，没有凭据、输入正文、文档内容或原始异常。
WAIT 的日志注明 `source=conductor_status_observer`，表示当时实际观察到执行中／完成，
不是冒充系统任务的内部执行日志；从未观察到开始的旧完成 WAIT 不补写日志。

## UI 与验收

真实前端为 ui-next，使用 `/executions`、`/scheduleDef`、`/schedulerExecs`。
执行详情中的任务 Logs 来自上述原生 log API；服务运行日志与 task execution log 是不同记录。
这个流程的 AI 替身不要求打开 UI 的真实供应商集成开关。

验收必须分别核对：三条 workflow 的真实终态、AI 两次尝试、失败路径没有 record、
WAIT 前后同一个 task ID及不同 worker进程、原生任务 Logs、SQLite 唯一登记数、
一条 `EXECUTED` 调度记录及日程最终 `paused=true`。结束时确认没有待处理演示任务和常驻 worker。
只有注册成功或本地测试通过，不能称为完成这些运行态验收。

## 固定版本 API 依据

- [TaskResource v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/rest/src/main/java/com/netflix/conductor/rest/controllers/TaskResource.java)
- [WorkflowResource v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/rest/src/main/java/com/netflix/conductor/rest/controllers/WorkflowResource.java)
- [StartWorkflowRequest v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/common/src/main/java/com/netflix/conductor/common/metadata/workflow/StartWorkflowRequest.java)
- [SchedulerResource v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/scheduler/core/src/main/java/io/orkes/conductor/scheduler/rest/SchedulerResource.java)
- [WorkflowSchedule v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/scheduler/core/src/main/java/io/orkes/conductor/scheduler/model/WorkflowSchedule.java)
- [SchedulerSearchQuery v3.33.0](https://github.com/conductor-oss/conductor/blob/v3.33.0/scheduler/core/src/main/java/io/orkes/conductor/dao/archive/SchedulerSearchQuery.java)
