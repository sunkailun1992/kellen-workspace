# 个人七库 JDBC 流式迁移

此工具的来源和目标只接受固定允许的 `github_dev_ai`、`github_dev_ai_memory`、`github_dev_mcp`、`github_dev_message`、`github_dev_rag`、`github_dev_report`、`github_dev_user`。默认只读计划；显式执行时仅向已审核的空目标进行事务化 INSERT。PostgreSQL 只部署在 NAS，为七个同名独立数据库，各自角色同名，表均在 `public`；本机只运行 Java 17/JDBC 客户端，不安装或运行 PostgreSQL 服务。不连接公司库，不生成业务正文 dump，不安装软件，不创建数据库、角色、schema 或表，不执行删除、覆盖或 truncate。

Java 21 与五个官方依赖只从现有 Gradle 缓存读取并核验固定 SHA-256：MySQL Connector/J 9.6.0、PostgreSQL JDBC 42.7.10、Jackson Core/Databind 3.1.0、Jackson Annotations 2.21。源码编译产物放在忽略的 `build/database-migration/`；缺少固定制品时明确拒绝，不下载其他版本。

## 配置与计划

所有配置、元信息与 credentials 引用位于 Git 外，必须为当前用户所有的 0600 普通文件且不是 symlink。receipt 目录由操作者预先建立为 0700；工具不会替未知目录改权限。配置字段必须精确匹配下面的形状，示例中的路径只是占位符：

```json
{
  "schema_version": 1,
  "source_properties_ref": "/secure/mysql.properties",
  "target_properties_refs": {
    "github_dev_ai": "/secure/pg-ai.properties",
    "github_dev_ai_memory": "/secure/pg-ai-memory.properties",
    "github_dev_mcp": "/secure/pg-mcp.properties",
    "github_dev_message": "/secure/pg-message.properties",
    "github_dev_rag": "/secure/pg-rag.properties",
    "github_dev_report": "/secure/pg-report.properties",
    "github_dev_user": "/secure/pg-user.properties"
  },
  "metadata_ref": "/secure/mysql-schema-metadata.json",
  "metadata_sha256": "替换为已审核元信息文件的64位小写SHA256",
  "receipt_dir": "/secure/migration-receipts",
  "max_rows_per_table": 1000000,
  "max_row_bytes": 1048576,
  "query_timeout_seconds": 600,
  "write_window_id": "explicit_stopped_writer_window"
}
```

每份 Java Properties 精确包含 `url`、`user`、`password` 三键。源 URL 的 catalog 必须为 `github_dev_ai`，这一连接使用全限定 catalog 读取其余六库。目标 URL 的 database 和实际 `current_database/current_user` 必须匹配对应清单键，`current_schema` 必须为 `public`。URL 不允许 userinfo、密码参数或任意驱动选项；TLS 等有限选项仍来自私有文件。使用标准 Java Properties 的转义规则保存密码，不将密码放命令行或示例。

迁移客户端直接使用 JDBC 驱动，不经过 Druid 或 Seata 代理。MySQL URL 的 `connectTimeout`、`socketTimeout` 单位为毫秒，分别限制在 1–10000、1–600000；PostgreSQL URL 的两项单位为秒，分别限制在 1–10、1–600。`query_timeout_seconds` 是语句超时秒数，范围 1–600。不要把业务连接池的毫秒字段值直接复制进 PostgreSQL URL；显式 URL 选项仍须通过有限白名单、格式、重复键和范围检查。

```bash
python3 scripts/database/migrate.py --config /secure/migration.json
```

默认只计划：源使用 `REPEATABLE_READ` 的只读一致性快照覆盖七库，目标使用只读事务。它会核对权威 MySQL 元信息、目标表/列/类型/精度、自增映射、源空 `undo_log`，再读取数量与指纹。源、目标都先在服务器计算行数和最大单行原始字节，超限时不向客户端传输大行。行值通过有界 Reader/InputStream 读取；fingerprint 只在内存保存每行 SHA-256 并排序，保留重复行的数量，最多受 `max_rows_per_table` 限制。计划不会写 receipt、取写锁或执行 INSERT。

`plan_sha256` 绑定元信息、源 schema、源全部内容指纹、目标 schema、私有连接 authority 摘要、迁移策略和写入窗口标记。对象字段排序、JSON 数字与 Decimal 按语义规范化；JSON 数组顺序、SQL NULL、字符串 `NULL`、binary、微秒 LocalDateTime 分别保留。计划和错误只输出元数据；SQLException 只可输出固定错误码、SQLSTATE 与 vendor code，不输出异常正文、参数、URL 或凭据。

唯一 schema 例外是：已证明源和目标 `undo_log` 为空时，允许目标使用官方 PostgreSQL Seata `undo_log` 的额外 generated integer/bigint `id`。其他业务表不允许额外或缺失列。时间精度不得降低；源默认生成值与真实 generated column 分开处理。

## 目标准备与启动顺序

1. 在 NAS 预先建立七个目标数据库、同名角色，以及经过审核的 PostgreSQL 表、索引、约束和必要公共基础表。工具不从 MySQL 字符串自动生成 DDL，不替代业务仓库的 PG 脚本审查。
2. 首次迁移的目标表必须全部为空。只建立结构，不执行会填充记录的 bootstrap/种子脚本，也不启动业务应用；幂等种子仍然可能产生新记录并使目标不再为空。
3. 停止源和目标全部业务写入者后生成本次计划，再按下述 SHA 门禁执行。独立调度平台的状态库、共用中间件数据库和公司数据库不在七库 allowlist 内，不能通过此入口顺带迁移。
4. 全部七库达到 VERIFIED 后，用新连接独立读回数量、内容指纹和序列，再由发布流程切换业务配置并启动服务。应用启动后的新 PG `ddl_history` 记录、合法初始化数据和正常业务写入属于另一个验收阶段，不再与迁移时的空目标或初始指纹混为一谈。

内容指纹证明的是所定义类型规范化后的数据一致性；它不证明 MySQL 与 PostgreSQL 的 collation、空格比较、排序和所有查询行为完全等价。数据库方言、租户、逻辑删除、唯一约束和业务行为仍需独立验证。

## 执行与恢复

操作者必须先停止七库全部业务写入者，并在全部数据库 VERIFIED 前保持目标业务客户端停止。工具无法替操作者证明外部写入窗口；仅有 `write_window_id` 不代表已经停写。

```bash
python3 scripts/database/migrate.py --config /secure/migration.json \
  --execute --expected-plan-sha256 <本次停写窗口的plan_sha256>
```

执行先重新计算完整计划并比较 SHA，然后验证全部七个目标均为空或严格匹配已知 receipt。每库各自一个事务，先锁定全部目标表并重查空表/schema，再通过参数化 INSERT 保留原 ID，核对事务内数量与内容指纹后 commit。失败只 rollback 当前未 commit 的库；前面已经成功的库不会被删除或覆盖。不会自动切换 Nacos。

receipt 依次记录 PREPARED → COMMIT_INTENT → COMMITTED → VERIFIED，通过受限原子替换、CAS 和 fsync 保存。源当前 AUTO_INCREMENT 计数单独取摘要并绑定计划；目标下一 ID 至少为源 AUTO_INCREMENT 与已迁 MAX(id)+1 的较大值，防止重新分配历史删除/回滚号段。序列 `setval` 不属于可回滚数据事务，因此放在数据 commit 后；校准成功才记 VERIFIED。执行阶段持有独立受限文件锁，拒绝第二个执行者。

SQL 中的库、表、列标识符统一先通过 `[a-z][a-z0-9_]{0,62}` 完整匹配，再由 `MigrationCore.mysql()` / `pg()` 按对应方言引用。标识符属于 SQL 结构，不能用 `?` 替代；行值及序列函数参数仍使用 `PreparedStatement` 绑定。`FleetMigration` 五处 `nosemgrep` 仅针对 `formatted-sql-string` 无法识别上述自定义守卫的误报，保留就地依据，不忽略整个文件或其他规则。

```bash
python3 scripts/database/migrate.py --config /secure/migration.json \
  --execute --resume --expected-plan-sha256 <原plan_sha256>
```

恢复必须显式指定。工具校验相同 source/schema/authority/window/plan 及全部目标内容；只有自有 receipt 的完整内容可跳过复制并重做安全序列校准。PREPARED/COMMIT_INTENT 对应的空目标可以重试；commit 返回不确定时，只有全部表精确等于源指纹才能确认已提交。未知非空目标、部分内容、源变化或 receipt 变化均拒绝，不尝试修正数据。故障后若源重新开放写入，不能直接沿用旧计划恢复。

服务已切换并恢复写入后，不能用原计划再次 `--execute` 或 `--resume` 来验证健康；新增历史或业务内容会使旧指纹失效，这是保护条件。保留原 receipts，用独立只读验收或重新审核的专项方案处理后续问题，不删表、清库或改写 receipt 来强行通过。

真实迁移、实际 PostgreSQL 字段/序列回读和全部七库对账必须单独验收，合成测试通过不代表已经迁移。全部 VERIFIED 后仍需由主流程验收目标业务/JDBC/租户/逻辑删除/DDL，再决定是否切换。

## 无真实数据库的回归

```bash
python3 -m unittest discover -s tests -p test_database_migration.py -v
```

回归使用临时私有文件与合成 JDBC proxy：默认计划零写、第二库失败时 rollback 以及保留第一库、显式恢复不重复写入、未知非空拒绝、JSON/Decimal/Boolean/NULL/引号/binary/微秒时间、限制、私有权限/symlink、JSON重复键及异常不泄漏。独立 Java 回归在没有现成 JDK17/固定依赖缓存的环境明确 skip，编译错误不会按缺件跳过；本机有依赖时必须实际执行。真实 synthetic PostgreSQL 验证只在已授权 NAS 隔离库进行，本机不安装或运行 PostgreSQL。

SQL 守卫回归还核对恶意标识符在任何 JDBC prepare/execute 前被拒绝、合法保留字及 63 字符边界、危险正文只进入绑定参数、实际 `LOCK` / `INSERT` / `MAX` 引用，以及序列校准的四项绑定。这些检查使用合成连接，不连接数据库；独立 `sql-guards` 入口用于针对守卫运行回归。

显式提供 `FLEET_PG_TEST_URL`、`FLEET_PG_TEST_USERNAME`、`FLEET_PG_TEST_PASSWORD` 时，同一测试入口还执行 NAS 专用库中的 6 项真实 codec/序列回归，并清理自身随机 schema。测试只接受 `fleet_pg_test_` 前缀的 PG 18 数据库，拒绝七个业务目标；操作者须使用获准的专用测试角色。凭据由受限私有环境注入，不写进命令、测试资源或回执。未提供测试 URL 时该真实数据库用例会 skip，不计为通过；提供 URL 却缺少其它必要变量会失败。临时 schema 清理成功也不代表实际七库迁移已经验收。
