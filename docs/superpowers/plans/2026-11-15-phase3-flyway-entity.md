# 第3阶段Flyway与实体基类规范化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 7个有库服务的建表与初始化全部改由各自的Flyway迁移管理，删除MyBatis-Plus DDL方式；实体基类按芋道两层只留审计字段并自动填充创建人、修改人；数据源改用dynamic-datasource原生配置与其Seata集成；删除没用的列与表；utils只发1.6.9一个版本。

> **执行中修订（2026-11-16、17）：** 基类字段、迁移编号、Seata 与 OSS 的做法按 spec「修订记录」调整：旧字段全部保留、不删列，审计列按主流多数为 `create_by`、`update_by`，新增 `create_dept`，Seata 固定开启并由 utils 提前迁移。下面各步骤里「按芋道」「V4 删列」等初版写法以 spec 为准。

**Architecture:** 先在NAS临时库实测Flyway 11.14.1与PostgreSQL 18，以及baseline接管真实库后结构与空库一致，同时只读统计删除候选。utils分三个提交后发布1.6.9；Nacos先让新旧数据源键并存；7个服务各一个提交（迁移、实体与SQL改名、配置、真库迁移测试、CI加PostgreSQL 18服务容器）；最后整体运行验证并删除旧键。

**Tech Stack:** Gradle 9.9.1、JDK 21、Spring Boot 4.0.9、Flyway 11.14.1、PostgreSQL 18（NAS容器`orb-postgres`）、pgjdbc 42.7.14、MyBatis-Plus 3.5.18.1、dynamic-datasource 4.6.0、Druid 1.2.28、Seata 2.6.0、JUnit 5。

**Spec:** `docs/superpowers/specs/2026-11-15-phase3-flyway-entity-design.md`

## Global Constraints

- 范围：utils、user、ai、ai-memory、message、mcp、rag、report与kellen-workspace；ai-agent只改文档；gateway、admin-web、rpc-api不改；美联项目不动。
- Flyway：版本由Spring Boot依赖管理（11.14.1），utils以`api`声明`org.springframework.boot:spring-boot-starter-flyway`与`org.flywaydb:flyway-database-postgresql`；utils不放迁移SQL。
- 迁移文件：各服务`src/main/resources/db/migration/V<n>__<说明>.sql`；SQL不写schema前缀（测试在独立schema里执行）；已提交的迁移文件不再修改，改结构只新增版本；每段语句上方用`--`注释写用途。
- Flyway配置（各服务`application.yml`，不分环境）：`spring.flyway.locations: classpath:db/migration`、`baseline-on-migrate: true`、`baseline-version: 1`、`validate-on-migrate: true`、`clean-disabled: true`；`url`、`user`、`password`分别引用`${spring.datasource.dynamic.datasource.master.url}`、`.username`、`.password`。
- 列名：`create_time`、`update_time`、`creator`、`updater`、`is_deleted`；字段：`createTime`、`updateTime`、`creator`、`updater`、`deleted`。
- 基类：`com.kellen.entity.EntityBase`（抽象，只有上面5个字段）与`com.kellen.entity.TenantEntityBase extends EntityBase`（只有`tenantId`）。`id`仍为字符串雪花ID；现在用乐观锁的实体继续声明`@Version`；`label`删除；`sorting`本阶段不改名；admin-web在用的`sorting`、`code`、`version`、`tenantId`接口字段名不变。
- 自动填充：插入填`createTime`、`updateTime`、`creator`、`updater`、`tenantId`，更新填`updateTime`、`updater`；创建人、修改人取`UserContextHolder.getUserId()`，为空时不填；实体上已有的`creator`不覆盖。
- 时间：审计列`timestamp`配`LocalDateTime`；ai存储台的`started_at`、`finished_at`、`credential_updated_at`等真实时刻列保留`timestamptz`配`Instant`。
- 数据源：`spring.datasource.dynamic.primary: master`、`strict: true`、`seata: true`、`seata-mode: AT`；Druid连接池master `max-active: 4`、gray `max-active: 2`、`initial-size: 1`、`min-idle: 1`、`max-wait: 3000`、`connect-timeout: 3000`、`socket-timeout: 30000`、`validation-query: SELECT 1`、`validation-query-timeout: 3`、`test-on-borrow: true`、`test-while-idle: true`、`keep-alive: true`、`time-between-eviction-runs-millis: 30000`、`keep-alive-between-time-millis: 60000`；`seata.yaml`加`seata.enable-auto-data-source-proxy: false`；数据源名master、gray不变。
- 删除规则：服务代码、手写SQL、HTTP接口、rpc-api与admin-web都不读写，且库里整列（全部为NULL或等于列默认值）或整表为空才删；有数据的保留，实体上声明对应字段并在台账与最终报告里列出。
- 版本：utils 1.6.8→1.6.9；user 1.4.2→1.4.3、ai 1.7.8→1.7.9、ai-memory 1.1.9→1.2.0、message 1.2.7→1.2.8、mcp 1.2.8→1.2.9、rag 1.3.2→1.3.3、report 1.2.7→1.2.8；推送前核对远端没有占用。
- 文档规则文本（替换各仓`AGENTS.md`、README、`docs/ai-coding`里关于`ddl_history`、`common-infra-schema.sql`、`PostgresqlDdl`、`IDdl`的旧规则）：「表结构只由本服务`src/main/resources/db/migration`的Flyway迁移管理；已提交的迁移文件不修改，改结构只新增`V<下一个>__<说明>.sql`；新库保持空库，由服务首次启动时的Flyway建表，不手工执行任何公共脚本。」`docs/superpowers/`、`archive/`与已退役说明等历史文档不改。
- 构建一律JDK 21：`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`。
- Git：开工、提交前、推送前都`git fetch`；每个任务从最新`origin/main`建短分支，本机验证全过后快进合入`main`并推送、删短分支；读回该精确提交的CI为success再开下一个任务；不强推。
- NAS：只用`ssh -o BatchMode=yes qw-nas`与`sudo -n docker exec orb-postgres …`；库内命令以容器内`postgres`本地免口令执行，SQL经stdin传入；临时库与临时角色名一律以`fleet_pg_test_phase3`开头。不取出、不打印任何口令；临时角色口令在本机随机生成，只存`~/.config/kellen-github/phase3/fleet-pg-test.env`（0600），测试经`FLEET_PG_TEST_URL`、`FLEET_PG_TEST_USERNAME`、`FLEET_PG_TEST_PASSWORD`读取。
- 运行态：7100等端口当前被另一项目的Codex任务占用。运行态任务开始前先确认端口空闲且不属于其他项目，被占用就停下请用户处理，不向其他项目的服务发请求。
- 凭据：私有Maven凭据只经`~/.config/kellen-github/observability-20261106/run-maven-release.py`注入；Nacos凭据只经私有`dev.env`与现有脚本；登录口令与灰度签名密钥不落盘、不打印。
- 已定裁定（规格未写死之处）：
  - 迁移测试用仓库现有的`FLEET_PG_TEST_URL`真库测试方式（本机连NAS临时库，CI用GitHub Actions的`postgres:18`服务容器）代替规格写的Testcontainers：本机没有Docker。
  - ai的7个实体保留`AiGovernanceEntity`（字段改名`updateTime`），不改继承：`ChatMemoryEntity`是物理删除（清空记忆接口），且没有实体在用手写条件做逻辑删除，符合规格「有物理删除语义的保持物理删除」。
  - user的`id`、`version`放在13个实体共用的`AuthEntity`里；ai-memory、message的实体各自声明。
  - 不删除任何`version`列：现在继承`EntityBase`的实体都在用乐观锁，行为不变。
  - 数据源全局参数放各服务`application.yml`（原为utils代码默认值，不分环境）；Nacos只放各库地址与账号。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-15-phase3-flyway-entity/progress.md`。

## Review Focus

1. 真实库与V1快照有细微差别（手工加过的索引、默认值、后来补的列）：baseline后V3改名或V4删列失败，或新旧库结构分岔。期望：每个服务的真实库副本经baseline迁到最新后，结构与空库迁到最新完全一致。→ 任务9–13的「副本等价」步骤。
2. 空库首次启动时，MyBatis或启动期写库的Bean早于Flyway访问数据库（MyBatis-Plus的顺序探测器登记在Boot 3的接口上，Boot 4下是否生效未知），报「表不存在」。期望：迁移先完成。→ 任务3 `FlywayMybatisOrderingLiveTest`。
3. 手写SQL、Mapper注解或排序白名单里残留旧列名：启动不报错，调用时才报SQL错误。期望：没有残留。→ 任务9–13的检索关口与真库测试。
4. 没有登录用户的写入（种子、后台任务、Dubbo调用）：创建人、修改人应为空，不报错，也不能写成别人的ID。→ 任务4 `insertWithoutLoginUserLeavesCreatorNull`。
5. ai存储三表的审计列改类型时数值偏移8小时。期望：同一时刻按Asia/Shanghai读出的本地时间不变。→ 任务12 `storageAuditTimesKeepShanghaiWallClock`。

---

### Task 1: 提交计划

**Files:**
- Create: `kellen-workspace/docs/superpowers/plans/2026-11-15-phase3-flyway-entity.md`

- [ ] **Step 1:** `python3 -m unittest discover -s tests`。Expected：OK。
- [ ] **Step 2:** 提交`docs: 第3阶段Flyway与实体基类执行计划`，交付并读回CI。

### Task 2: 实测与只读盘点（不改任何仓库与现有库）

**Files（台账目录，Git外）:**
- Create: `flyway-runner/`：独立Gradle小工程，依赖`org.flywaydb:flyway-core`与`flyway-database-postgresql` 11.14.1、`org.postgresql:postgresql` 42.7.14；`main(url, locationsDir, mode)`，`mode`为`fresh`或`baseline`（后者开`baselineOnMigrate`、`baselineVersion=1`）；账号口令只从`FLEET_PG_TEST_USERNAME/PASSWORD`读；输出只有已应用版本号与Flyway告警。
- Create: `nas_pg.py`：子命令`create <db>`、`drop <db>`、`clone <src> <dst>`（容器内`pg_dump | psql`，数据不出NAS）、`schema <db>`（容器内`pg_dump -s --no-owner --no-privileges`）、`query <db>`（stdin读SQL，只打印结果计数）。
- Copy: 会话草稿区的`canary_walk.py`、`maven-readback.py`到台账目录。
- Create: `user-V1-draft/V1__baseline.sql`：`user-schema.sql`加`user-bootstrap.sql`去掉schema前缀，末尾加Seata 2.6.0 PostgreSQL版`undo_log`（与utils现有`common-infra-schema.sql`同一定义），不含`ddl_history`。

- [ ] **Step 1:** 新建台账，首行`# SDD ledger — plan: <本计划路径>`；`nas_pg.py create fleet_pg_test_phase3`并建同名角色，口令写入私有env。Expected：可连接，`server_version_num` ≥ 180000。
- [ ] **Step 2:** `nas_pg.py create fleet_pg_test_phase3_fresh_user`，`flyway-runner` fresh模式把user草稿V1迁到该库。Expected：应用版本1；台账记下Flyway对PostgreSQL 18有没有告警及原文。
- [ ] **Step 3:** `clone github_dev_user fleet_pg_test_phase3_clone_user`，baseline模式迁移。Expected：登记版本1为baseline，不执行V1里的语句。
- [ ] **Step 4:** 两库`schema`输出去掉`flyway_schema_history`与`ddl_history`段后比对。Expected：无差异；有差异逐项记台账，修草稿V1后从Step 2重跑。
- [ ] **Step 5:** 对7个业务库只读统计：每张业务表行数；`code`、`description`、`label`、`sorting`、`version`每列「非空且不等于列默认值」的行数；ai的8张未引用表行数。只记数量。
- [ ] **Step 6:** 按「删除规则」逐表写出V4（ai另含V5）的删除清单与保留理由，写进台账。user的租户、角色、部门、用户、资源五表保留`code`、`sorting`；有数据的列一律保留并列出。
- [ ] **Step 7:** 删除两个副本库（`fleet_pg_test_phase3`保留给后续测试）。Expected：只剩`fleet_pg_test_phase3`。

### Task 3: utils删除MyBatis-Plus DDL方式并引入Flyway

**Files:**
- Delete: `utils/src/main/java/com/kellen/config/datasource/PostgresqlDdlApplicationRunner.java`、`PostgresqlDdlAutoConfiguration.java`、`PostgresqlDdlScriptPolicy.java`
- Delete: `utils/src/main/resources/db/postgresql/common-infra-schema.sql`、`utils/src/main/resources/db/common-infra-schema.sql`
- Delete: `utils/src/test/java/com/kellen/config/datasource/PostgresqlDdlAutoConfigurationTest.java`、`PostgresqlDdlScriptPolicyTest.java`、`PostgresqlDdlAtomicityTest.java`、`PostgresqlDdlConnectionFailureTest.java`
- Modify: `utils/src/main/resources/META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`（删`PostgresqlDdlAutoConfiguration`一行）
- Modify: `utils/build.gradle`（`api 'org.springframework.boot:spring-boot-starter-flyway'`、`api 'org.flywaydb:flyway-database-postgresql'`）
- Create: `utils/src/test/resources/db/test/undo_log.sql`（Seata 2.6.0 PostgreSQL版，不带schema前缀）
- Modify: `PostgresqlLiveContractTest`、`PostgresqlSqlContractTest`、`SeataDubboPostgresqlLiveContractTest`、`SeataProbeNode`里对`common-infra-schema.sql`的引用改为`db/test/undo_log.sql`
- Test: `utils/src/test/java/com/kellen/config/flyway/OldDdlModeRemovedTest.java`、`utils/src/test/java/com/kellen/config/flyway/FlywayMybatisOrderingLiveTest.java`
- Create（测试资源）: `utils/src/test/resources/db/test/ordering/V1__ordering_probe.sql`（建表`ordering_probe(id varchar(64) primary key)`）与测试内的`OrderingProbeMapper`（`@Select("SELECT count(*) FROM ordering_probe")`）

**Interfaces:**
- Produces：utils的`api`依赖带上Flyway与其PostgreSQL模块；`PostgresqlDdl*`类与公共建表脚本不再存在，服务不能再实现`IDdl`或引用这些脚本。

- [ ] **Step 1: 写失败测试**
  - `OldDdlModeRemovedTest.autoConfigurationNoLongerRegistersDdlRunner`：读`AutoConfiguration.imports`，断言不含`PostgresqlDdlAutoConfiguration`。
  - `OldDdlModeRemovedTest.oldDdlClassesAndScriptsAreGone`：`Class.forName("com.kellen.config.datasource.PostgresqlDdlApplicationRunner")`抛`ClassNotFoundException`；`db/postgresql/common-infra-schema.sql`不在classpath。
  - `OldDdlModeRemovedTest.flywayAndPostgresqlModuleAreOnApiClasspath`：`org.flywaydb.core.Flyway`与`org.flywaydb.database.postgresql.PostgreSQLDatabaseType`可加载。
  - `FlywayMybatisOrderingLiveTest`（`@EnabledIfEnvironmentVariable(named = "FLEET_PG_TEST_URL", matches = "jdbc:postgresql:.*")`）：在独立schema上启动只含DataSource、Flyway、MyBatis-Plus自动配置的上下文，迁移目录`classpath:db/test/ordering`（建一张表），一个在初始化时经Mapper查询该表的Bean，断言上下文启动成功、查询返回0行。
- [ ] **Step 2:** `./gradlew test --tests 'com.kellen.config.flyway.*'`（带私有env）。Expected：`OldDdlModeRemovedTest`三条失败；`FlywayMybatisOrderingLiveTest`因无Flyway失败。
- [ ] **Step 3:** 按Files删改。若Boot 4下MyBatis-Plus的顺序探测器不生效，在utils新增`MybatisFlywayDependsOnDetector implements DependsOnDatabaseInitializationDetector`（探测`SqlSessionFactory`与`SqlSessionTemplate`），用Boot 4的接口登记到`META-INF/spring.factories`；生效则不加，台账记录。
- [ ] **Step 4:** `./gradlew test javadoc`（带与不带私有env各一次）。Expected：BUILD SUCCESSFUL，javadoc无错误。
- [ ] **Step 5:** 提交`refactor!: 删除MyBatis-Plus DDL建表方式，改由各服务Flyway迁移`，交付并读回CI。

### Task 4: utils实体基类两层与自动填充

**Files:**
- Modify: `utils/src/main/java/com/kellen/entity/EntityBase.java`
- Create: `utils/src/main/java/com/kellen/entity/TenantEntityBase.java`
- Modify: `utils/src/main/java/com/kellen/config/mybatis/MyMetaObjectHandler.java`
- Modify: `utils/src/main/java/com/kellen/datapermission/DataPermissionProperties.java`（`defaultUserColumn = "creator"`）
- Modify: `GeneralConvertorTest`、`PostgresqlSqlContractTest`、`PostgresqlLiveContractTest`、`DataPermissionSqlHandlerTest`里的旧字段名、旧列名
- Test: `utils/src/test/java/com/kellen/entity/EntityBaseTest.java`、`utils/src/test/java/com/kellen/config/mybatis/MyMetaObjectHandlerTest.java`

**Interfaces:**
- Produces：`abstract class EntityBase implements Serializable`：`LocalDateTime createTime`（`@TableField(fill = FieldFill.INSERT)`）、`LocalDateTime updateTime`（`fill = INSERT_UPDATE`）、`String creator`（`fill = INSERT`）、`String updater`（`fill = INSERT_UPDATE`）、`Boolean deleted`（`@TableLogic(value = "false", delval = "true")`、`@TableField("is_deleted")`）；两个时间沿用现有`@DateTimeFormat`与`@JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss", timezone = "GMT+8")`。
- Produces：`abstract class TenantEntityBase extends EntityBase`：`String tenantId`（`@TableField(value = "tenant_id", fill = FieldFill.INSERT)`）。
- Produces：`MyMetaObjectHandler`按Global Constraints的自动填充规则工作；租户填充沿用`TenantProperties`。

- [ ] **Step 1: 写失败测试**
  - `EntityBaseTest.entityBaseDeclaresOnlyAuditFields`：`EntityBase`是抽象类，声明字段按顺序为`createTime, updateTime, creator, updater, deleted`，类型为`LocalDateTime, LocalDateTime, String, String, Boolean`；`deleted`的`@TableLogic`值为`false`/`true`、`@TableField`值为`is_deleted`。
  - `EntityBaseTest.tenantEntityBaseAddsOnlyTenantId`：声明字段只有`tenantId`，`@TableField`为`tenant_id`、`fill = INSERT`。
  - `MyMetaObjectHandlerTest.insertFillsAuditFieldsAndTenantFromContext`：上下文用户ID`u-1`、租户`t-1`，插入填充后`createTime`等于`updateTime`且非空，`creator`、`updater`为`u-1`，`tenantId`为`t-1`。
  - `MyMetaObjectHandlerTest.insertWithoutLoginUserLeavesCreatorNull`：无上下文用户时`creator`、`updater`为null，两个时间已填，不抛异常。
  - `MyMetaObjectHandlerTest.insertKeepsExplicitCreator`：实体`creator`预设`seed`，填充后仍为`seed`。
  - `MyMetaObjectHandlerTest.updateRefreshesUpdateTimeAndUpdater`：更新填充后`updateTime`晚于原值，`updater`为当前用户，`createTime`与`creator`不变。
  - `DataPermissionSqlHandlerTest`里默认用户列的期望改为`creator`。
- [ ] **Step 2:** `./gradlew test --tests 'com.kellen.entity.*' --tests 'com.kellen.config.mybatis.MyMetaObjectHandlerTest' --tests 'com.kellen.datapermission.*'`。Expected：失败于字段与填充断言。
- [ ] **Step 3:** 按Interfaces实现；`id`、`code`、`description`、`label`、`sorting`、`version`、`tenantId`从`EntityBase`移除。
- [ ] **Step 4:** `./gradlew test javadoc`（带与不带私有env）。Expected：全过。
- [ ] **Step 5:** 提交`refactor!: 实体基类按芋道两层，审计字段改名并自动填充创建人修改人`，交付并读回CI。

### Task 5: utils数据源改用dynamic-datasource原生配置

**Files:**
- Delete: `utils/src/main/java/com/kellen/config/datasource/DynamicDataSourceConfig.java`
- Delete: `DynamicDataSourceConfigTest`、`PostgresqlDruidTimeoutTest`、`PostgresqlDruidTimeoutLiveTest`、`PostgresqlDataSourceContractTest`
- Test: `utils/src/test/java/com/kellen/config/datasource/NativeDynamicDataSourceTest.java`

**Interfaces:**
- Produces：服务`application.yml`统一使用的数据源块（任务9–13照抄，`<库名>`与私有变量名按服务替换的部分在Nacos，见任务8）：

```yaml
spring:
  datasource:
    dynamic:
      primary: master
      strict: true
      seata: true
      seata-mode: AT
      druid:
        initial-size: 1
        min-idle: 1
        max-wait: 3000
        connect-timeout: 3000
        socket-timeout: 30000
        validation-query: SELECT 1
        validation-query-timeout: 3
        test-on-borrow: true
        test-while-idle: true
        keep-alive: true
        time-between-eviction-runs-millis: 30000
        keep-alive-between-time-millis: 60000
      datasource:
        master:
          type: com.alibaba.druid.pool.DruidDataSource
          druid:
            max-active: 4
        gray:
          type: com.alibaba.druid.pool.DruidDataSource
          druid:
            max-active: 2
```

- [ ] **Step 1: 写失败测试** `NativeDynamicDataSourceTest.nativePropertiesBuildSeataProxiedMasterAndGray`：用`ApplicationContextRunner`加载dynamic-datasource、Druid与Seata自动配置，属性取上面的块，master与gray的`url`为`jdbc:postgresql://127.0.0.1:1/fleet_pg_test_none`、账号口令为合成值、`lazy: true`（不连库）；断言`DataSource`是`DynamicRoutingDataSource`，`getDataSources()`键为`master`、`gray`，各自包着Seata的`DataSourceProxy`，Druid的`maxActive`为4与2、`connectTimeout`为3000、`socketTimeout`为30000、`maxWait`为3000。再加`noCustomDataSourceBeanRemains`：上下文里不存在名为`master`、`gray`的独立`DruidDataSource` Bean。
- [ ] **Step 2:** `./gradlew test --tests 'com.kellen.config.datasource.NativeDynamicDataSourceTest'`。Expected：失败（自写配置仍在，或键未生效）。
- [ ] **Step 3:** 删除自写配置与旧测试；属性键若与dynamic-datasource 4.6.0实际不符，以其源码为准改测试与上面的块，台账记录。
- [ ] **Step 4:** `./gradlew test javadoc`（带与不带私有env）。Expected：全过。
- [ ] **Step 5:** 提交`refactor!: 数据源改用dynamic-datasource原生配置与其Seata集成`，交付并读回CI。

### Task 6: utils文档

**Files:**
- Modify: `utils/AGENTS.md`、`utils/README.md`、`utils/docs/ai-coding/UTILS_TOOL_CATALOG.md`、`PROJECT_CODING_SPEC.md`（公共治理字段改为新基类与新列名，建表规则换成「文档规则文本」）、`NACOS_CONFIG_SPEC.md`（数据源键）、`AI_AUTOMATION_WORKFLOW.md`、`AI_CODING_GUIDE.md`、`AI_ENGINEERING_GUARDRAILS.md`、`SECURITY_CODING_SPEC.md`

- [ ] **Step 1:** `rg -n 'ddl_history|common-infra-schema|PostgresqlDdl|IDdl|createDateTime|modifyDateTime|createName|modifyName|isDelete|create_date_time|modify_date_time|create_name|modify_name|is_delete\b' utils --glob '*.md' --glob '!**/archive/**' --glob '!docs/superpowers/**' --glob '!docs/postgresql-fleet-foundation.md' --glob '!docs/seata-live-contract.md'`，逐处按新规则改。Expected：改完后无输出。
- [ ] **Step 2:** 提交`docs: 第3阶段建表与基类规则同步`，交付并读回CI。

### Task 7: 发布utils 1.6.9

**Files:**
- Modify: `utils/build.gradle`（`version = '1.6.9'`）、`utils/README.md`（1.6.9变更说明：Flyway、基类两层与自动填充、原生数据源、删除旧建表方式）

- [ ] **Step 1:** 核对远端tag、在途分支与私有Maven没有1.6.9。Expected：未占用。
- [ ] **Step 2:** `./gradlew clean test javadoc`与仓库密钥扫描；`./gradlew dependencyInsight --dependency flyway-core --configuration runtimeClasspath`与`--dependency flyway-database-postgresql`。Expected：全过；两者解析为11.14.1。
- [ ] **Step 3:** 提交`build: 发布utils 1.6.9`，交付并读回CI；记下完整提交SHA（任务9–13的CI检出用）。
- [ ] **Step 4:** 从该提交经`run-maven-release.py`发布`com:utils:1.6.9`，用`maven-readback.py`读回POM、JAR、sourcesJar、javadocJar与metadata并核对SHA-256。Expected：读回一致。

### Task 8: Nacos新增数据源新键（旧键保留）

**Files（utils/ops/github-dev）:**
- Modify: `nacos/DEFAULT_GROUP/{ai,ai-memory,mcp,message,rag,report,user}-spring.yaml`：在`postgresql.*`、`postgresql-gray.*`之外新增`spring.datasource.dynamic.datasource.master`与`gray`的`url`、`username`、`password`、`driver-class-name`，取值与旧键相同（gray沿用master的值），逐行注释并说明旧键在任务16删除。
- Modify: `nacos/DEFAULT_GROUP/seata.yaml`：加`seata.enable-auto-data-source-proxy: false`及注释。
- Modify: `nacos/catalog.json`与模板头部`# 用途：`（只在说明需要变时）。
- Create（台账目录）：按第2阶段逻辑重建`nacos_inventory.py`（列出并备份远端全部dataId）、`nacos_prepare_previous.py`（JSON快照还原为远端原文目录）、`nacos_verify_candidate.py`（候选与远端逐键比对，差异必须恰好等于本任务新增的键）。

- [ ] **Step 1:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`。Expected：改模板后全过（含逐行注释检查）。
- [ ] **Step 2:** 只读盘点并备份远端；还原远端原文目录；保值渲染：`render_nacos.py --output <新空目录> --remote <原文目录> --keep-remote ai-agent-spring.yaml --keep-remote rag-spring.yaml`。Expected：21个候选。
- [ ] **Step 3:** `nacos_verify_candidate.py`。Expected：只有7份服务配置新增数据源键与`seata.yaml`新增一个键，其余键、值、列表顺序一致。
- [ ] **Step 4:** `sync_nacos.py --publish --previous <原文目录>`，再`maintain_nacos_catalog.py --apply`同步描述；最后只读盘点复核。Expected：21项读回一致、描述一致。
- [ ] **Step 5:** 先按任务15 Step 1确认端口空闲（被占用就停下请用户处理），再在新旧键并存时启动一个旧版本服务（user 1.4.2的bootJar）核对启动与登录不受影响，然后停掉。Expected：健康UP、登录200。
- [ ] **Step 6:** 提交`chore(ops): Nacos新增dynamic-datasource数据源键并关闭Seata自动代理`（只动ops，不升utils版本），交付并读回CI。

### Task 9: user迁移到Flyway与新基类

**Files:**
- Create: `user/src/main/resources/db/migration/V1__baseline.sql`（任务2的草稿，与副本等价后的定稿）、`V2__drop_ddl_history.sql`、`V3__rename_audit_columns.sql`、`V4__drop_unused_columns.sql`（按台账清单；删列前用`DO`块核对整列为空，不为空`RAISE EXCEPTION`）
- Delete: `user/src/main/java/com/kellen/bean/PostgresqlDdl.java`、`user/src/main/resources/db/postgresql/`、`user/src/main/resources/db/`下5个MySQL脚本
- Modify: `user/src/main/java/com/kellen/auth/entity/AuthEntity.java`（继承`TenantEntityBase`，声明`@TableId(type = IdType.ASSIGN_ID) String id`、`@Version Integer version`，保留`type`、`state`）；`AuthTenant`、`AuthRole`、`AuthDept`、`AuthUser`、`AuthResource`声明`String code`与`Integer sorting`；台账「有数据保留」的列在对应实体声明
- Modify: `user/src/main/java/com/kellen/auth/service/query/Auth{Dept,OAuthClient,Resource,Role,Tenant,User}ServiceQuery.java`排序白名单的旧键与旧列名；`AuthOAuthClientMapper.java`、`AuthRoleResourceMapper.java`里的旧列名；VO里的审计字段改名
- Modify: `user/src/main/resources/application.yml`（Flyway块与任务5的数据源块）、`user/build.gradle`（`utilsVersion = '1.6.9'`、`version = '1.4.3'`）、`user/.github/workflows/ci.yml`（utils检出`ref:`改为任务7的SHA；加`services.postgres`：`image: postgres:18`、`POSTGRES_DB: fleet_pg_test_ci`、`POSTGRES_HOST_AUTH_METHOD: trust`、端口5432；作业环境`FLEET_PG_TEST_URL: jdbc:postgresql://localhost:5432/fleet_pg_test_ci`、`FLEET_PG_TEST_USERNAME: postgres`、`FLEET_PG_TEST_PASSWORD: unused`）
- Modify: 测试`PostgresqlSchemaLiveTest`（`script("db/postgresql/...")`改为在fixture的独立schema上执行Flyway迁移）、`AuthQueryFieldWhitelistTest`、`AuthOAuthTenantHttpIntegrationTest`、`AuthRoleResourceTenantHttpIntegrationTest`
- Modify: 文档（`AGENTS.md`、`README.md`、`docs/ai-coding/`下引用旧规则或旧列名的文件）按「文档规则文本」

**Interfaces:**
- Consumes：任务3–5的utils 1.6.9（`TenantEntityBase`、自动填充、原生数据源块）；任务2台账的删除清单；任务8已发布的Nacos新键。

- [ ] **Step 1: 写失败测试**（`PostgresqlSchemaLiveTest`）
  - `migratesEmptySchemaToLatestVersion`：Flyway在独立schema上迁移后，`flyway_schema_history`的成功版本为`1,2,3,4`；13张业务表都有`create_time`、`update_time`、`creator`、`updater`、`is_deleted`，都没有`create_date_time`、`modify_date_time`、`create_name`、`modify_name`、`is_delete`、`label`；租户、角色、部门、用户、资源五表有`code`与`sorting`；没有`ddl_history`，有`undo_log`。
  - 现有三个用例改为迁移后执行，断言中的`is_delete`改为`is_deleted`；种子用例断言迁移后默认租户、管理员角色与资源仍在，重复执行V1的种子语句不改变已有行。
- [ ] **Step 2:** 带私有env运行`./gradlew test --tests 'com.kellen.database.PostgresqlSchemaLiveTest'`。Expected：失败（迁移文件不存在）。
- [ ] **Step 3:** 写迁移文件。
- [ ] **Step 4:** 重跑Step 2。Expected：通过。
- [ ] **Step 5: 副本等价**：`clone github_dev_user fleet_pg_test_phase3_clone_user`，`flyway-runner` baseline模式迁到最新；另建空库fresh模式迁到最新；两库`schema`比对。Expected：除`flyway_schema_history`的行外无差异。比完删除这两个库。
- [ ] **Step 6:** 改实体、查询、Mapper、VO、配置，删旧文件。
- [ ] **Step 7: 检索关口** `rg -n 'createDateTime|modifyDateTime|createName|modifyName|\bisDelete\b|getIsDelete|setIsDelete|create_date_time|modify_date_time|\bcreate_name\b|\bmodify_name\b|\bis_delete\b|ddl_history|PostgresqlDdl|\bIDdl\b|common-infra-schema|db/postgresql' user --glob '!**/build/**' --glob '!**/archive/**' --glob '!docs/superpowers/**'`。Expected：无输出。
- [ ] **Step 8:** `./gradlew test`（带与不带私有env）加仓库密钥扫描。Expected：全过。
- [ ] **Step 9:** 提交`refactor!: 建表改用Flyway，审计字段与列按新基类改名（版本1.4.2 → 1.4.3）`，交付并读回CI（CI里真库迁移测试应实际执行，不是跳过）。

### Task 10: ai-memory迁移到Flyway与新基类

**Files:**
- Create: `ai-memory/src/main/resources/db/migration/V1__baseline.sql`（`ai-memory-schema.sql`加`undo_log`，去schema前缀）、`V2__drop_ddl_history.sql`、`V3__rename_audit_columns.sql`、`V4__drop_unused_columns.sql`
- Delete: `ai-memory/src/main/java/com/kellen/bean/PostgresqlDdl.java`、`db/postgresql/`、`db/ai-memory-init.sql`
- Modify: 7个实体改继承`TenantEntityBase`，各自声明`@TableId(type = IdType.ASSIGN_ID) String id`与`@Version Integer version`（以及台账「有数据保留」的列）；`MemoryShortMessageMapper`、`MemoryShortSummaryMapper`、`MemoryVectorTaskMapper`里的旧列名
- Modify: `application.yml`、`build.gradle`（`utilsVersion = '1.6.9'`、`version = '1.2.0'`）、`.github/workflows/ci.yml`（同任务9）、`PostgresqlSchemaLiveTest`、文档

- [ ] **Step 1–9:** 同任务9的步骤；`migratesEmptySchemaToLatestVersion`断言7张表的新旧列同任务9，且`code`、`description`、`label`、`sorting`按台账清单删除；提交`refactor!: 建表改用Flyway，审计字段与列按新基类改名（版本1.1.9 → 1.2.0）`。

### Task 11: message迁移到Flyway与新基类

**Files:**
- Create: `message/src/main/resources/db/migration/V1__baseline.sql`（`message-schema.sql`加`undo_log`）、`V2__drop_ddl_history.sql`、`V3__rename_audit_columns.sql`、`V4__drop_unused_columns.sql`
- Delete: `message/src/main/java/com/kellen/bean/PostgresqlDdl.java`、`db/postgresql/`、`db/message-schema.sql`
- Modify: `UserMessage`改继承`TenantEntityBase`，声明`id`与`version`；`application.yml`、`build.gradle`（`version = '1.2.8'`）、`ci.yml`、`PostgresqlSchemaLiveTest`、文档

- [ ] **Step 1–9:** 同任务9；提交`refactor!: 建表改用Flyway，审计字段与列按新基类改名（版本1.2.7 → 1.2.8）`。

### Task 12: ai迁移到Flyway

**Files:**
- Create: `ai/src/main/resources/db/migration/V1__baseline.sql`（`ai-schema.sql`加`undo_log`；存储三表只取一份定义）、`V2__drop_ddl_history.sql`、`V3__rename_audit_columns.sql`、`V4__drop_unused_columns.sql`、`V5__drop_unused_tables_and_unify_audit_time.sql`（台账确认为空的8张表；存储三表`create_time`、`update_time`改`timestamp`：`ALTER COLUMN … TYPE timestamp USING … AT TIME ZONE 'Asia/Shanghai'`，默认值`CURRENT_TIMESTAMP`）
- Delete: `ai/src/main/java/com/kellen/config/PostgresqlDdl.java`、`db/postgresql/`、`db/ai-init.sql`、`db/ai-file-storage-platform.sql`
- Modify: `ai/src/main/java/com/kellen/repository/AiGovernanceEntity.java`（`modifyDateTime`改`updateTime`，`@TableField(fill = FieldFill.INSERT_UPDATE)`与`@JsonIgnore`保留）；`StorageConfigurationRepository`、`StorageOperationRepository`、`StorageSettingsRepository`里的旧列名
- Modify: 测试资源`ai/src/test/resources/schema.sql`、`storage-schema.sql`的旧列名；`AiApplicationTests`、`ChatAuditTenantPersistenceTest`、`StorageConfigurationHttpTest`、`StorageConfigurationServiceTest`、`StorageRepositoryTest`、`PostgresqlSchemaLiveTest`
- Modify: `application.yml`、`build.gradle`（`version = '1.7.9'`）、`ci.yml`、文档

- [ ] **Step 1: 写失败测试**（`PostgresqlSchemaLiveTest`）
  - `migratesEmptySchemaToLatestVersion`：成功版本`1–5`；10张保留表的新旧列同任务9；8张未引用表不存在；存储三表的`create_time`、`update_time`为`timestamp without time zone`，`started_at`、`finished_at`、`credential_updated_at`仍为`timestamp with time zone`。
  - `storageAuditTimesKeepShanghaiWallClock`：Flyway先迁到V4，以`'2026-11-15 10:00:00+08'`写入一条存储配置的`create_time`，再迁到最新，读出`create_time`为`2026-11-15 10:00:00`。
  - `chatMemoryDeleteStaysPhysical`：迁移后经`AiChatMemoryRepository.deleteMemoryIds`删除一条记忆，库里该行不存在（不是`is_deleted = true`）。
- [ ] **Step 2–9:** 同任务9的Step 2–9（检索关口命令把`user`换成`ai`）；提交`refactor!: 建表改用Flyway，审计列改名并删除未使用的表（版本1.7.8 → 1.7.9）`。

### Task 13: mcp、rag、report迁移到Flyway；ai-agent文档

**Files:**
- Create: `{mcp,rag,report}/src/main/resources/db/migration/V1__baseline.sql`（只有`undo_log`）、`V2__drop_ddl_history.sql`
- Delete: 三仓的`PostgresqlDdl.java`与`db/`下的脚本（rag的`rag-knowledge-graph-schema.sql`、`retired-rag-graph-cleanup.sql`）
- Create: 三仓`src/test/java/com/kellen/database/FlywayMigrationLiveTest.java`
- Modify: 三仓`application.yml`、`build.gradle`（mcp `1.2.9`、rag `1.3.3`、report `1.2.8`）、`ci.yml`、文档；`ai-agent`的`AGENTS.md`等提到`common-infra-schema.sql`的文档按「文档规则文本」

- [ ] **Step 1: 写失败测试** `FlywayMigrationLiveTest.migratesToUndoLogOnly`：在fixture独立schema上迁移后成功版本为`1,2`，只有`undo_log`与`flyway_schema_history`两张表。
- [ ] **Step 2:** 三仓带私有env各跑该测试。Expected：失败（迁移不存在）。
- [ ] **Step 3:** 写迁移、删旧文件、改配置与文档。
- [ ] **Step 4:** 三仓各做副本等价（真实库只有`undo_log`与`ddl_history`，baseline后V2删`ddl_history`）。Expected：与空库迁移结果一致。
- [ ] **Step 5:** 三仓`./gradlew test`（带与不带私有env）加密钥扫描，检索关口同任务9。Expected：全过、无输出。
- [ ] **Step 6:** 三仓各提交`refactor!: 建表改用Flyway（版本…）`；ai-agent提交`docs: 建表规则改为Flyway`；分别交付并读回CI。

### Task 14: 运维工具改读迁移目录

**Files（utils/ops/github-dev）:**
- Modify: `scripts/prepare_databases.py`：删除生成建表计划的命令行功能，只保留`migration_inventory(root) -> dict[str, set[str]]`（读各服务`db/migration`里的`CREATE TABLE`减去`DROP TABLE`）
- Modify: `scripts/verify_configuration.py`：表核对改用`migration_inventory`；`tests/test_configuration.py`、`tests/test_postgresql_configuration.py`相应改；`delivery-manifest.json`与`README.md`同步（新库保持空库、表由Flyway建）

- [ ] **Step 1: 写失败测试** `test_postgresql_configuration.MigrationInventoryTests.dropped_tables_are_not_counted`：夹具里一个服务`V1`建表A、B，`V5`删B，断言清单只有A；`create_table_without_migration_is_rejected`：服务主代码`@TableName`引用不存在于清单的表时`check`失败。
- [ ] **Step 2:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`。Expected：新用例失败。
- [ ] **Step 3:** 实现并改README。
- [ ] **Step 4:** 重跑Step 2，再`cd utils/ops/github-dev/scripts && python3 verify_configuration.py`。Expected：全过，`PASS: 9 服务 / 27 profile / 21 dataId …`。
- [ ] **Step 5:** 提交`chore(ops): 表结构核对改读Flyway迁移目录`，交付并读回CI。

### Task 15: 整体运行验证

前提：任务9–13全部交付；按Global Constraints确认端口空闲。

- [ ] **Step 1:** `lsof`核对7100、7200、7300、7301、7310、7400、7500、7600、7700。Expected：空闲；被其他项目占用就停下请用户处理。
- [ ] **Step 2:** NAS上对7个业务库各做`pg_dump -Fc`备份到容器数据卷内的`phase3-backup/`目录（0700），台账记文件名与大小。
- [ ] **Step 3:** 按user、gateway、message、report、ai-memory、rag、mcp、ai-agent、ai的顺序用`kellen-workspace/scripts/run-observed.py`启动。Expected：7个有库服务日志里Flyway显示baseline版本1并应用到最新版本；没有`NoSuchBeanDefinitionException`、`ClassNotFoundException`、`NoClassDefFoundError`；7个服务健康UP，gateway与ai-agent主端口在监听。
- [ ] **Step 4:** 在台账目录按第2阶段最终版重建`chain-verify.py`（登录、400/404错误报文与traceId、7个管理列表、message→user、资源、RAG检索、AI对话、存储探针、约2 MiB上传与清理），并新增：经网关新建一个部门，详情里`createTime`、`updateTime`非空、创建人等于登录用户ID；删除后列表不再返回，库里该行`is_deleted`为true；最后从库里清掉该行。登录口令按第2阶段的方式只在子进程内使用。Expected：最后一行`phase 3 chain verification OK`。
- [ ] **Step 5:** 只读核对7个库：`flyway_schema_history`最高版本与各服务迁移一致；`information_schema.columns`里没有旧列名；`ddl_history`不存在。Expected：全部符合。
- [ ] **Step 6:** 台账记录证据（各提交、CI、制品读回、启动、接口与库核对结果）。

### Task 16: Nacos删除旧数据源键

**Files（utils/ops/github-dev）:**
- Modify: 7份`{service}-spring.yaml`删除`postgresql.*`、`postgresql-gray.*`及其注释；`nacos/README.md`的退役键表加上这两组

- [ ] **Step 1:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`。Expected：全过。
- [ ] **Step 2:** 盘点备份、还原原文目录、保值渲染（rag加`--drop rag-spring.yaml:postgresql`与`--drop rag-spring.yaml:postgresql-gray`）、逐键比对。Expected：差异恰好是删除的两组键。
- [ ] **Step 3:** CAS发布、同步描述、只读复核。Expected：21项一致。
- [ ] **Step 4:** 依次重启7个有库服务，重跑`chain-verify.py`。Expected：`phase 3 chain verification OK`。
- [ ] **Step 5:** 提交`chore(ops): Nacos删除旧数据源键`，交付并读回CI。

### Task 17: 收尾

- [ ] **Step 1:** 逐节核对规格都有对应证据；台账汇总全部`Ruling:`与推迟的小问题。
- [ ] **Step 2:** 按执行方式做全分支最终复查。
- [ ] **Step 3:** 删除NAS上的`fleet_pg_test_phase3`库与角色、私有env；`phase3-backup/`保留到用户确认不再需要回退；清理各仓本计划的短分支；用户要求时归档到Wiki。
