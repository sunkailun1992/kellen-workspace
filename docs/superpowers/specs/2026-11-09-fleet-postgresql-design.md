# 个人 Java fleet PostgreSQL 迁移设计

## 当前授权与范围

- 2026-11-09 用户明确要求个人 Java 业务库统一 PostgreSQL，并新增 NAS PostgreSQL。
- 仅处理清单中的 9 个 Java 服务及共享库；7 个 JDBC 消费者为 ai、ai-memory、mcp、message、rag、report、user。gateway、ai-agent 不增加业务数据库。
- 现有 MySQL 供其他开发环境使用，容器、资源、端口、账号、卷和数据全部保留。不得访问或修改公司 schema。仅允许从明确对应的 7 个个人业务 schema 读取迁移数据。
- Nacos、Seata 等现有共用基础服务连接保持，不迁其全局数据库。用户另授权退役 NAS XXL-JOB 服务；美联 dev 先显式关闭执行器，旧调度数据库保留。Neo4j、Qdrant、Elasticsearch 的职责和数据保持。
- 本轮已授权提交推送 main；使用短分支，不强推，不建立 test/preprod。原监控验收仍需完成，新增数据库迁移不能掩盖原来的日志缺口。

## 目标结构

只在 NAS 新增固定官方版本及摘要的 PostgreSQL 18 容器；本机不安装或运行 PostgreSQL 服务，仅保留 JDBC 驱动和迁移客户端。该容器使用独立数据卷。每个个人业务服务拥有独立数据库与最小权限登录角色；管理凭据只保存在本机私有配置和用户的 NAS 完整 Compose。全部容器配置和初始化脚本内联，不依赖额外宿主配置文件。

共享库只提供 PostgreSQL 业务驱动、方言及连接属性，不长期维护双业务数据库分支。master/gray 继续作为业务路由别名，但都使用 PostgreSQL。租户列保持字符串语义，逻辑删除列使用 Boolean；不能依赖 MySQL 隐式类型转换。

PostgreSQL 初始化与迁移脚本使用新的 `db/postgresql/` 路径，保留已执行的历史 MySQL 脚本。Seata `undo_log` 必须在应用启动前完成初始化。DDL 执行器是否吞错需要单独验证，不以 JVM 成功监听代替建表成功。

## 数据迁移与切换

源库只读，在明确停止个人服务写入的窗口使用一致性快照，将记录直接传输到新 PostgreSQL，不生成业务正文 dump 文件。目标在写入前必须为空或严格匹配受管重试状态，不能对未知已有记录执行覆盖、删除或 truncate。

转换按实际列类型完成：Boolean、binary、JSON、定点数、时间、NULL 和原有 ID 分别处理。所有已有 ID 保留，15 个自增列在复制后校准序列。逐表核对列、行数及确定性内容指纹；报告只保存数量、类型和指纹，不保存正文、认证数据或原始 SQL 参数。

切换仅在全部源/目标对账、真实 PostgreSQL 回归和新服务启动检查通过后进行。Nacos 发布须从权威旧配置读回、受限备份、CAS 校验并验证新值。旧 MySQL 数据保留作为回退依据；切换后已有新写入时，不能直接回切并假定两边仍一致。

## 连接与资源预算

- PostgreSQL 起步 1 CPU / 1 GiB，`max_connections=70`，健康检查同时验证 TCP 接受连接和完整初始化标记。
- 业务角色连接上限 10；每服务 master 池最多 4、gray 池最多 2，避免默认连接池相乘超过实例预算。
- 调度最终选定 Conductor OSS；其使用独立 PostgreSQL 库与账号、独立小型 Redis 执行锁实例及账号/前缀，不经过 Seata AT 代理或业务灰度路由。个人 XXL-JOB 源码接入退役；用户随后明确删除 NAS XXL-JOB 服务，先核美联 dev 显式关闭执行器，保留旧调度数据库和数据。
- PostgreSQL 18 数据卷挂父目录 `/var/lib/postgresql`；保留持久化默认保证，不关闭 fsync 或 synchronous_commit。

## 日志与验收

真实 IDEA 新实例已经分别核到唯一新 PID、原生 `idea_rt`、最新隐私扩展和配置引用。免费请求已暴露源端终态日志缺失，必须先修复真实 ECS 编码的 MDC/KVP 键冲突，保持原文、自由 MDC、异常正文与密钥不进入日志。

切库后验证 7 个数据库的真实 JDBC/SQL、租户字符串、Boolean 删除、分页、自增回填、唯一约束及两次 DDL 执行。随后重跑原生启动和既定请求矩阵，逐请求对账 Jaeger 与 ES；模型生成仍限制为既定的 2 次，部分读回只能重查 ledger，不自动重新调用模型。

## 不扩大范围

不并行新增 Quartz、JobRunr、PowerJob、pgvector 或公司环境接入；不重传文档、重建 RAG 图谱、修改原发布 generation。隐藏的模型内部思维链仍不可观测，只验收公开元数据与实际外部执行步骤。

## 调度与任务流最终选择

用户 21:08 明确采用 Conductor OSS，21:11 确认希望覆盖既定任务流与 AI 任务流方向。选型统一为当前维护的 `conductor-oss/conductor`，固定正式镜像和摘要。采用 PostgreSQL 作为状态、队列和索引后端，并使用独立小型 Redis 作为隔离执行锁（共享 Redis 没有持久化 ACL 文件，避免改动或重启其它环境共用实例）；不使用旧 Netflix 归档仓库或把 Orkes 商业专有能力当作开源能力。

平台负责后台定时触发、步骤依赖、分支、并行、超时、重试与持久化恢复；模型执行继续属于 ai-agent，知识检索继续属于 rag。先用不调用付费模型的受控任务验证真实 Worker/HTTP 接入、定时、重试、恢复与可观测性。平台可用不等于所有未来业务流程均已实施，任务输入输出默认使用受限标识或引用，不能把真实敏感正文复制进平台执行历史。
