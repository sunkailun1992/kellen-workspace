# 第4阶段RocketMQ消息 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **执行中修订（2026-11-20 13:32）：** 用户同意不用Spring Cloud Stream，收发接口改为rocketmq-spring 2.3.6（见spec「修订记录」）。任务4–7、9–11、13–15按此改写；任务1已按原计划完成。

**Goal:** NAS上线RocketMQ 5.5.0与dashboard 2.1.0并删除RabbitMQ；utils主制品加通用的RocketMQ消息模块（rocketmq-spring加收发工具类）；第一条业务“新建账号发欢迎提醒”上线并通过运行态整体验证。

**Architecture:** 先在NAS部署并实测RocketMQ，再给utils分项提交：依赖与Boot 4装配、发送工具、收发钩子、泳道隔离、上下文不向新建线程继承、真库测试、文档，然后一次发版1.7.2。rpc-api发事件契约1.1.0；Nacos加地址与两个服务的RocketMQ配置；user发事件、message消费写站内信；其余5个依赖utils的服务只升级utils。最后由用户在IDEA起9个服务做两条全链路加消息收发的整体验证。

**Tech Stack:** Gradle 9.9.1、JDK 21、Spring Boot 4.0.9（Spring Framework 7.0.10、Jackson 2.21.7）、rocketmq-spring 2.3.6（`rocketmq-spring-boot-starter`）、rocketmq-client 5.5.1、RocketMQ 5.5.0（NAS容器）、rocketmq-dashboard 2.1.0、MyBatis-Plus、Flyway、TransmittableThreadLocal、PostgreSQL 18（NAS `orb-postgres`）。

**Spec:** `docs/superpowers/specs/2026-11-20-phase4-rocketmq-design.md`

## Global Constraints

- 范围：utils、rpc-api、user、message、ai、ai-memory、rag、mcp、report与kellen-workspace；ai-agent只改文档；gateway、admin-web不改；美联项目不动。NAS只动RocketMQ与RabbitMQ的服务，不动`orb-litellm`与其他容器。
- 版本（VERSIONING_SPEC：补丁位到9时进位）：utils 1.7.1→1.7.2；rpc-api 1.0.9→1.1.0；user 1.4.4→1.4.5；message 1.2.9→1.3.0；ai 1.8.1→1.8.2；ai-memory 1.2.1→1.2.2；rag 1.3.4→1.3.5；mcp 1.2.9→1.3.0；report 1.2.8→1.2.9。推送前核对远端tag、在途分支与私有Maven没有占用，占用就顺延。
- 依赖：utils以`api`声明`org.apache.rocketmq:rocketmq-spring-boot-starter:2.3.6`；`org.apache.rocketmq`下名字以`rocketmq-`开头、且不以`rocketmq-spring`开头的模块用`resolutionStrategy`锁5.5.1（写法同现有Nacos客户端锁定）；不引入Spring Cloud Stream；Nacos客户端3.3.0的强制对齐不变。
- 镜像：`apache/rocketmq:5.5.0@sha256:7e8f6c9dbd9df742ed26ba69c00d4ad69e2f86a56f3ca7782ff8144dd0798132`、`apacherocketmq/rocketmq-dashboard:2.1.0@sha256:ce78506bd6fe01095bf1b37c954625e363ba5afae5d1539b77f395785d445e33`（任务2 Step 1读回）。
- 包：对外API在`com.kellen.utils.mq`（`MqTemplate`、`MqSendOptions`、`MqHeaders`、`MqSendException`）；装配与内部实现在`com.kellen.config.mq`（`MqAutoConfiguration`标`@AutoConfiguration(after = RocketMQAutoConfiguration.class)`并登记进`AutoConfiguration.imports`；`MqInstanceLane`、`MqLaneSelector`、`MqContextSendMessageHook`、`MqContextConsumeMessageHook`；后置处理器`MqRocketMqInitializer`以静态`@Bean`登记）。
- 消息属性（`MqHeaders`常量）：`fleet_tenant_id`、`fleet_user_id`、`fleet_dept_id`、`fleet_datasource`、`fleet_lane`、`fleet_canary_tag`；key用`RocketMQHeaders.KEYS`（多个以空格分隔），tag写进目的地`主题:tag`。
- 实例泳道：用`Binder`读`spring.cloud.nacos.discovery.metadata`映射，`traffic.lane`缺省`stable`，`canary.tag`缺省无；值必须匹配`^[A-Za-z0-9._:-]{1,64}$`，否则启动失败。
- 泳道订阅：stable为`(fleet_lane IS NULL OR fleet_lane = 'stable')`；其他泳道为`(fleet_lane = '<泳道>' AND fleet_canary_tag = '<标签>')`，没有标签时为`(fleet_lane = '<泳道>')`；容器原有TAG表达式`a || b`合并为`(TAGS IN ('a', 'b')) AND (<泳道条件>)`，原表达式为`*`时只用泳道条件，原有SQL92表达式`x`合并为`(x) AND (<泳道条件>)`。非stable实例的消费组加后缀`-<泳道>-<标签>`（没有标签时`-<泳道>`），已带后缀的不再加。消费组在容器`afterPropertiesSet`之前改；订阅在消费者创建后、启动前用`consumer.subscribe(topic, MessageSelector.bySql(<表达式>))`重设。
- 名字：主题`github-dev-user-account-created`（4个队列）；消费组`github-dev-message-user-account-created`；生产者组`github-dev-user-producer`；Nacos键`fleet.mq.topic.user-account-created`（user与message）、`fleet.mq.group.user-account-created`（message）；账号`github-dev-admin`（超级用户）、`github-dev-user`、`github-dev-message`、`github-dev-utils-test`；真库测试主题`github-dev-utils-live-test`、`github-dev-utils-live-test-orderly`，测试消费组前缀`github-dev-utils-test-`。
- 欢迎提醒：类型`SYSTEM_NOTICE`，发件人名`system`、发件人ID为空，标题`欢迎使用`，正文`你好，<称呼>，你的账号已创建。`（称呼：昵称非空用昵称，否则用户名），发送状态`SENT`，未读；`owner_user_id`、`dept_id`取还原的操作人（与`buildSendMessage`一致）。
- 消费：`@RocketMQMessageListener(topic = "${fleet.mq.topic.user-account-created}", consumerGroup = "${fleet.mq.group.user-account-created}", maxReconsumeTimes = 3)`，之后进RocketMQ死信。
- 转换：`MqRocketMqInitializer`给`RocketMQMessageConverter`里的Jackson转换器关掉`DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES`（与Spring Boot缺省一致）；时间模块沿用rocketmq-spring已注册的。
- 私有值：dev.env新增`GITHUB_ROCKETMQ_HOST`、`GITHUB_ROCKETMQ_PORT`、`GITHUB_ROCKETMQ_USER_ACCESS_KEY`、`GITHUB_ROCKETMQ_USER_SECRET_KEY`、`GITHUB_ROCKETMQ_MESSAGE_ACCESS_KEY`、`GITHUB_ROCKETMQ_MESSAGE_SECRET_KEY`；管理账号与dashboard口令只在NAS编排与`~/.config/kellen-github/phase4/`（目录0700、文件0600）；真库测试变量`FLEET_MQ_TEST_NAMESRV`、`FLEET_MQ_TEST_ACCESS_KEY`、`FLEET_MQ_TEST_SECRET_KEY`放`~/.config/kellen-github/phase4/fleet-mq-test.env`。口令随机生成，不打印、不进Git与Wiki。
- 构建一律JDK 21：`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`。
- Git：开工、提交前、推送前都`git fetch`；每个任务从最新`origin/main`建短分支`codex/phase4-<任务>`，本机验证全过后快进合入`main`并推送、删短分支；远端前进就rebase并重跑验证，不强推；读回该精确提交的CI为success再开下一个任务。ai、gateway、message、user推送不触发CI，用`gh workflow run ci.yml --ref main`手动触发并核对`headSha`。服务CI里固定的utils、rpc-api检出`ref:`在各服务任务里改为本阶段发布提交的完整SHA。
- NAS：只用`ssh -o BatchMode=yes qw-nas`与`sudo -n docker …`；编排与文件操作在临时容器里做（`busybox:1.37.0-musl`的`cp -p`；改编排文本用带python3的现有镜像，按行插入或删除，不整体重排YAML）；改编排前另存`docker-compose.yml.before-<改动>-<日期>`；compose固定`-p orb_env`，单服务用`up -d --no-deps <服务>`；输出遮掉9位以上数字串；编排路径与真实地址不进Git、Wiki或回复；代理、VPN、组网相关的配置、端口与日志一律不碰。
- 凭据：私有Maven只经`~/.config/kellen-github/observability-20261106/run-maven-release.py`；Nacos只经私有`dev.env`与`utils/ops/github-dev/scripts`；登录口令只由`run_chain_verify.py`在子进程内使用。
- 运行态：9个服务（端口7100、7200、7300、7301、7310、7400、7500、7600、7700）由用户在IDEA启停；运行配置不挂JVM参数。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-20-phase4-rocketmq/progress.md`，逐任务记完成行（提交区间、测试命令与结果）与`Ruling:`行；可复用脚本放`~/.config/kellen-github/tools`。
- 已定裁定（规格留给实施的细节）：
  - 上下文钩子、泳道改写、转换器设置都由一个后置处理器`MqRocketMqInitializer`完成（照芋道`TenantRocketMQInitializer`）。
  - `MqTemplate`按调用线程直接写上下文属性；发送钩子只补缺的属性，给业务直接用`RocketMQTemplate`同步发送兜底。
  - message消费测试用现有`FLEET_PG_TEST_URL`真库测试方式（CI用postgres:18服务容器），直接调用监听器并用真实消费钩子包住；utils的RocketMQ真库测试本机跑、CI跳过。
  - 事件时间用`Instant`；事件类按rpc-api现有DTO写法（`Serializable`、无参构造、getter/setter，不引入Jackson注解）。
  - NAS的RocketMQ数据卷由一次性准备任务`orb-rocketmq-prepare`（busybox）改属主为3000并写入内联配置：镜像里没有`/home/rocketmq/store`与`logs`，新建的命名卷归root。

## Review Focus

1. 不收发消息的服务（ai、ai-memory、rag、mcp、report）带上rocketmq-spring后创建生产者或监听容器、去连RocketMQ。期望：没配`rocketmq.*`就不创建、不连接。→ 任务4 `noRocketMqBeansWithoutNameServer`；任务17 Step 5。
2. 消费失败（触发RocketMQ重投）后，消费线程残留租户或操作人，下一条消息写错归属。期望：失败后也恢复原值。→ 任务6 `consumeHookRestoresAfterFailedConsume`。
3. 生产方以后给事件加字段，旧消费方反序列化失败、消息进死信。期望：忽略未知字段照常消费。→ 任务6 `converterIgnoresUnknownFields`；任务15 `consumesEventWithUnknownField`。
4. RocketMQ不可用或发送失败时，建号接口跟着失败或回滚。期望：账号照常建好，只缺这条提醒，日志记目的地与key。→ 任务5 `sendAfterCommitFailureIsLoggedNotThrown`。
5. 同一个容器被后置处理器处理两次，或消费组已带后缀，结果被重复加后缀、订阅被套两层条件。期望：幂等。→ 任务7 `laneSelectorIsIdempotent`。

---

### Task 1: 提交计划

**Files:**
- Create: `kellen-workspace/docs/superpowers/plans/2026-11-20-phase4-rocketmq.md`

- [ ] **Step 1:** `python3 -m unittest discover -s tests`（kellen-workspace）。Expected：OK。
- [ ] **Step 2:** 提交`docs: 第4阶段RocketMQ执行计划`，与已提交的设计一起推送并读回CI；新建台账。

### Task 2: NAS部署RocketMQ与dashboard

**Files（Git外）:**
- Modify: NAS编排`docker-compose.yml`：新增`orb-rocketmq-prepare`、`orb-rocketmq-namesrv`、`orb-rocketmq-broker`、`orb-rocketmq-dashboard`、`orb-rocketmq-init`与命名卷`rocketmq-config`、`rocketmq-namesrv-logs`、`rocketmq-broker-store`、`rocketmq-broker-logs`、`rocketmq-dashboard-data`。
- Create: `~/.config/kellen-github/phase4/`：各账号口令、`fleet-mq-test.env`。
- Create: `~/.config/kellen-github/tools/nas_mq.py`：只读子命令`topics`、`progress <消费组>`、`consumers <消费组>`，在broker容器里用管理账号的`tools.yml`执行`mqadmin`。
- Modify: 私有`dev.env`：Global Constraints列出的6个变量（主机取客户端访问NAS用的地址，与现有中间件同一来源）。

- [ ] **Step 1:** NAS上`docker pull`两个镜像，取`RepoDigests`；确认启动方式：rocketmq镜像以`nameserver`/`broker <参数>`为命令，`HEAP_OPTS`整体替换缺省堆参数，`JAVA_OPT_EXT`追加在后；dashboard以`JAVA_OPTS`传参。Expected：台账记下两个摘要与启动写法（已完成，见Global Constraints）。
- [ ] **Step 2:** 生成`github-dev-admin`、`github-dev-user`、`github-dev-message`、`github-dev-utils-test`四个账号与dashboard登录的随机口令，写入私有文件（0600）。
- [ ] **Step 3:** 另存编排备份，在`orb-litellm`之前按行插入5个服务、在`volumes:`末尾插入5个卷：
  - `orb-rocketmq-prepare`：`busybox:1.37.0-musl`、`user: 0:0`、`restart: "no"`；把3个数据卷`chown 3000:3000`；用环境变量里的口令把broker.conf、`tools.yml`（管理账号）、dashboard的`users.properties`写进`rocketmq-config`（文件0640，属主3000）。broker.conf：`brokerClusterName=DefaultCluster`、`brokerName=broker-a`、`brokerId=0`、`brokerRole=ASYNC_MASTER`、`flushDiskType=ASYNC_FLUSH`、`namesrvAddr=orb-rocketmq-namesrv:9876`、`brokerIP1=<私有>`、`listenPort=10911`、`autoCreateTopicEnable=false`、`enablePropertyFilter=true`、`fileReservedTime=48`、`traceTopicEnable=true`、`authenticationEnabled=true`、`authenticationProvider=org.apache.rocketmq.auth.authentication.provider.DefaultAuthenticationProvider`、`authenticationMetadataProvider=org.apache.rocketmq.auth.authentication.provider.LocalAuthenticationMetadataProvider`、`authorizationEnabled=true`、`authorizationProvider=org.apache.rocketmq.auth.authorization.provider.DefaultAuthorizationProvider`、`authorizationMetadataProvider=org.apache.rocketmq.auth.authorization.provider.LocalAuthorizationMetadataProvider`、`initAuthenticationUser={"username":"github-dev-admin","password":"<私有>"}`、`innerClientAuthenticationCredentials={"accessKey":"github-dev-admin","secretKey":"<私有>"}`。
  - `orb-rocketmq-namesrv`：命令`nameserver`；`HEAP_OPTS=-Xms256m -Xmx256m -Xmn128m`；`JAVA_OPT_EXT`把GC日志改到日志卷（`-Xloggc:/home/rocketmq/logs/rmq_srv_gc.log -XX:+UseGCLogFileRotation -XX:NumberOfGCLogFiles=3 -XX:GCLogFileSize=10m`）；上限512m；端口9876；依赖prepare成功退出。
  - `orb-rocketmq-broker`：命令`broker -c /home/rocketmq/config/broker.conf`；`HEAP_OPTS=-Xms512m -Xmx512m -Xmn256m -XX:MaxDirectMemorySize=256m`；`JAVA_OPT_EXT`同样把GC日志改到日志卷（`mq_gc_%p.log`）；上限1536m；端口10911；挂存储卷、日志卷与只读配置卷；依赖prepare成功退出与namesrv。
  - `orb-rocketmq-dashboard`：`JAVA_OPTS`为`-Xms256m -Xmx256m`与`-Drocketmq.config.namesrvAddrs=orb-rocketmq-namesrv:9876 -Drocketmq.config.isVIPChannel=false -Drocketmq.config.loginRequired=true -Drocketmq.config.dataPath=/data -Drocketmq.config.accessKey=github-dev-admin -Drocketmq.config.secretKey=<私有>`；`users.properties`放在`/data`；上限768m；宿主端口`18082:8082`（选前再核对空闲）。
  - `orb-rocketmq-init`：rocketmq镜像，`restart: "no"`，上限256m，挂只读配置卷取`tools.yml`；执行Step 5的命令。
  - 重启策略：常驻服务`unless-stopped`。`docker compose -p orb_env -f <文件> config -q`。Expected：校验通过。
- [ ] **Step 4:** `up -d --no-deps orb-rocketmq-prepare`，等它退出0；再`up -d --no-deps orb-rocketmq-namesrv orb-rocketmq-broker orb-rocketmq-dashboard`。Expected：日志有`The Name Server boot success`与broker的`boot success`；dashboard登录页HTTP 200。
- [ ] **Step 5:** init（`up --no-deps orb-rocketmq-init`）幂等执行`mqadmin`：`updateTopic -c DefaultCluster`建3个主题（4读4写队列）；`createUser -c DefaultCluster -u <账号> -p <口令> -t Normal`建3个普通账号；`createAcl -c DefaultCluster -s User:<账号> -r <资源> -a <动作> -d Allow`：user对`Topic:github-dev-user-account-created`为`Pub`；message对该主题与`Group:github-dev-message-*`为`Sub`；测试账号对两个测试主题为`Pub,Sub`、对`Group:github-dev-utils-test-*`为`Sub`；三个账号对`Topic:RMQ_SYS_TRACE_TOPIC`为`Pub`。Expected：退出0；再跑一次仍退出0。init能经namesrv连到broker，说明容器里经`brokerIP1`可达；连不上就按规格改用宿主网络或调整地址，台账记`Ruling:`。
- [ ] **Step 6:** `nas_mq.py topics`。Expected：列出3个主题与轨迹主题。用管理账号登录dashboard。Expected：能看到集群与主题。
- [ ] **Step 7:** 只读复核内存：`free -m`，以及3个新容器的`docker stats`。Expected：可用内存不低于3.5GB，交换区已用量比上线前（3082MB）增加不超过500MB；不满足就停下报告用户。台账记录端口、卷与内存。

### Task 3: 删除RabbitMQ

**Files（Git外）:**
- Create: NAS编排目录`backups/rabbitmq-retire-20261120/`（0700）：数据目录打包、`rabbitmqctl export_definitions`导出的definitions、编排原文、`SHA256SUMS`。
- Modify: NAS编排（删`orb-rabbitmq`服务段与它上方的`# 4.`注释行）；私有`dev.env`（删4个`GITHUB_RABBITMQ_*`变量）。

- [ ] **Step 1:** 只读确认仍然没有连接、队列消息数为0。Expected：同设计里的现场。
- [ ] **Step 2:** 备份三样并写`SHA256SUMS`，在busybox里`sha256sum -c`读回。Expected：全部OK。
- [ ] **Step 3:** 另存编排备份；停止并删除`orb-rabbitmq`容器；删编排服务段；`config -q`。Expected：校验通过；其他容器的ID与启动时间不变。
- [ ] **Step 4:** 删除数据目录与`rabbitmq:3.11-management`镜像；删私有变量。Expected：`docker ps -a`没有`orb-rabbitmq`；备份目录完整。

### Task 4: utils引入rocketmq-spring并在Boot 4上装配

**Files:**
- Modify: `utils/build.gradle`（`rocketmqSpringVersion = '2.3.6'`、`rocketmqClientVersion = '5.5.1'`；`api "org.apache.rocketmq:rocketmq-spring-boot-starter:${rocketmqSpringVersion}"`；`resolutionStrategy`按Global Constraints锁客户端模块）
- Test: `utils/src/test/java/com/kellen/config/mq/RocketMqAutoConfigurationTest.java`

**Interfaces:**
- Produces：依赖utils的服务都带上rocketmq-spring与rocketmq-client 5.5.1；没配`rocketmq.name-server`时不创建`DefaultMQProducer`、`RocketMQTemplate`与监听容器。

- [ ] **Step 1: 写失败测试**（`ApplicationContextRunner`，自动配置`RocketMQAutoConfiguration`）
  - `noRocketMqBeansWithoutNameServer`：不设`rocketmq.*`时没有`DefaultMQProducer`、`RocketMQTemplate`。
  - `templateCreatedWhenConfigured`：设`rocketmq.name-server=127.0.0.1:9876`、`rocketmq.producer.group=github-dev-utils-test-producer`时，Boot 4.0.9上下文启动成功并有`RocketMQTemplate`。
  - `clientVersionIs551`：`MQVersion.getVersionDesc(MQVersion.CURRENT_VERSION)`等于`V5_5_1`。
- [ ] **Step 2:** `./gradlew test --tests 'com.kellen.config.mq.RocketMqAutoConfigurationTest'`。Expected：编译失败（还没有依赖）。
- [ ] **Step 3:** 改`build.gradle`。
- [ ] **Step 4:** `./gradlew test javadoc`；`dependencyInsight --configuration runtimeClasspath`分别查`rocketmq-spring-boot`、`rocketmq-client`、`nacos-client`、`netty-common`、`jackson-databind`、`com.alibaba:fastjson`。Expected：全过；依次为2.3.6、5.5.1、3.3.0；`netty-common`只有一个版本且与改动前相同（改动前先记下）；`jackson-databind`为2.21.7；rocketmq没有带进`com.alibaba:fastjson`。
- [ ] **Step 5:** 提交`feat(mq): 引入rocketmq-spring 2.3.6并锁定RocketMQ客户端5.5.1`，交付并读回CI。

### Task 5: utils发送工具

**Files:**
- Create: `utils/src/main/java/com/kellen/utils/mq/MqTemplate.java`、`MqSendOptions.java`、`MqHeaders.java`、`MqSendException.java`
- Create: `utils/src/main/java/com/kellen/config/mq/MqInstanceLane.java`、`MqAutoConfiguration.java`（`MqInstanceLane`为Bean；`MqTemplate`标`@ConditionalOnBean(RocketMQTemplate.class)`），登记进`AutoConfiguration.imports`
- Test: `utils/src/test/java/com/kellen/utils/mq/MqTemplateTest.java`（Mockito模拟`RocketMQTemplate`；事务用测试里一个不连库的`AbstractPlatformTransactionManager`子类）

**Interfaces:**
- Produces：`MqTemplate(RocketMQTemplate rocketMQTemplate, MqInstanceLane lane)`；`void send(String topic, Object payload)`；`void send(String topic, Object payload, MqSendOptions options)`（失败或`SendStatus`不是`SEND_OK`时抛`MqSendException`）；`void sendAfterCommit(String topic, Object payload)`；`void sendAfterCommit(String topic, Object payload, MqSendOptions options)`（调用时抓取上下文快照；有事务时提交后发、回滚不发；没有事务时立即发；提交后失败只记`error`日志，内容为目的地、key与`fleet_tenant_id`，不含正文）。
- Produces：`record MqSendOptions(String tag, List<String> keys, Duration delay, String orderKey)`，`static MqSendOptions none()`，`withTag(String)`、`withKeys(String...)`、`withDelay(Duration)`、`withOrderKey(String)`；`delay`与`orderKey`同时设置、`delay`小于1秒或大于3天时抛`IllegalArgumentException`。
- Produces：调用映射——有`orderKey`用`syncSendOrderly(目的地, 消息, orderKey)`；有`delay`用`syncSendDelayTimeSeconds(目的地, 消息, 秒数)`；否则`syncSend(目的地, 消息)`；目的地为`主题`或`主题:tag`。
- Produces：`record MqInstanceLane(String lane, String canaryTag)`，`static MqInstanceLane resolve(Environment environment)`（读法与校验见Global Constraints）。

- [ ] **Step 1: 写失败测试**
  - `sendWritesContextHeaders`：租户`t1`、操作人`u1`/部门`d1`、数据源`gray`、实例`canary`/`kellen-local`时，传给`RocketMQTemplate`的消息头依次为`fleet_tenant_id=t1`、`fleet_user_id=u1`、`fleet_dept_id=d1`、`fleet_datasource=gray`、`fleet_lane=canary`、`fleet_canary_tag=kellen-local`。
  - `sendWithoutContextOmitsEmptyHeaders`：没有上下文、实例为stable时，没有租户、操作人、数据源、标签头，`fleet_lane=stable`。
  - `optionsMapToRocketMqCalls`：`withTag("a").withKeys("k1","k2")`调用`syncSend("t:a", …)`且`KEYS=k1 k2`；`withDelay(Duration.ofSeconds(5))`调用`syncSendDelayTimeSeconds("t", …, 5)`；`withOrderKey("o1")`调用`syncSendOrderly("t", …, "o1")`。
  - `delayAndOrderKeyTogetherAreRejected`、`delayOutOfRangeIsRejected`（0秒、3天加1秒）。
  - `sendAfterCommitSendsOnlyAfterCommit`：事务里调用并在提交前清掉上下文；提交前没有发送，提交后发送一次，头里是调用时的上下文。
  - `sendAfterCommitDropsOnRollback`、`sendAfterCommitWithoutTransactionSendsNow`。
  - `sendAfterCommitFailureIsLoggedNotThrown`：提交后`syncSend`抛`MessagingException`时调用方不收到异常；日志含目的地与key，不含负载里的字符串。
  - `sendFailureThrowsMqSendException`：`syncSend`抛`MessagingException`或返回非`SEND_OK`时`send`抛`MqSendException`。
  - `instanceLaneDefaultsToStable`、`invalidLaneIsRejected`（`MqInstanceLane.resolve`）。
- [ ] **Step 2:** 运行该测试类。Expected：编译失败或全部失败。
- [ ] **Step 3:** 实现：提交后发送用`TransactionSynchronizationManager.registerSynchronization`；消息用`MessageBuilder`组装。
- [ ] **Step 4:** `./gradlew test javadoc`。Expected：全过。
- [ ] **Step 5:** 提交`feat(mq): 新增MqTemplate发送工具（上下文属性、tag与key、延时、顺序、事务提交后发送）`，交付并读回CI。

### Task 6: utils收发钩子与转换器

**Files:**
- Create: `utils/src/main/java/com/kellen/config/mq/MqContextSendMessageHook.java`、`MqContextConsumeMessageHook.java`、`MqRocketMqInitializer.java`（在`MqAutoConfiguration`里以静态`@Bean`登记）
- Test: `utils/src/test/java/com/kellen/config/mq/MqContextHooksTest.java`、`MqRocketMqInitializerTest.java`

**Interfaces:**
- Consumes：任务5的`MqHeaders`、`MqInstanceLane`。
- Produces：`MqRocketMqInitializer.postProcessAfterInitialization`：`RocketMQTemplate`→其生产者`getDefaultMQProducerImpl().registerSendMessageHook(...)`；`DefaultRocketMQListenerContainer`→其消费者`getDefaultMQPushConsumerImpl().registerConsumeMessageHook(...)`；`RocketMQMessageConverter`→其中Jackson转换器的`ObjectMapper`关掉`FAIL_ON_UNKNOWN_PROPERTIES`。消费钩子执行期间`TenantContextHolder.getTenantId()`、`UserContextHolder.get()`（`SecurityUser`只填`userId`、`tenantId`、`deptId`，`authorities`与`dataScopeDeptIds`为空列表、`dataScope`为null）、`DynamicSourceTtl.get()`等于消息属性，处理后恢复为处理前的值。

- [ ] **Step 1: 写失败测试**
  - `sendHookFillsMissingContextProperties`：消息没有上下文属性、线程上有上下文时补上。
  - `sendHookKeepsExistingProperties`：消息已有`fleet_tenant_id=x`时不覆盖。
  - `consumeHookRestoresContextAndRestoresPrevious`：处理前按属性设置三类上下文，处理后恢复原值。
  - `consumeHookRestoresAfterFailedConsume`：消费上下文标为失败时同样恢复。
  - `consumeHookRejectsBatch`：一次两条消息时抛`IllegalStateException`。
  - `restoredUserCarriesNoAuthorities`。
  - `initializerRegistersHooksOnTemplateAndContainer`：用未启动的`DefaultMQProducer`建`RocketMQTemplate`，用测试主题、消费组与监听器建容器并调用`afterPropertiesSet()`（不连网络）；经后置处理器后，生产者`hasSendMessageHook()`、消费者`hasHook()`为true。
  - `converterIgnoresUnknownFields`：经后置处理器的`RocketMQMessageConverter`能把`{"eventId":"e","foo":1}`转成只有`eventId`字段的测试类。
- [ ] **Step 2–4:** 同任务5的Step 2–4。
- [ ] **Step 5:** 提交`feat(mq): 收发钩子传递租户、操作人与数据源，消费端忽略未知字段`，交付并读回CI。

### Task 7: utils泳道隔离

**Files:**
- Create: `utils/src/main/java/com/kellen/config/mq/MqLaneSelector.java`
- Modify: `MqRocketMqInitializer.java`（`postProcessBeforeInitialization`给容器改消费组；`postProcessAfterInitialization`重设订阅）
- Test: `utils/src/test/java/com/kellen/config/mq/MqLaneSelectorTest.java`、`MqRocketMqInitializerTest.java`

**Interfaces:**
- Consumes：任务5的`MqInstanceLane`；任务6的`MqRocketMqInitializer`。
- Produces：`MqLaneSelector(MqInstanceLane lane)`；`String consumerGroup(String original)`；`String sqlExpression(SelectorType originalType, String originalExpression)`（规则见Global Constraints）。

- [ ] **Step 1: 写失败测试**
  - `stableKeepsGroupAndFiltersStableLane`、`canarySuffixesGroupAndFiltersOwnTag`（`g-canary-kellen-local`与`(fleet_lane = 'canary' AND fleet_canary_tag = 'kellen-local')`）、`canaryWithoutTagFiltersLaneOnly`。
  - `tagExpressionIsMergedIntoSql`（`a || b`）、`starTagUsesLaneConditionOnly`、`sqlExpressionIsMergedIntoSql`。
  - `laneSelectorIsIdempotent`：已带后缀的消费组不再加。
  - `initializerRewritesContainerGroupAndSubscription`：canary实例下，经后置处理器的容器`getConsumerGroup()`带后缀，消费者对该主题的订阅为SQL92且表达式符合规则。
- [ ] **Step 2–4:** 同任务5的Step 2–4。
- [ ] **Step 5:** 提交`feat(mq): 消费组与订阅按实例泳道隔离`，交付并读回CI。

### Task 8: utils上下文不向新建线程继承

**Files:**
- Modify: `utils/src/main/java/com/kellen/security/UserContextHolder.java`、`utils/src/main/java/com/kellen/utils/context/TenantContextHolder.java`（`TransmittableThreadLocal`覆写`childValue`返回null；`copy`保持默认，`TtlRunnable`、`TtlExecutors`照常传递）
- Test: `utils/src/test/java/com/kellen/security/ContextInheritanceTest.java`

- [ ] **Step 1: 写失败测试**：`newThreadDoesNotInheritUser`、`newThreadDoesNotInheritTenant`（设置上下文后`new Thread`读到null）；`ttlRunnableStillPropagatesUser`、`ttlExecutorsStillPropagateTenant`（经`TtlRunnable.get`与`TtlExecutors.getTtlExecutorService`提交的任务读到原值）。
- [ ] **Step 2:** 运行该测试类。Expected：前两条失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 依赖继承的检查：`rg -n 'new Thread\(|CompletableFuture\.(supplyAsync|runAsync)\([^,()]*\)|Executors\.new|parallelStream\(|Schedulers\.' utils/src/main ai/src/main ai-memory/src/main user/src/main message/src/main rag/src/main mcp/src/main report/src/main`，逐处判断任务里是否读用户或租户上下文；需要的在所属服务的升级任务（任务14–16）里改为显式传递并补测试，其余在台账记“不依赖”。
- [ ] **Step 5:** `./gradlew test javadoc`。Expected：全过。
- [ ] **Step 6:** 提交`fix(security): 用户与租户上下文不再向新建线程继承`，交付并读回CI。

### Task 9: utils的RocketMQ真库测试

**Files:**
- Create: `utils/src/test/java/com/kellen/config/mq/RocketMqLiveTest.java`（`@EnabledIfEnvironmentVariable(named = "FLEET_MQ_TEST_NAMESRV", matches = ".+")`；`ApplicationContextRunner`起真实的rocketmq-spring与utils自动配置，账号`github-dev-utils-test`；每次运行的消费组名带时间戳）

- [ ] **Step 1: 写测试**
  - `sendAndReceiveWithContext`：经`MqTemplate`发到`github-dev-utils-live-test`，30秒内收到，监听器里租户等于发送时的租户。
  - `canaryMessageSkipsStableConsumer`：同时起一个stable实例的上下文与一个canary/`kellen-local`实例的上下文，各有一个监听器；canary实例发出的消息30秒内只被后者收到，前者10秒内没有收到。
  - `delayDelaysDelivery`：`withDelay(Duration.ofSeconds(5))`的消息在发送4.5秒之后才到。
  - `orderKeyKeepsOrder`：同一顺序键的20条消息发到`github-dev-utils-live-test-orderly`，`consumeMode = ConsumeMode.ORDERLY`的监听器按发送顺序收到。
  - `wrongSecretIsRejected`：错误口令发送时抛`MqSendException`。
- [ ] **Step 2:** 加载`fleet-mq-test.env`后运行。Expected：全过；不过时按原因修NAS配置（回到任务2）或代码，台账记`Ruling:`。台账另记：按前缀授权消费组时，自动创建消费组是否足够。
- [ ] **Step 3:** 不加载私有env运行。Expected：该类全部跳过。
- [ ] **Step 4:** 提交`test(mq): 新增RocketMQ真库测试（收发、泳道过滤、延时、顺序、认证）`，交付并读回CI。

### Task 10: utils文档与第3阶段遗留注释

**Files:**
- Create: `utils/docs/ai-coding/MQ_SPEC.md`：Nacos配置模板（`rocketmq.name-server`、生产者组与账号、`fleet.mq`主题与消费组）、发送（`MqTemplate`与选项，事务提交后发送；可以直接用`RocketMQTemplate`同步发送，但异步发送请经`MqTemplate`以免丢上下文）、消费（`@RocketMQMessageListener`加`RocketMQListener<T>`、只支持单条、必须幂等、不调需要灰度授权的RPC、需权限的查询自行处理）、泳道规则、命名规则、`mq/message|producer|consumer`分包、禁止事项（不直接用`DefaultMQProducer`、`DefaultMQPushConsumer`，消息里不放凭据与完整个人信息）。
- Modify: `utils/AGENTS.md`（必读加`MQ_SPEC.md`）、`utils/README.md`（消息模块一节）、`utils/docs/ai-coding/UTILS_TOOL_CATALOG.md`、`PROJECT_CODING_SPEC.md`、`TESTING_SPEC.md`（RabbitMQ改RocketMQ，写明真库测试变量）、`NACOS_CONFIG_SPEC.md`（新键）、`utils/docs/seata-live-contract.md`、`DynamicSourceTtl.java`注释里的Rabbit字样
- Modify: `utils/ops/github-dev/nacos/DEFAULT_GROUP/security-auth.yaml`第19行注释与`utils/ops/github-dev/tests/test_postgresql_configuration.py`第70行文档串（`creator`改`create_by`）；`utils/ops/github-dev/README.md`（删`/github-dev` vhost那句，补RocketMQ）；`utils/ops/github-dev/delivery-manifest.json`里的`rabbitmq.yaml`条目（核对是否已在退役清单，按现有退役条目的写法处理）

- [ ] **Step 1:** `rg -n -i 'rabbit|amqp' utils --glob '!**/build/**' --glob '!**/bin/**' --glob '!docs/superpowers/**' --glob '!**/archive/**'`。Expected：只剩`retired-dataids.json`与README历史版本说明里的记载（台账列出保留理由）。`rg -n 'creator' utils/ops`。Expected：无输出。
- [ ] **Step 2:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`。Expected：OK（含逐行注释检查）。
- [ ] **Step 3:** 提交`docs(mq): 消息模块用法与规范；RabbitMQ描述改为RocketMQ`，交付并读回CI。

### Task 11: 发布utils 1.7.2

**Files:**
- Modify: `utils/build.gradle`（`version = '1.7.2'`）、`utils/README.md`（1.7.2变更说明：RocketMQ消息模块、上下文不向新建线程继承、注释修正）

- [ ] **Step 1:** 核对远端tag、在途分支与私有Maven没有1.7.2。Expected：未占用。
- [ ] **Step 2:** `./gradlew clean test javadoc`、仓库密钥扫描、任务4 Step 4的`dependencyInsight`。Expected：全过，版本同任务4。
- [ ] **Step 3:** 提交`build: 发布utils 1.7.2`，交付并读回CI；记下完整提交SHA（任务14–16的CI检出用）。
- [ ] **Step 4:** 经`run-maven-release.py`发布`com:utils:1.7.2`，用`maven-readback.py`读回POM、JAR、sourcesJar、javadocJar与metadata并核对SHA-256。Expected：读回一致。

### Task 12: rpc-api 1.1.0事件契约

**Files:**
- Create: `rpc-api/src/main/java/com/kellen/rpc/user/event/UserAccountCreatedEvent.java`：字段`String eventId`、`Instant occurredAt`、`String tenantId`、`String userId`、`String username`、`String nickname`
- Test: `rpc-api/src/test/java/com/kellen/rpc/user/event/UserAccountCreatedEventTest.java`
- Modify: `rpc-api/build.gradle`（`version='1.1.0'`）、README与`docs/ai-coding/RPC_API_CODING_SPEC.md`（事件类放`<领域>.event`包）、`docs/ai-coding/TESTING_SPEC.md`的RabbitMQ字样

- [ ] **Step 1: 写失败测试**：`javaSerializationRoundTrip`（各字段原样还原）；`hasNoSensitiveFields`（反射检查没有名字含`password`、`secret`、`token`、`authorit`的字段）。
- [ ] **Step 2–4:** 运行失败、实现、`./gradlew test javadoc`全过。
- [ ] **Step 5:** 提交`feat(user): 新增账号已创建事件契约（版本1.0.9 → 1.1.0）`，交付并读回CI；记下完整SHA；经`run-maven-release.py`发布并用`maven-readback.py`读回。Expected：读回一致。

### Task 13: Nacos新增RocketMQ配置

**Files（utils/ops/github-dev）:**
- Modify: `nacos/DEFAULT_GROUP/reuse-configuration.yaml`：`custom.infra-rocketmq-host: ${GITHUB_ROCKETMQ_HOST}`、`custom.infra-rocketmq-port: ${GITHUB_ROCKETMQ_PORT}`。
- Modify: `nacos/DEFAULT_GROUP/user-spring.yaml`：`rocketmq.name-server: ${custom.infra-rocketmq-host}:${custom.infra-rocketmq-port}`、`rocketmq.producer.group: github-dev-user-producer`、`rocketmq.producer.access-key: ${GITHUB_ROCKETMQ_USER_ACCESS_KEY}`、`rocketmq.producer.secret-key: ${GITHUB_ROCKETMQ_USER_SECRET_KEY}`；`fleet.mq.topic.user-account-created: github-dev-user-account-created`。
- Modify: `nacos/DEFAULT_GROUP/message-spring.yaml`：`rocketmq.name-server`（同上）、`rocketmq.consumer.access-key`/`secret-key`引用`GITHUB_ROCKETMQ_MESSAGE_*`；`fleet.mq.topic.user-account-created`、`fleet.mq.group.user-account-created: github-dev-message-user-account-created`。
- 每行紧贴注释；`security-auth.yaml`的注释改动随本任务发布。

- [ ] **Step 1:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`；`cd utils/ops/github-dev/scripts && python3 verify_configuration.py`。Expected：全过（新增键使基线比对不等价时，按第2阶段的做法更新基线并在台账说明）。
- [ ] **Step 2:** `nacos_inventory.py`只读盘点并备份；`nacos_prepare_previous.py`还原原文目录；`render_nacos.py --output <新空目录> --remote <原文目录> --keep-remote ai-agent-spring.yaml --keep-remote rag-spring.yaml`。Expected：21个候选。
- [ ] **Step 3:** `nacos_verify_candidate.py`。Expected：差异恰好是`reuse-configuration.yaml`的2个键、`user-spring.yaml`与`message-spring.yaml`新增的`rocketmq`与`fleet.mq`键，以及`security-auth.yaml`的一行注释。
- [ ] **Step 4:** `sync_nacos.py --publish --previous <原文目录>`，再`maintain_nacos_catalog.py --apply`；只读盘点复核。Expected：21项读回一致。正在运行的旧版本服务（没有rocketmq依赖）不受影响。
- [ ] **Step 5:** 提交`chore(ops): Nacos新增RocketMQ地址与user、message的RocketMQ配置`，交付并读回CI。

### Task 14: user 1.4.5发“账号已创建”事件

**Files:**
- Create: `user/src/main/java/com/kellen/auth/mq/producer/UserAccountEventProducer.java`
- Modify: `user/src/main/java/com/kellen/auth/service/impl/AuthUserServiceImpl.java`（构造器注入生产者；`syncUserTenants`之后调用）
- Modify: `user/build.gradle`（`version='1.4.5'`、`utilsVersion='1.7.2'`、`rpcApiVersion='1.1.0'`）、`.github/workflows/ci.yml`（utils与rpc-api的`ref:`）、`AGENTS.md`（第54行改为1.7.2与1.1.0）、`README.md`（删`infra-rabbitmq-addr`示例，RabbitMQ改RocketMQ，补“账号已创建事件”一句）、`docs/ai-coding/`里的RabbitMQ字样
- Test: `user/src/test/java/com/kellen/auth/mq/producer/UserAccountEventProducerTest.java`、`user/src/test/java/com/kellen/auth/service/impl/AuthUserServiceImplEventTest.java`（Mockito）

**Interfaces:**
- Consumes：任务5的`MqTemplate`、`MqSendOptions`；任务12的`UserAccountCreatedEvent`；任务13的`fleet.mq.topic.user-account-created`。
- Produces：`UserAccountEventProducer(MqTemplate mqTemplate, @Value("${fleet.mq.topic.user-account-created}") String topic)`；`void publishAccountCreated(AuthUser user, String tenantId)`：事件ID为UUID，`occurredAt`为当前时刻，调用`mqTemplate.sendAfterCommit(topic, event, MqSendOptions.none().withKeys(eventId))`。

- [ ] **Step 1: 写失败测试**
  - `usesAfterCommitWithEventIdAsKey`：参数里的主题、key等于事件ID，事件的租户、用户ID、用户名、昵称来自入参。
  - `newUserPublishesAccountCreatedEvent`：`selectOne`返回null、`insert`给实体赋ID`u-new`时，生产者被调用一次，用户ID为`u-new`，租户为`bo.getTenantId()`。
  - `existingUsernameDoesNotPublish`：`selectOne`返回已有用户时不调用生产者。
- [ ] **Step 2–4:** 运行失败、实现、测试通过。
- [ ] **Step 5:** 检索关口`rg -n -i 'rabbit|amqp' user --glob '!**/build/**' --glob '!docs/superpowers/**'`。Expected：无输出。
- [ ] **Step 6:** `./gradlew test`（带与不带私有env）加仓库密钥扫描。Expected：全过。
- [ ] **Step 7:** 提交`feat(auth): 新建账号后发账号已创建事件（版本1.4.4 → 1.4.5）`，交付，手动触发CI并读回。

### Task 15: message 1.3.0消费事件写欢迎提醒

**Files:**
- Create: `message/src/main/resources/db/migration/V6__add_source_event_id.sql`：`message_user_message`加`source_event_id varchar(64) COLLATE fleet_general_ci`及注释；`CREATE UNIQUE INDEX IF NOT EXISTS uk_message_user_message_source_event ON message_user_message (source_event_id, receiver_user_id) WHERE source_event_id IS NOT NULL`
- Create: `message/src/main/java/com/kellen/message/mq/consumer/UserAccountCreatedConsumer.java`（`@RocketMQMessageListener`见Global Constraints，实现`RocketMQListener<UserAccountCreatedEvent>`）
- Modify: `UserMessage`（`String sourceEventId`）、`UserMessageService`与`UserMessageServiceImpl`（`boolean sendWelcomeNotice(UserAccountCreatedEvent event)`：按Global Constraints的欢迎提醒写一行；唯一冲突`DuplicateKeyException`返回false，不抛出）
- Modify: `message/build.gradle`（`version='1.3.0'`、`utilsVersion='1.7.2'`、`rpcApiVersion='1.1.0'`）、`ci.yml`、`AGENTS.md`（第53行版本；技术栈与禁止事项里的RabbitMQ改RocketMQ）、`README.md`（`RabbitMQ`一节改为RocketMQ与本消费者）、`docs/ai-coding/`与`.github/copilot-instructions.md`的RabbitMQ字样
- Test: `message/src/test/java/com/kellen/message/mq/UserAccountCreatedConsumerLiveTest.java`（`FLEET_PG_TEST_URL`；用`PostgresqlSchemaFixture`的独立schema；调用`onMessage`前后用真实的`MqContextConsumeMessageHook`处理一条带属性的`MessageExt`）；`PostgresqlSchemaLiveTest`

- [ ] **Step 1: 写失败测试**
  - `PostgresqlSchemaLiveTest.migratesEmptySchemaToLatestVersion`：成功版本`1–6`，`source_event_id`列与唯一索引存在。
  - `welcomeNoticeWrittenForNewAccount`：事件（租户`t`、用户`u`、昵称`小明`）、属性`fleet_tenant_id=t`、`fleet_user_id=op`、`fleet_dept_id=d`，入库一行：`SYSTEM_NOTICE`、`sender_name=system`、`sender_user_id`为空、`receiver_user_id=u`、标题`欢迎使用`、正文`你好，小明，你的账号已创建。`、`SENT`、`UNREAD`、`tenant_id=t`、`owner_user_id=op`、`dept_id=d`、`create_by=op`、`create_dept=d`、`source_event_id`为事件ID。
  - `usernameUsedWhenNicknameBlank`：昵称为空时正文用用户名。
  - `duplicateEventInsertsOnlyOneNotice`：同一事件处理两次，只有一行，第二次不抛异常。
  - `consumesEventWithUnknownField`：经utils装配的`RocketMQMessageConverter`把多一个未知字段的JSON转成事件后照常入库。
- [ ] **Step 2–4:** 带私有env运行失败、实现、通过。
- [ ] **Step 5: 副本迁移**：`nas_pg.py clone github_dev_message fleet_pg_test_phase3_clone_message`，用`run_flyway.py`迁到最新。Expected：V6成功；比完删除副本。
- [ ] **Step 6:** 检索关口`rg -n -i 'rabbit|amqp' message --glob '!**/build/**' --glob '!docs/superpowers/**'`。Expected：无输出。
- [ ] **Step 7:** `./gradlew test`（带与不带私有env）加密钥扫描。Expected：全过。
- [ ] **Step 8:** 提交`feat(message): 消费账号已创建事件写欢迎提醒，站内信加来源事件ID去重（版本1.2.9 → 1.3.0）`，交付，手动触发CI并读回（CI里的真库测试应实际执行，不是跳过）。

### Task 16: 其余服务升级utils；ai-agent文档

**Files:**
- Modify: ai、ai-memory、rag、mcp、report各自的`build.gradle`（版本见Global Constraints，`utilsVersion='1.7.2'`）、`.github/workflows/ci.yml`（utils的`ref:`）、`AGENTS.md`（utils版本行）
- Modify: 文档里的RabbitMQ字样：ai-memory `AGENTS.md`的「RabbitMQ异步索引」改为「RocketMQ异步索引（未实现）」；ai-memory、rag、mcp的`.github/copilot-instructions.md`；rag的`docs/ai-coding/`；ai-agent的`README.md`与`docs/ai-coding/README.md`
- 任务8 Step 4判定需要显式传递的地方，在所属服务改并补测试

- [ ] **Step 1:** 每个仓库：检索关口`rg -n -i 'rabbit|amqp' <仓库> --glob '!**/build/**' --glob '!docs/superpowers/**'`无输出；`./gradlew test`（带与不带私有env）加密钥扫描全过。
- [ ] **Step 2:** 每个仓库提交`build: 升级utils 1.7.2（版本<旧> → <新>）`；ai-agent提交`docs: RabbitMQ描述改为RocketMQ`；分别交付并读回CI（ai手动触发）。rag推送会触发引擎镜像构建，NAS引擎不换版。

### Task 17: 整体运行验证

前提：任务2–16都有完成行，所有精确提交的CI为success；有缺项就回到对应任务。

- [ ] **Step 1:** `lsof`核对9个端口。Expected：空闲；被其他项目占用就停下请用户处理。
- [ ] **Step 2:** 请用户在IDEA起9个服务；核对端口归属与工作目录是个人GitHub的九个服务。Expected：健康UP；user日志里生产者启动，message日志里监听容器启动，消费组为`github-dev-message-user-account-created-canary-kellen-local`；没有`NoSuchBeanDefinitionException`、`ClassNotFoundException`；ai、ai-memory、rag、mcp、report的定时任务照常（各找一条定时任务的日志或数据变化为证，找不到的在台账注明）。
- [ ] **Step 3:** 在`~/.config/kellen-github/tools/chain-verify.py`里：
  - 新增`message_db(sql)`，同`user_db`，库为`github_dev_message`。
  - 新增消息步骤：经网关`POST /auth/users`新建测试账号（用户名`phase4-mq-probe-<时间>`，租户同登录用户，随机口令只在内存里）；轮询`GET /messages/user-messages?receiverUserId=<新账号ID>&tenantId=<租户>&current=1&size=10`至多30秒，断言恰好一条，类型`SYSTEM_NOTICE`，标题`欢迎使用`；`message_db`核对`create_by`等于登录用户ID、`create_dept`等于登录部门、`source_event_id`非空。
  - 清理：`DELETE /auth/users/<新账号ID>`，用`message_db`删除该行。
  - 最后一行改为`phase 4 chain verification OK`。
  README同步。
- [ ] **Step 4:** `python3 -I run_chain_verify.py`与`KW_PROBE_NO_GRANT=1 python3 -I run_chain_verify.py`串行各跑一次。Expected：两次的最后一行都是`phase 4 chain verification OK`（后者带“不带灰度授权”标注）。
- [ ] **Step 5:** `nas_mq.py progress github-dev-message-user-account-created-canary-kellen-local`的堆积为0；`nas_mq.py consumers github-dev-message-user-account-created`显示该组不存在或没有在线消费者。对ai、ai-memory、rag、mcp、report的进程逐个`lsof -nP -iTCP -sTCP:ESTABLISHED -a -p <pid>`。Expected：没有连到NAS 9876或10911的连接。
- [ ] **Step 6: 重复投递**：用dashboard把一条已消费的消息重新投递给该消费组。Expected：`message_db`里该`source_event_id`仍只有一行；之后清理。
- [ ] **Step 7:** NAS只读复核：`free -m`、3个RocketMQ容器的`docker stats`，并确认`orb-rabbitmq`不在、RabbitMQ备份可读。Expected：满足任务2 Step 7的门槛。
- [ ] **Step 8:** 台账记录证据（各提交、CI、制品读回、启动日志、两条链路、堆积与连接、重复投递、内存）。

### Task 18: 收尾

- [ ] **Step 1:** 逐节核对规格都有对应证据；台账汇总全部`Ruling:`与推迟的小问题。
- [ ] **Step 2:** 按执行方式做全分支最终复查。
- [ ] **Step 3:** 删除NAS上的`fleet_pg_test_phase3`库与同名角色、本机`~/.config/kellen-github/phase3/fleet-pg-test.env`；`phase3-backup/`与RabbitMQ备份保留到用户确认不再回退；清理各仓本计划的短分支。
- [ ] **Step 4:** 用`llm-wiki-archive`归档（台账全文进证据页附录；6阶段路线页第4阶段的“Spring Cloud Stream”口径改为rocketmq-spring），以“已提交推送 <hash>”为准；台账目录在归档后由用户在终端删除。
