# 第1阶段底座升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把NAS中间件和12个个人Java仓库升到2026-11-13核对的兼容最新版本，三步完成：NAS服务端、Java客户端、整体验证。

**Architecture:** 服务端先升：Nacos、MySQL等服务端兼容旧客户端，升级期间旧客户端照常可连。Java侧先升`rpc-api`、`utils`两个库并发布到Maven Local，再升9个服务。每个组件升完立即验证，失败就用本次备份回退，不往下走。

**Tech Stack:** Docker Compose（NAS的orb_env项目）、MySQL 8.4、Nacos 3.3、PostgreSQL 18、Gradle 9、JDK 21、Spring Boot 4.0、Spring Cloud Alibaba 2025.1、Dubbo 3.3。

**Spec:** `docs/superpowers/specs/2026-11-13-phase1-foundation-upgrade-design.md`

## Global Constraints

- 版本目标以规格「版本目标」两张表为准；不升Spring Boot 4.1、JDK 25、EMQX 6、Gotenberg 9。
- NAS compose只改：镜像版本、MySQL启动参数、Nacos客户端鉴权开关、Conductor建库任务里的PostgreSQL版本号；内联配置正文（带SHA256校验）不改；不碰`orb-litellm`、`orb-rabbitmq`、`orb-rag-engine`、`orb-rag-ocr`；不删任何数据库。
- 凭据只在NAS容器内部用环境变量引用，不出现在命令行输出、聊天、Git或Wiki；compose文件不整体打印，只看改动行。
- 停Nacos、MySQL、PostgreSQL前，确认没有Docker网络外的客户端连接；有就停下报告。
- Java仓库：版本号按各仓`VERSIONING_SPEC`顺延；分支按各仓`BRANCHING_SPEC`走短分支合入`main`（kellen-workspace用`codex/`前缀）；合入前本机按`TESTING_SPEC`必跑命令在JDK 21上全过，合入后精确提交CI通过；`ai`、`gateway`、`message`、`user`手动触发CI并核对`headSha`。
- Java步骤开工前确认另一会话的灰度路由改动已收尾：各仓`origin/main`30分钟内无新提交，且在最新`main`上开分支。

## Review Focus

1. Nacos 3.3镜像默认开启客户端鉴权：Seata服务端、美联与个人服务都不带账号，开启后会被拒。期望升级后不带账号的配置读取仍返回内容，Seata照常注册。→ 任务3第1步、第8步。
2. MySQL 8.4拒绝已删除的启动参数，容器会反复重启。期望容器正常运行、版本为8.4.13，Nacos与Seata重新连上。→ 任务3第5步。
3. Nacos 3.3缺新表时AI资源相关接口报错。期望三张新表存在，Nacos日志无表不存在的错误，个人`github-dev`的Agent配置可读。→ 任务3第6、7步。
4. Qdrant跨多个小版本升级可能读不了旧存储。期望经1.19.2再到1.20.0，两个集合的点数与升级前一致。→ 任务4第3步。
5. JDK 21下旧库（jjwt 0.9.1依赖JAXB、dozer）在运行期才暴露问题，单测覆盖不到。期望第③步登录签发与校验JWT、文件与报告链路都正常。→ 任务8。

---

### Task 1: 提交设计与计划

**Files:**
- Create: `kellen-workspace/docs/superpowers/specs/2026-11-13-phase1-foundation-upgrade-design.md`
- Create: `kellen-workspace/docs/superpowers/plans/2026-11-13-phase1-foundation-upgrade.md`

- [ ] **Step 1:** `git fetch`后确认`main`与`origin/main`一致、工作区只有这两份新文件。
- [ ] **Step 2:** 运行仓库自带测试（`bash -n scripts/*.sh`、`python3 -m unittest discover -s tests -v`），期望全过。
- [ ] **Step 3:** 提交`docs: 第1阶段底座升级设计与计划`并推送`main`；读回该提交的Workspace CI结论为success。

### Task 2: NAS预检与备份

**Interfaces:**
- Produces: NAS备份目录`<compose目录>/backups/phase1-20261113/`，内含`mysql-all.sql.gz`、`pg-all.sql.gz`、`qdrant-<集合>.snapshot`、`redis-data.tar.gz`、`docker-compose.yml.orig`、`SHA256SUMS`；后续任务回退都用它。

- [ ] **Step 1:** 核对`docker compose`可用、compose目录所在盘剩余空间大于20GiB。
- [ ] **Step 2:** 列出3306、5432、8848、9848端口上的已建立连接，排除Docker网段后应为空；不为空就停下报告。
- [ ] **Step 3:** 复制compose原文件为`docker-compose.yml.orig`。
- [ ] **Step 4:** MySQL全库逻辑导出（`--single-transaction --routines --events`，密码取容器内环境变量），PostgreSQL `pg_dumpall`，均gzip。
- [ ] **Step 5:** Qdrant两个集合（`github_dev_rag`、`ai_chat_files`）各建快照并下载到备份目录；记录每个集合的点数。
- [ ] **Step 6:** Redis执行`BGSAVE`后打包数据目录；记录各库键数。
- [ ] **Step 7:** 生成`SHA256SUMS`；每个文件大小非零；`gzip -t`全过。

### Task 3: MySQL 8.4与Nacos 3.3

**Interfaces:**
- Consumes: 任务2的备份目录。
- Produces: `orb-mysql`运行8.4.13、`orb-nacos`运行v3.3.0且客户端鉴权关闭；后续任务与Java客户端连接这两个服务。

- [ ] **Step 1:** 用临时容器验证关闭客户端鉴权的写法：起一个不映射宿主端口、使用内置存储的`nacos/nacos-server:v3.3.0`，分别不加和加候选设置（`NACOS_AUTH_ENABLE=false`，不生效再试`JAVA_OPT_EXT=-Dnacos.core.auth.enabled=false`），不带账号读一个不存在的配置。期望：不加时返回403，加了后返回404。记录生效的写法，然后删除临时容器。
- [ ] **Step 2:** 预先拉取`mysql:8.4.13`与`nacos/nacos-server:v3.3.0`。
- [ ] **Step 3:** 依次停止`orb-seata`、`orb-nacos`、`orb-mysql`；把`./mysql_data`整目录冷复制到备份目录。
- [ ] **Step 4:** compose改两行：`image: mysql:8.0`→`image: mysql:8.4.13`；`--default-authentication-plugin=mysql_native_password`→`--mysql-native-password=ON`。用`diff -U0`只核对这两处。
- [ ] **Step 5:** 启动`orb-mysql`。期望日志里数据字典升级完成；`SELECT VERSION()`为8.4.13；`root`账号插件仍为`mysql_native_password`。
- [ ] **Step 6:** 从Nacos 3.3.0标签取官方`mysql-schema.sql`中三张新表的建表语句和`mysql-upgrade-visibility-permission-resource.sql`，存入备份目录，在`nacos_config`执行。期望`ai_resource_search_chunk`、`ai_resource_search_document`、`ai_resource_task`存在，`permissions.resource`为`varchar(512)`。
- [ ] **Step 7:** compose改Nacos镜像为`v3.3.0`，并加上第1步验证生效的鉴权关闭设置；启动`orb-nacos`。期望日志无表不存在错误；`dev`、`github-dev`两个命名空间的配置条数与升级前一致（224、47）；不带账号读取`github-dev`的`dubbo.yaml`返回内容。
- [ ] **Step 8:** 启动`orb-seata`。期望Nacos实例列表里有`seata-server`（`SEATA_GROUP`），Seata日志无连接错误。
- [ ] **Step 9:** 任一步失败：改回原镜像和启动参数，用冷复制目录恢复`mysql_data`，再启动三项服务，停下报告。

### Task 4: 其余NAS组件

**Interfaces:**
- Consumes: 任务2的备份目录。

- [ ] **Step 1:** PostgreSQL：取`postgres:18.7`的digest，替换compose里两处`postgres@sha256:f1ea2a1a…`（`orb-postgres`与`fleet-conductor-db-init`），建库任务保护条件`180006`改为`180007`。重建`orb-postgres`。期望健康检查通过，`SHOW server_version`为18.7，`orb-conductor`与`orb-litellm`仍为healthy。
- [ ] **Step 2:** Conductor：取`v3.33.1`镜像digest，替换compose里两处`conductoross/conductor@sha256:f77a8e15…`，重建`fleet-conductor-init`与`orb-conductor`。期望init退出0，conductor healthy，`/health`正常。
- [ ] **Step 3:** Qdrant：`latest`→`v1.19.2`，验证；再→`v1.20.0`，验证。每次期望根路径版本号正确，两个集合点数与任务2记录一致。
- [ ] **Step 4:** Neo4j：停容器，冷复制数据卷，镜像改`neo4j:5.26.33-community`后启动。期望健康检查（含`apoc.version()`）通过。
- [ ] **Step 5:** Redis Stack：先拉取`redis/redis-stack:7.4.0-v8`，核对内含Redis为7.4.x；一致再改`latest`为该标签并重建。期望各库键数与任务2记录一致（带TTL的键允许自然减少）。
- [ ] **Step 6:** Grafana改`13.3.1`，Collector改`0.165.0`，重建。期望Grafana `/api/health`为ok，Prometheus里`collector`目标为up。
- [ ] **Step 7:** Gotenberg：确认`gotenberg/gotenberg:8.40.0-chromium`标签存在，替换`8-chromium`并重建。期望`/health`为up。
- [ ] **Step 8:** 记录全部容器的镜像与状态，和规格表逐项核对；把compose相对`.orig`的改动行清单（只含镜像与参数行）存入备份目录，交给用户同步本机副本。

### Task 5: rpc-api与utils

**Files（每个仓库）:**
- Modify: `build.gradle`（工具链21与版本表）、`gradle/wrapper/gradle-wrapper.properties`、`gradlew`、`gradlew.bat`、`gradle/wrapper/gradle-wrapper.jar`
- Modify: `.github/workflows/*.yml`（`java-version: "21"`）
- Modify: 文档里写死的JDK 17、Gradle 8.14.3说明（按实际出现处）

**Interfaces:**
- Produces: `com:rpc-api`、`com:utils`、`com:fleet-observability`的新版本已发布到Maven Local；任务6的服务改用这些新版本号。

- [ ] **Step 1:** 用Homebrew安装`zulu@21`，`/usr/libexec/java_home -v 21`能找到。
- [ ] **Step 2:** 确认两个仓库`origin/main`30分钟内无新提交，在最新`main`上开短分支。
- [ ] **Step 3:** `./gradlew wrapper --gradle-version 9.9.1 --distribution-type bin`，核对wrapper分发的SHA256与Gradle官方公布一致。
- [ ] **Step 4:** utils按规格表改版本：Boot 4.0.9、Spring Cloud 2025.1.4、Nacos客户端3.3.0、Dubbo 3.3.7、MyBatis-Plus 3.5.18.1、dynamic-datasource 4.6.0、Redisson 4.8.0、springdoc 3.1.1、Lombok 1.18.48、hutool 5.8.48、okhttp 5.5.1、guava 33.8.0-jre、pdfbox 3.0.8、aviator 5.4.4；`javaReleaseVersion`改为21。`fleet-observability`里单独声明的OpenTelemetry版本对齐到API 1.68.0、instrumentation 2.33.1；mybatis-mate按其发布说明决定是否跟补丁版。rpc-api只改工具链与wrapper。
- [ ] **Step 5:** 跑各仓`TESTING_SPEC`的必跑命令，期望全过；处理Gradle 9执行期告警，不靠关闭检查绕过。
- [ ] **Step 6:** 版本号按规范顺延，`publishToMavenLocal`；提交、合入`main`、推送；读回精确提交CI为success。

### Task 6: 9个服务

**Files（每个仓库）:** 同任务5，另加`Dockerfile`（若有）。

**Interfaces:**
- Consumes: 任务5发布的新版`rpc-api`、`utils`、`fleet-observability`。

- [ ] **Step 1:** 顺序：`gateway`、`user`、`message`、`report`、`mcp`、`ai-memory`、`rag`、`ai-agent`、`ai`；每个仓库开工前同样确认`origin/main`无新提交。
- [ ] **Step 2:** 改版本：服务自带的版本表（如`ai`、`ai-agent`的`versions`）按规格表对齐；`rpcApi`、`utils`改为任务5的新版本；`ai`的阿里云OSS SDK改为3.18.5；Spring AI改为2.0.2。
- [ ] **Step 3:** 工具链21、wrapper 9.9.1、CI的`java-version`为21。
- [ ] **Step 4:** Dockerfile基础镜像统一为`eclipse-temurin:21-jre`，`COPY`的包名改为该服务`bootJar`的固定包名；能本机构建镜像的跑一次`docker build`，不能的写明未验证。
- [ ] **Step 5:** 必跑命令全过；版本顺延；合入`main`、推送；精确提交CI为success。

### Task 7: kellen-workspace

**Files:**
- Modify: `gradle/wrapper/*`、`gradlew`、`gradlew.bat`
- Modify: `AGENTS.md`（「标准wrapper不改」与8.14.3的说法）、`docs/superpowers/specs/2026-11-03-workspace-design.md`（wrapper固定8.14.3）
- Modify: 工作区脚本与测试里写死的JDK或Gradle版本（若有）

- [ ] **Step 1:** wrapper升9.9.1，两处文档改为9.9.1。
- [ ] **Step 2:** 工作区测试全过（含`WORKSPACE_GRADLE_TESTS=1`的composite测试）；`./gradlew --no-daemon verifyWorkspace compileAllJava`通过。
- [ ] **Step 3:** 短分支（`codex/`前缀）合入`main`、推送；CI为success。

### Task 8: 整体验证

- [ ] **Step 1:** 用户在IDEA里把各项目SDK切到21，用原生Application配置启动9个服务。
- [ ] **Step 2:** 核对Nacos：9个服务都已注册，各自配置拉取成功。
- [ ] **Step 3:** 走一遍主要链路：登录拿到JWT后经网关访问user；一次Dubbo跨服务调用；一次Seata AT提交；一个Conductor任务；一次RAG检索；一次真实AI调用。
- [ ] **Step 4:** 每个请求在Jaeger、Prometheus、ES运行日志里都能逐个查回。
- [ ] **Step 5:** 失败的链路记下现象并修复，修复按任务5、6的流程走；修不了的写进待复核清单。

### Task 9: 归档

- [ ] **Step 1:** 记忆库装机清单登记`zulu@21`。
- [ ] **Step 2:** 按LLM-Wiki规则归档：证据页记录各版本、备份位置、验证结果与未覆盖项；更新相关仓库页、主题页与待复核清单；跑两道校验后提交推送，以提交哈希回报。
