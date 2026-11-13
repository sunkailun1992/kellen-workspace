# 第1阶段底座升级设计

## 已确认目标

用户2026-11-13定：个人GitHub微服务全面升级，按6个阶段推进（底座升级、utils改造、Flyway、RocketMQ、Keycloak、前端），本文只覆盖第1阶段。要求兼容性优先，特别注意Nacos与各组件之间的版本依赖；分步升级，每一步验证通过再进入下一步。

NAS中间件除RabbitMQ（第4阶段由RocketMQ取代后删除）外全部保留并升级；EMQX（物理设备）和Gotenberg（PDF）继续使用；Nacos保留，存储继续用MySQL并从8.0升到8.4。

为什么现在做：Spring Boot 4.0的免费支持到2026-12-31结束；MySQL 8.0已停止维护；后面5个阶段都建立在这一轮的版本基线上。

## 方案比较

- 采用：分三步，先升NAS服务端，再升Java客户端，最后整体验证。每一步单独可回退，出问题能定位到具体组件。服务端放前面，是因为Nacos、Seata等服务端兼容旧版客户端：服务端先升，旧客户端照常连接；客户端再统一升到新版。
- 未采用：一次全部升级。时间最短，但同时改动几十个版本，失败时很难判断是哪一项引起的。
- 未采用：升到Spring Boot 4.1。Spring Cloud Alibaba最新的2025.1.0.0只支持Boot 4.0与Spring Cloud 2025.1；配Boot 4.1的Spring Cloud 2026.0仍是RC2。等Spring Cloud Alibaba发布对应版本后另做一轮。
- 未采用：JDK 25。Dubbo已发布版本的官方兼容表最高到JDK 21；Seata 2.6才开始测25。等Dubbo明确支持后再升。

## 版本目标

核对时间2026-11-13，依据为各项目GitHub Releases、Maven Central元数据和Docker Hub标签。

### Java（12个仓库与kellen-workspace）

| 组件 | 现版本 | 目标 | 说明 |
|---|---|---|---|
| JDK | 17 | 21 | 编译工具链、CI、IDEA、Dockerfile一起改 |
| Gradle | 8.14.3 | 9.9.1 | utils、ai在8.14.3上预检，配置阶段没有弃用告警 |
| Spring Boot | 4.0.4 | 4.0.9 | 4.0系列最新补丁 |
| Spring Cloud | 2025.1.1 | 2025.1.4 | |
| Spring Cloud Alibaba | 2025.1.0.0 | 不变 | 已是最新 |
| Nacos客户端 | 3.2.2 | 3.3.0 | 与服务端同版本 |
| Dubbo | 3.3.6 | 3.3.7 | |
| Seata客户端 | 2.6.0 | 不变 | 最新稳定版 |
| MyBatis-Plus | 3.5.16 | 3.5.18.1 | Boot 4专用starter |
| dynamic-datasource | 4.5.0 | 4.6.0 | |
| Spring AI | 2.0.0 | 2.0.2 | |
| Redisson | 4.6.1 | 4.8.0 | |
| springdoc | 3.0.3 | 3.1.1 | 3.2.0基于Boot 4.1，不用 |
| Lombok | 1.18.46 | 1.18.48 | 跟随Boot 4.0.9管理版本 |
| hutool、okhttp、guava、pdfbox、aviator | 5.8.46、5.0.0、33.6.0、3.0.5、5.4.3 | 5.8.48、5.5.1、33.8.0、3.0.8、5.4.4 | 补丁与小版本 |
| 阿里云OSS SDK（ai） | 3.17.4 | 3.18.5 | 与utils一致 |

以下要改代码的升级不在本阶段：MinIO SDK 9、iText 9、POI 6、jjwt 0.13，以及删除orika、dozer、javax.mail。前四个与清理旧库放到第2阶段utils改造；jjwt由第5阶段Keycloak取代。

### NAS（orb_env的compose）

| 组件 | 现版本 | 目标 | 说明 |
|---|---|---|---|
| MySQL | 8.0 | 8.4.13 | 启动参数`--default-authentication-plugin`在8.4已删除，原样启动会失败，改为`--mysql-native-password=ON`，现有账号登录方式不变。8.4不能原地降回8.0，回退只能用停机整目录备份 |
| Nacos | v3.2.2 | v3.3.0 | 先执行官方3.3.0的升级SQL：新增`ai_resource_search_chunk`、`ai_resource_search_document`、`ai_resource_task`三张表，放宽`permissions.resource`列。3.3.0镜像默认开启客户端鉴权，本阶段显式关闭，保持现状 |
| PostgreSQL | 18.6 | 18.7 | 小版本，数据目录格式不变 |
| Conductor | 3.33.0 | 3.33.1 | 修复数据库短暂故障后PostgreSQL队列停止调度的问题 |
| Qdrant | 1.18.1 | 1.19.2 → 1.20.0 | 逐个小版本升级，升级前先快照 |
| Neo4j | 5.26.32 | 5.26.33 | 5.26长期支持线补丁 |
| Redis Stack | latest | 固定7.4.0-v8 | 先核对与当前运行的7.4.7一致 |
| Grafana | 13.3.0 | 13.3.1 | |
| OTel Collector | 0.164.0 | 0.165.0 | |
| Gotenberg | 8-chromium | 固定8.40.0 | 9.x是大版本，另行评估 |
| EMQX | 5.9.3 | 不变 | 6.x删除了旧版数据桥接接口、改了会话配置，先确认设备侧没用到再单独升 |
| Seata服务端 | 2.7.0 | 不变 | 已验收；GitHub上2.7.0仍是预发布版，客户端用2.6.0稳定版，2.7.0正式发布后再对齐 |

已是最新：Elasticsearch/Kibana 9.5.6、Jaeger 2.23.0、Prometheus 3.16.0。本阶段不动：LiteLLM（按访问规则不改）、rag-engine与rag-ocr（自有镜像）、RabbitMQ（第4阶段删除）。

## 执行步骤

### 第①步：NAS服务端

1. 备份到NAS本机的日期目录。NAS只有一块机械盘，备份只用于本次回退，不能防磁盘故障。
   - MySQL：停机后整目录复制，另做全库逻辑导出（含`nacos_config`、`seata`）。
   - PostgreSQL：全库逻辑导出。
   - Qdrant：各集合快照。
   - Neo4j、Redis：数据卷复制。
   - compose文件：复制一份原文件。
2. 按以下顺序逐个升级，每个组件升完立即检查健康状态与关键功能，失败就用备份回退，不往下走：
   1. MySQL 8.4
   2. Nacos 3.3：先执行升级SQL，再换镜像
   3. 核对Seata服务端重新注册到Nacos
   4. PostgreSQL 18.7
   5. Conductor 3.33.1
   6. Qdrant两跳
   7. Neo4j
   8. Redis固定版本
   9. Grafana与Collector
   10. Gotenberg
3. compose只改三类内容：镜像版本、MySQL启动参数、Nacos客户端鉴权开关。各内联配置正文不改，它们带SHA256校验；Conductor内联配置里的版本标签仍写3.33.0，本阶段只换镜像。
4. 停机窗口：美联kellen的开发配置和个人服务都连这台Nacos与MySQL。停机前确认双方都没有服务在运行，包括其他正在开发的会话。

### 第②步：Java客户端

1. 先升`rpc-api`和`utils`（含`fleet-observability`）并发布到Maven Local，再升9个服务和`kellen-workspace`。
2. 每个仓库：
   - 版本号按各自`VERSIONING_SPEC`顺延；
   - 编译工具链改为21，Gradle wrapper改为9.9.1；
   - CI的`java-version`改为21；
   - Dockerfile基础镜像改为Temurin 21 JRE，包名与`bootJar`固定名一致。
3. 本机：用Homebrew安装Azul Zulu 21（`brew install --cask zulu@21`），登记进记忆库装机清单；IDEA各项目SDK切到21。
4. 验证：每个仓库按自己`TESTING_SPEC`的必跑命令，在JDK 21上完整通过；短分支合入`main`；精确提交CI通过（`ai`、`gateway`、`message`、`user`推送不触发CI，需手动触发并核对`headSha`）。

### 第③步：整体验证

1. 用户在IDEA里用现有原生Application配置启动9个服务。
2. 核对：
   - Nacos服务注册与配置拉取；
   - Dubbo跨服务调用；
   - Seata AT基础事务；
   - Conductor任务；
   - RAG检索；
   - 一次真实AI调用；
   - Jaeger链路、Prometheus指标、ES运行日志逐请求读回。
3. 结果归档到LLM-Wiki。

## 并行工作

2026-11-13 15:06起另有会话在`utils`、`gateway`、`user`、`message`、`report`、`ai-memory`、`kellen-workspace`推送灰度路由改动，最近一次是16:55。

- 第②步开工前确认那项工作已经收尾，在最新`main`上做，不覆盖对方改动。
- 第①步停Nacos、MySQL前，确认对方没有服务连着这台NAS。

## 回退

- Java：各仓库独立回退提交。
- NAS：改回原镜像版本，并用第①步的备份恢复数据。MySQL只能用停机整目录备份恢复。

## 不在本阶段

- Spring Boot 4.1、Spring Cloud 2026.0
- Flyway、RocketMQ、Keycloak、utils改造、前端升级
- EMQX 6、Gotenberg 9
- 开启Nacos客户端鉴权：放到第5阶段与Keycloak一起处理
- 删除或迁移任何数据库

## 留给实施确定的细节

- 各仓库的具体新版本号（按各自版本规范）。
- `fleet-observability`使用的OpenTelemetry版本对齐。
- Gradle 9执行阶段出现的告警逐仓处理。
- 关闭Nacos 3.3客户端鉴权的确切写法（环境变量或JVM参数）：先用临时容器验证生效。
- Redis Stack镜像标签与内部Redis版本的对应关系。
- mybatis-mate是否需要跟随补丁版本。
- Conductor建库任务的保护条件里写死了PostgreSQL版本号180006。它只在全新安装时执行，升到18.7后同步改为180007，避免以后重建时误拒。
- 本仓库`AGENTS.md`和2026-11-03工作区设计写明Gradle wrapper固定8.14.3、标准wrapper不改。第②步升级wrapper时，在同一提交里把这两处改成9.9.1。
