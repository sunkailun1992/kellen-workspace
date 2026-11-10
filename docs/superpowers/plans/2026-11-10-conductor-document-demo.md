# Conductor 文档模拟业务流实施计划

> **For agentic workers:** 使用当前已授权的原生执行方式，逐项完成离线回归，再执行有界真实验收。步骤与主会话的执行授权一致，无需额外设计审批。

**Goal:** 在既有 Conductor 3.33.0 中展示真实持久化编排、原生 worker 日志、重试、WAIT 恢复和一次 UTC Cron；检索、AI 与文档均为明确模拟。

**Architecture:** Python 标准库 worker 轮询四种 SIMPLE 任务，使用私有 SQLite 保存启动意图、任务回执及幂等结果。WAIT 由服务端执行；只读观察日志明确标为观察，不冒充引擎原生日志。

**Tech Stack:** Python 3.10+、SQLite、urllib、Conductor REST 3.33.0。

## 固定约束

- 仅新增 `fleet_demo_document_` 前缀定义，不改变旧验收；不调用真实模型、文档、检索、消息，不启入站服务。
- Workflow `fleet_demo_document_analysis_v1`；四个 task 名为 `fleet_demo_document_{validate,retrieve,ai,record}_v1`。
- 新日程 `fleet_demo_document_utc_once_20261110`；UTC 一次性 Cron，catch-up 关闭，触发一次后暂停保留。
- 实际最多三个 workflow：成功/重试及 WAIT worker 重启、终态失败、Cron 成功。主会话已授权离线通过后执行。
- 每次启动写意图在先；相同 correlation/idempotency 结果不确定时只读查回，绝不自动重发启动 POST。
- 凭据、地址、SQLite、回执只在 Git 外的私有文件；日志仅写新执行的实际步骤和脱敏固定元数据。

## Review Focus

1. 启动成功但 HTTP 回应丢失：重启后采用原 ID，启动 POST 总数仍为一次。
2. 结果提交后 worker 中断：相同幂等键返回原 record，不能重复登记或覆盖冲突结果。
3. Task update/log 结果不明：读回确认，不能盲目重发或伪造历史日志。
4. 日程等待失败或异常：finally 暂停，单次 Cron 的有界时间窗阻止常驻触发。
5. 不属于本次 ledger 的任务：拒绝执行和写日志，避免污染历史验收。

## Tasks

- [x] 在 `tests/test_conductor_demo.py` 写启动不确定、重复登记/进程恢复、显式日志、失败重试、所有权和日程暂停回归，并观察 RED。
- [x] 实现 `scripts/conductor_demo.py` 的 Client、Ledger、Driver、Worker；新增 `docs/examples/conductor_document_demo.json`，仅最小必要标准库实现。
- [x] 跑定向及全套 Python 测试，核 CLI plan 默认无网络/写入；新增 `docs/conductor-document-demo.md` 记录运行和真实性边界。
- [x] 按批准名称注册新定义，最多启动两条直接流与一条 Cron 流；同 WAIT task 下停止/重新运行 worker。首次 poll 丢响应历史保留，经明确授权在原 ID retry 一次。
- [x] 查询三流终态为 COMPLETED/FAILED/COMPLETED，SQLite 两条唯一登记；单次 Cron EXECUTED、最终 paused，所有 worker 已退出。主会话已从真实 UI 独立核验 Logs 和调度记录。

Git 收口采用主会话后续明确授权的 fresh fetch、正常 commit、main 快进、push 与固定 SHA CI 核对；不重建 test/preprod。Git/CI 的实际完成状态以最终交付回执为准，本计划不代替执行回执。
