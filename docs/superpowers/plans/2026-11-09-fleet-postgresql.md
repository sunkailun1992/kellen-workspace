# 个人 Java fleet PostgreSQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 7 个个人业务 JDBC 消费者迁到 PostgreSQL，保留共享 MySQL，并完成原监控链路的真实验收。

**Architecture:** 先新增独立 PostgreSQL，再修共享数据源和 SQL，使用受控只读快照传输与逐表指纹验证，最后切个人配置并启动实际服务。原 MySQL 和共用中间件不切换。

**Tech Stack:** Java 17、Spring Boot 4.0.4、MyBatis-Plus 3.5.16、PostgreSQL 18、固定 JDBC 驱动、现有 OpenTelemetry/Collector/Grafana。

**Spec:** `docs/superpowers/specs/2026-11-09-fleet-postgresql-design.md`

## Global Constraints

- 仅个人 fleet；MySQL 整体及公司 schema 不变；共用 Nacos/Seata 保留。用户另授权退役 NAS XXL-JOB，先确认美联 dev 关闭执行器。
- PostgreSQL 服务和隔离测试数据库只部署 NAS；本机不安装或运行数据库。
- 凭据、正文、数据库 dump 不进 Git、日志报告或聊天；私有输入 0600、非 symlink。
- Compose 单文件、固定官方镜像摘要、独立新卷；不得整项目 down/up 或删除现有卷。
- master/gray 使用 PostgreSQL，租户字符串、Boolean、序列和原 ID 语义必须实测。
- 原生日常启动继续为 Spring Boot/Application，不用 Gradle 启动替代。

## Review Focus

- 数字形态租户 `00100` 不被转为数字 100，带引号租户保持参数/表达式转义安全。
- Boolean `false/true/NULL`、微秒时间、JSON、bytea、Decimal 精确值及自增 ID 不被静默改变。
- 已有非空目标、源 schema 漂移或并发写入时拒绝危险复制，不覆盖用户数据。
- 连接池乘积、重复启动和未完成初始化不能被 health 的假绿掩盖。
- MDC/KVP 同名、异步终态和采样位为 0 的请求仍有唯一可关联安全日志。

---

### Task 1: PostgreSQL 共享底座

**Files:** `utils/build.gradle`、`utils/src/main/java/com/kellen/config/datasource/DynamicDataSourceConfig.java`、`utils/src/main/java/com/kellen/config/MyBatisPlusConfig.java` 及对应实际位置的租户处理器；新增 `utils/src/main/resources/db/postgresql/common-infra-schema.sql`。

**Interfaces:** 输入 `postgresql.*`、`postgresql-gray.*`；输出保留名为 `master/gray` 的 PostgreSQL 数据源与既有路由器。公共基础表脚本是业务启动前输入。

- [x] 写并运行租户、PG 分页、Boolean 及字段绑定失败回归，再最小修改驱动和配置。
- [x] 限制 master4、gray2 及每池等待时间，覆盖不合理 min/max 配置拒绝。
- [x] 用 NAS 受控隔离测试库实跑 11 项 DDL/JDBC/ICU/租户/JSONB/逻辑删除/序列回归；全套消费者编译通过，最终制品 standalone 复核继续。

### Task 2: 新 PostgreSQL 与私有完整 Compose

**Files:** 用户 Git 外私有 Compose、初始化和读回工具；公开文档只保存占位符模板及说明。

**Interfaces:** 以实际最新 Compose SHA 为 CAS 基线，仅新增 `orb-postgres` 和新卷；输出 7 库/角色可连接的受限凭据引用。

- [x] 验证官方 tag/index/platform/config 摘要以及原 20 服务不变。
- [x] 审阅内联脚本、端口、角色权限、完整初始化 health 标记及连接预算。
- [x] NAS 原生 `compose config` 校验后仅启动 PostgreSQL，读回版本、资源、初始化标记和库隔离。

### Task 3: 新 PG 业务 DDL

**Files:** 7 个 JDBC 服务的 `IDdl` 入口、新 `src/main/resources/db/postgresql/` 脚本、相应实体/测试及文档。

**Interfaces:** 消费 Task1 公共基础表脚本；生产空 PG 库可重复初始化的业务 schema。已有 MySQL SQL 路径保留为历史，不进入新启动脚本。

- [x] 从实际元数据与实体共同核对字段/索引/唯一约束/默认值，生成 PG 专用脚本。
- [x] 验证大小写/重音比较、排序与租户隔离，不机械假定不同数据库 collation 相同。
- [x] 真实 PG 空库与已初始化库分别执行，检查权限初始化、Boolean 与序列回填。

### Task 4: 有界数据传输与切换

**Files:** 受控迁移工具及单元/隔离数据库测试，执行配置保持 Git 外。

**Interfaces:** 私有源/目标 JDBC 引用和固定 7 schema allowlist；输出不含正文的逐表数量/指纹与状态回执。

- [x] 默认只读 plan，显式 execute 才复制；schema、目标状态、源一致性与参数均先验证。
- [x] 覆盖 NULL/Boolean/JSON/bytea/Decimal/时间/序列、拒绝非空目标及中途故障回滚。
- [ ] 停止明确个人写入者后复制和对账，CAS 发布仅个人数据源配置，再按实际新版本启动。

### Task 5: 日志编码缺陷与实际验收

**Files:** `utils/fleet-observability` 真实 ECS customizer/测试、共享日志配置与 workspace prepare。

**Interfaces:** 禁止默认自由 MDC/KVP 合流，固定安全成员唯一输出；显式事件值优先，合法 agent trace/span 仅作身份兜底。

- [x] 真实 Boot4 formatter 重现同名 MDC/KVP 导致整条日志消失，排除匿名块过滤的初步假设。
- [x] 修复并运行真实 formatter 的 HTTP/AI/异步身份与隐私哨兵回归；保留安全异常类别诊断。
- [ ] 切库后免费 native/metadata、RAG、AI 原矩阵逐请求对账，生成实际 N/N、持久化父子关系和日志终态证据。

### Task 6: 调度决策与最终发布

**Files:** 个人共享调度接入、Conductor 正式镜像私有 Compose、独立 PG/Redis 配置、无模型 HTTP tasks 与工作流定义。

**Interfaces:** 用户已选定 Conductor OSS + PostgreSQL + 隔离 Redis 执行锁。正式镜像必须包含实际开源 scheduler、所需 PG 后端和管理界面；业务 Java 17 通过已验证 Worker SDK 或 HTTP 契约接入，不把服务端镜像 JDK 要求传播到业务服务。

- [x] 记录最终选择：Conductor OSS；排除 XXL-JOB、Quartz、JobRunr、PowerJob 的个人接入方向，用户随后授权移除 NAS XXL-JOB 服务；MySQL 及历史调度数据保留。
- [ ] 验证正式镜像、独立 PG 持久化、隔离 Redis 锁、管理界面、真实任务、Cron、失败重试、重启恢复、日志和 Trace；随后移除个人旧调度接入。
- [ ] 独立复核高风险变更，fresh fetch 后按依赖顺序发布共享制品与消费者、CI exact-head 读回。
- [ ] 完整 Compose 从 NAS 私有读回交付、保留回退数据和必要证据，清理仅本任务可确认归属的短分支/临时探针。
