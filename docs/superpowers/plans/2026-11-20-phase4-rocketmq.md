# 第4阶段RocketMQ消息 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NAS上线RocketMQ 5.5.0与dashboard 2.1.0并删除RabbitMQ；utils主制品加通用的RocketMQ消息模块（Spring Cloud Stream加SCA binder）；第一条业务“新建账号发欢迎提醒”上线并通过运行态整体验证。

**Architecture:** 先在NAS部署并实测RocketMQ，再给utils分项提交：依赖与安全默认值、发送工具、消费端上下文还原、泳道隔离、上下文不向新建线程继承、真库测试、文档，然后一次发版1.7.2。rpc-api发事件契约1.1.0；Nacos加地址与两个服务的Stream配置；user发事件、message消费写站内信；其余5个依赖utils的服务只升级utils。最后由用户在IDEA起9个服务做两条全链路加消息收发的整体验证。

**Tech Stack:** Gradle 9.9.1、JDK 21、Spring Boot 4.0.9、Spring Cloud 2025.1.4（Spring Cloud Stream 5.0.4）、Spring Cloud Alibaba 2025.1.0.0（`spring-cloud-starter-stream-rocketmq`，rocketmq-client 5.3.1）、RocketMQ 5.5.0（NAS容器）、rocketmq-dashboard 2.1.0、MyBatis-Plus、Flyway、TransmittableThreadLocal、PostgreSQL 18（NAS `orb-postgres`）。

**Spec:** `docs/superpowers/specs/2026-11-20-phase4-rocketmq-design.md`

## Global Constraints

- 范围：utils、rpc-api、user、message、ai、ai-memory、rag、mcp、report与kellen-workspace；ai-agent只改文档；gateway、admin-web不改；美联项目不动。NAS只动RocketMQ与RabbitMQ的服务，不动`orb-litellm`与其他容器。
- 版本（VERSIONING_SPEC：补丁位到9时进位）：utils 1.7.1→1.7.2；rpc-api 1.0.9→1.1.0；user 1.4.4→1.4.5；message 1.2.9→1.3.0；ai 1.8.1→1.8.2；ai-memory 1.2.1→1.2.2；rag 1.3.4→1.3.5；mcp 1.2.9→1.3.0；report 1.2.8→1.2.9。推送前核对远端tag、在途分支与私有Maven没有占用，占用就顺延。
- 依赖：utils以`api`声明`com.alibaba.cloud:spring-cloud-starter-stream-rocketmq`（版本由SCA BOM管理，不覆盖rocketmq-client）；`testImplementation 'org.springframework.cloud:spring-cloud-stream-test-binder'`（Spring Cloud BOM管理）；Nacos客户端3.3.0的强制对齐不变。
- 镜像：`apache/rocketmq:5.5.0`、`apacherocketmq/rocketmq-dashboard:2.1.0`，编排里写`镜像@sha256:<RepoDigests摘要>`。
- 包：对外API在`com.kellen.utils.mq`（`MqTemplate`、`MqSendOptions`、`MqDelayLevel`、`MqHeaders`、`MqSendException`）；装配与内部实现在`com.kellen.config.mq`，自动配置登记进`AutoConfiguration.imports`，环境后置处理器按Boot 4的`EnvironmentPostProcessor`接口登记到`META-INF/spring.factories`。
- 消息头（`MqHeaders`常量）：`fleet_tenant_id`、`fleet_user_id`、`fleet_dept_id`、`fleet_datasource`、`fleet_lane`、`fleet_canary_tag`、`fleet_order_key`；tag、key、延时等级用RocketMQ的`TAGS`、`KEYS`（多个key以空格分隔）、`DELAY`。
- 实例泳道：用`Binder`读`spring.cloud.nacos.discovery.metadata`映射，`traffic.lane`缺省`stable`，`canary.tag`缺省无；值必须匹配`^[A-Za-z0-9._:-]{1,64}$`，否则启动失败。
- 泳道订阅（写在`spring.cloud.stream.rocketmq.bindings.<绑定>.consumer.subscription`）：stable为`sql:(fleet_lane IS NULL OR fleet_lane = 'stable')`；其他泳道为`sql:(fleet_lane = '<泳道>' AND fleet_canary_tag = '<标签>')`，没有标签时为`sql:(fleet_lane = '<泳道>')`；绑定原有tag表达式`a || b`合并为`sql:(TAGS IN ('a', 'b')) AND (<泳道条件>)`。非stable实例的消费组加后缀`-<泳道>-<标签>`（没有标签时`-<泳道>`）。只改名字匹配`*-in-*`的绑定。
- 名字：主题`github-dev-user-account-created`（4个队列）；消费组`github-dev-message-user-account-created`；user的输出绑定`userAccountCreated-out-0`（`spring.cloud.stream.output-bindings: userAccountCreated`），message的输入绑定`userAccountCreated-in-0`（`spring.cloud.function.definition: userAccountCreated`）；生产者组`github-dev-user-producer`；账号`github-dev-admin`（超级用户）、`github-dev-user`、`github-dev-message`、`github-dev-utils-test`；真库测试主题`github-dev-utils-live-test`、`github-dev-utils-live-test-orderly`，测试消费组前缀`github-dev-utils-test-`。
- 欢迎提醒：类型`SYSTEM_NOTICE`，发件人名`system`、发件人ID为空，标题`欢迎使用`，正文`你好，<称呼>，你的账号已创建。`（称呼：昵称非空用昵称，否则用户名），发送状态`SENT`，未读；`owner_user_id`、`dept_id`取还原的操作人（与`buildSendMessage`一致）。
- 重试：message的输入绑定`consumer.max-attempts: 1`（不在进程内重试），`spring.cloud.stream.rocketmq.bindings.userAccountCreated-in-0.consumer.push.max-reconsume-times: 3`，之后进RocketMQ死信。
- 私有值：dev.env新增`GITHUB_ROCKETMQ_HOST`、`GITHUB_ROCKETMQ_PORT`、`GITHUB_ROCKETMQ_USER_ACCESS_KEY`、`GITHUB_ROCKETMQ_USER_SECRET_KEY`、`GITHUB_ROCKETMQ_MESSAGE_ACCESS_KEY`、`GITHUB_ROCKETMQ_MESSAGE_SECRET_KEY`；管理账号与dashboard口令只在NAS编排与`~/.config/kellen-github/phase4/`（目录0700、文件0600）；真库测试变量`FLEET_MQ_TEST_NAMESRV`、`FLEET_MQ_TEST_ACCESS_KEY`、`FLEET_MQ_TEST_SECRET_KEY`放`~/.config/kellen-github/phase4/fleet-mq-test.env`。口令随机生成，不打印、不进Git与Wiki。
- 构建一律JDK 21：`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`。
- Git：开工、提交前、推送前都`git fetch`；每个任务从最新`origin/main`建短分支`codex/phase4-<任务>`，本机验证全过后快进合入`main`并推送、删短分支；远端前进就rebase并重跑验证，不强推；读回该精确提交的CI为success再开下一个任务。ai、gateway、message、user推送不触发CI，用`gh workflow run ci.yml --ref main`手动触发并核对`headSha`。服务CI里固定的utils、rpc-api检出`ref:`在各服务任务里改为本阶段发布提交的完整SHA。
- NAS：只用`ssh -o BatchMode=yes qw-nas`与`sudo -n docker …`；编排与文件操作在`busybox:1.37.0-musl`临时容器里做（`cp -p`）；改编排前另存`docker-compose.yml.before-<改动>-<日期>`；compose固定`-p orb_env`，单服务用`up -d --no-deps <服务>`；输出遮掉9位以上数字串；编排路径与真实地址不进Git、Wiki或回复；代理、VPN、组网相关的配置、端口与日志一律不碰。
- 凭据：私有Maven只经`~/.config/kellen-github/observability-20261106/run-maven-release.py`；Nacos只经私有`dev.env`与`utils/ops/github-dev/scripts`；登录口令只由`run_chain_verify.py`在子进程内使用。
- 运行态：9个服务（端口7100、7200、7300、7301、7310、7400、7500、7600、7700）由用户在IDEA启停；运行配置不挂JVM参数。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-20-phase4-rocketmq/progress.md`，首行`# SDD ledger — plan: <本计划路径>`，逐任务记完成行（提交区间、测试命令与结果）与`Ruling:`行；可复用脚本放`~/.config/kellen-github/tools`。
- 已定裁定（规格留给实施的细节）：
  - 消息头由`MqTemplate`统一写，不用binder的发送钩子；业务不直接用`StreamBridge`。
  - 消费端上下文用Spring Integration全局通道拦截器还原：`@GlobalChannelInterceptor(patterns = "*-in-*")`，`preSend`设置并记下原值，`afterSendCompletion`恢复。
  - 泳道改写在环境层做：binder的RocketMQ扩展属性Bean在binder子容器里，主容器的Bean后置处理器改不到。
  - message消费测试用现有`FLEET_PG_TEST_URL`真库测试方式（CI用postgres:18服务容器）加Stream测试binder；utils的RocketMQ真库测试本机跑、CI跳过。
  - 事件时间用`Instant`；事件类按rpc-api现有DTO写法（`Serializable`、无参构造、getter/setter，不引入Jackson注解）。

## Review Focus

1. 不收发消息的服务（ai、ai-memory、rag、mcp、report）带上binder后，容器里唯一的函数式Bean被自动绑定、去连RocketMQ。期望：不绑定、不连接。→ 任务4 `functionalBeanIsNotBoundWithoutDefinition`；任务17 Step 5。
2. 消费处理抛异常（触发RocketMQ重投）后，消费线程残留租户或操作人，下一条消息写错归属。期望：异常后也恢复原值。→ 任务6 `restoresPreviousContextWhenHandlerThrows`。
3. 生产方以后给事件加字段，旧消费方反序列化失败、消息进死信。期望：忽略未知字段照常消费。→ 任务15 `consumesEventWithUnknownField`。
4. RocketMQ不可用或发送失败时，建号接口跟着失败或回滚。期望：账号照常建好，只缺这条提醒，日志记绑定名与key。→ 任务5 `sendAfterCommitFailureIsLoggedNotThrown`。
5. Nacos热刷新或环境后置处理器重复执行后，消费组被重复加后缀、订阅被套两层条件。期望：结果幂等。→ 任务7 `laneRoutingIsIdempotent`。

---

### Task 1: 提交计划

**Files:**
- Create: `kellen-workspace/docs/superpowers/plans/2026-11-20-phase4-rocketmq.md`

- [ ] **Step 1:** `python3 -m unittest discover -s tests`（kellen-workspace）。Expected：OK。
- [ ] **Step 2:** 提交`docs: 第4阶段RocketMQ执行计划`，与已提交的设计一起推送并读回CI；新建台账。

### Task 2: NAS部署RocketMQ与dashboard

**Files（Git外）:**
- Modify: NAS编排`docker-compose.yml`：新增`orb-rocketmq-namesrv`、`orb-rocketmq-broker`、`orb-rocketmq-dashboard`、`orb-rocketmq-init`与命名卷（namesrv日志、broker存储、broker日志、RocketMQ配置、dashboard数据）。
- Create: `~/.config/kellen-github/phase4/`：各账号口令、`fleet-mq-test.env`。
- Create: `~/.config/kellen-github/tools/nas_mq.py`：只读子命令`topics`、`progress <消费组>`、`consumers <消费组>`，在broker容器里用管理账号的`tools.yml`执行`mqadmin`。
- Modify: 私有`dev.env`：Global Constraints列出的6个变量（主机取客户端访问NAS用的地址，与现有中间件同一来源）。

- [ ] **Step 1:** NAS上`docker pull`两个镜像，取`RepoDigests`；在镜像里确认`ROCKETMQ_HOME=/home/rocketmq/rocketmq-5.5.0`、`docker-entrypoint.sh`的启动方式、`JAVA_OPT_EXT`是否生效，以及dashboard镜像的`JAVA_OPTS`。Expected：台账记下两个摘要与启动写法。
- [ ] **Step 2:** 生成`github-dev-admin`、`github-dev-user`、`github-dev-message`、`github-dev-utils-test`四个账号与dashboard登录的随机口令，写入私有文件（0600）。
- [ ] **Step 3:** 另存编排备份，按现有内联配置的做法把以下文件写入RocketMQ配置卷：
  - broker.conf：`brokerClusterName=DefaultCluster`、`brokerName=broker-a`、`brokerId=0`、`brokerRole=ASYNC_MASTER`、`flushDiskType=ASYNC_FLUSH`、`namesrvAddr=orb-rocketmq-namesrv:9876`、`brokerIP1=<私有>`、`listenPort=10911`、`autoCreateTopicEnable=false`、`enablePropertyFilter=true`、`fileReservedTime=48`、`traceTopicEnable=true`、`authenticationEnabled=true`、`authenticationProvider=org.apache.rocketmq.auth.authentication.provider.DefaultAuthenticationProvider`、`authenticationMetadataProvider=org.apache.rocketmq.auth.authentication.provider.LocalAuthenticationMetadataProvider`、`authorizationEnabled=true`、`authorizationProvider=org.apache.rocketmq.auth.authorization.provider.DefaultAuthorizationProvider`、`authorizationMetadataProvider=org.apache.rocketmq.auth.authorization.provider.LocalAuthorizationMetadataProvider`、`initAuthenticationUser={"username":"github-dev-admin","password":"<私有>"}`、`innerClientAuthenticationCredentials={"accessKey":"github-dev-admin","secretKey":"<私有>"}`；
  - `tools.yml`（管理账号，给init与`nas_mq.py`用）；dashboard的`users.properties`（一个管理员）。
  新增4个服务：namesrv `JAVA_OPT_EXT=-Xms256m -Xmx256m`、上限512m、端口9876；broker `-Xms512m -Xmx512m -XX:MaxDirectMemorySize=256m`、上限1536m、端口10911；dashboard `-Xms256m -Xmx256m`与`rocketmq.config.namesrvAddrs=orb-rocketmq-namesrv:9876`、`isVIPChannel=false`、`loginRequired=true`、`dataPath`指向dashboard数据卷、`accessKey`/`secretKey`为管理账号，上限768m，宿主端口选空闲端口；init上限256m、不重启。重启策略同其他常驻服务。`docker compose -p orb_env -f <文件> config -q`。Expected：校验通过。
- [ ] **Step 4:** `up -d --no-deps orb-rocketmq-namesrv orb-rocketmq-broker orb-rocketmq-dashboard`。Expected：日志有`The Name Server boot success`与broker的`boot success`；dashboard登录页HTTP 200。
- [ ] **Step 5:** init（`up --no-deps orb-rocketmq-init`）幂等执行`mqadmin`：`updateTopic -c DefaultCluster`建3个主题（4读4写队列）；`createUser -c DefaultCluster -u <账号> -p <口令> -t Normal`建3个普通账号；`createAcl -c DefaultCluster -s User:<账号> -r <资源> -a <动作> -d Allow`：user对`Topic:github-dev-user-account-created`为`Pub`；message对该主题与`Group:github-dev-message-*`为`Sub`；测试账号对两个测试主题为`Pub,Sub`、对`Group:github-dev-utils-test-*`为`Sub`；三个账号对`Topic:RMQ_SYS_TRACE_TOPIC`为`Pub`。Expected：退出0；再跑一次仍退出0。init能经namesrv连到broker，说明容器里经`brokerIP1`可达；连不上就按规格改用宿主网络或调整地址，台账记`Ruling:`。
- [ ] **Step 6:** `nas_mq.py topics`。Expected：列出3个主题与轨迹主题。用管理账号登录dashboard。Expected：能看到集群与主题。
- [ ] **Step 7:** 只读复核内存：`free -m`，以及3个新容器的`docker stats`。Expected：可用内存不低于3.5GB，交换区已用量比上线前增加不超过500MB；不满足就停下报告用户。台账记录摘要、端口、卷与内存。

### Task 3: 删除RabbitMQ

**Files（Git外）:**
- Create: NAS编排目录`backups/rabbitmq-retire-20261120/`（0700）：数据目录打包、`rabbitmqctl export_definitions`导出的definitions、编排原文、`SHA256SUMS`。
- Modify: NAS编排（删`orb-rabbitmq`服务段）；私有`dev.env`（删4个`GITHUB_RABBITMQ_*`变量）。

- [ ] **Step 1:** 只读确认仍然没有连接、队列消息数为0。Expected：同设计里的现场。
- [ ] **Step 2:** 备份三样并写`SHA256SUMS`，在busybox里`sha256sum -c`读回。Expected：全部OK。
- [ ] **Step 3:** 另存编排备份；停止并删除`orb-rabbitmq`容器；删编排服务段；`config -q`。Expected：校验通过；其他容器的ID与启动时间不变。
- [ ] **Step 4:** 删除数据目录与`rabbitmq:3.11-management`镜像；删私有变量。Expected：`docker ps -a`没有`orb-rabbitmq`；备份目录完整。

### Task 4: utils依赖与安全默认值

**Files:**
- Modify: `utils/build.gradle`（`api 'com.alibaba.cloud:spring-cloud-starter-stream-rocketmq'`，`testImplementation 'org.springframework.cloud:spring-cloud-stream-test-binder'`）
- Create: `utils/src/main/java/com/kellen/config/mq/MqDefaultsEnvironmentPostProcessor.java`
- Create: `utils/src/main/resources/META-INF/spring.factories`
- Test: `utils/src/test/java/com/kellen/config/mq/MqDefaultsTest.java`

**Interfaces:**
- Produces：依赖utils的服务都带上Stream与binder；环境里有最低优先级的属性源`fleetMqDefaults`，内容为`spring.cloud.stream.function.autodetect=false`，服务自己的配置可以覆盖。

- [ ] **Step 1: 写失败测试**
  - `autodetectDefaultsToFalse`：只装后置处理器的`SpringApplication`，环境里该属性为`false`；另设`spring.cloud.stream.function.autodetect=true`时为`true`。
  - `functionalBeanIsNotBoundWithoutDefinition`：测试binder上下文里只有一个`Consumer<String>` Bean、不设`spring.cloud.function.definition`，`BindingService`没有任何输入绑定。
  - `functionalBeanIsBoundWithDefinition`：设`spring.cloud.function.definition`为该Bean后，`InputDestination`发的消息能被它收到。
- [ ] **Step 2:** `./gradlew test --tests 'com.kellen.config.mq.MqDefaultsTest'`。Expected：前两条失败。
- [ ] **Step 3:** 实现并登记。
- [ ] **Step 4:** `./gradlew test javadoc`；`dependencyInsight --configuration runtimeClasspath`分别查`spring-cloud-stream`、`rocketmq-client`、`nacos-client`、`netty-common`。Expected：全过；依次为5.0.4、5.3.1、3.3.0，`netty-common`只有一个版本且与改动前相同（改动前先记下）。
- [ ] **Step 5:** 提交`feat(mq): 引入Spring Cloud Stream与RocketMQ binder并默认关闭函数自动绑定`，交付并读回CI。

### Task 5: utils发送工具

**Files:**
- Create: `utils/src/main/java/com/kellen/utils/mq/MqTemplate.java`、`MqSendOptions.java`、`MqDelayLevel.java`、`MqHeaders.java`、`MqSendException.java`
- Create: `utils/src/main/java/com/kellen/config/mq/MqInstanceLane.java`、`MqAutoConfiguration.java`（`@ConditionalOnClass(StreamBridge.class)`），登记进`AutoConfiguration.imports`
- Test: `utils/src/test/java/com/kellen/utils/mq/MqTemplateTest.java`

**Interfaces:**
- Produces：`MqTemplate(StreamBridge streamBridge, MqInstanceLane lane)`；`void send(String bindingName, Object payload)`；`void send(String bindingName, Object payload, MqSendOptions options)`（失败抛`MqSendException`）；`void sendAfterCommit(String bindingName, Object payload)`；`void sendAfterCommit(String bindingName, Object payload, MqSendOptions options)`（调用时抓取上下文快照；有事务时提交后发、回滚不发；没有事务时立即发；提交后失败只记`error`日志，内容为绑定名、key与`fleet_tenant_id`，不含正文）。
- Produces：`record MqSendOptions(String tag, List<String> keys, MqDelayLevel delay, String orderKey)`，`static MqSendOptions none()`，`withTag(String)`、`withKeys(String...)`、`withDelay(MqDelayLevel)`、`withOrderKey(String)`。
- Produces：`enum MqDelayLevel`：`SECONDS_1, SECONDS_5, SECONDS_10, SECONDS_30, MINUTES_1 … MINUTES_10, MINUTES_20, MINUTES_30, HOURS_1, HOURS_2`，`int level()`依次为1–18。
- Produces：`record MqInstanceLane(String lane, String canaryTag)`，`static MqInstanceLane resolve(Environment environment)`（读法与校验见Global Constraints）。

- [ ] **Step 1: 写失败测试**（测试binder的`OutputDestination`）
  - `sendWritesContextHeaders`：租户`t1`、操作人`u1`/部门`d1`、数据源`gray`、实例`canary`/`kellen-local`时，收到的消息头依次为`fleet_tenant_id=t1`、`fleet_user_id=u1`、`fleet_dept_id=d1`、`fleet_datasource=gray`、`fleet_lane=canary`、`fleet_canary_tag=kellen-local`。
  - `sendWithoutContextOmitsEmptyHeaders`：没有上下文、实例为stable时，没有租户、操作人、数据源、标签头，`fleet_lane=stable`。
  - `optionsMapToRocketMqHeaders`：`withTag("a").withKeys("k1","k2").withDelay(SECONDS_10).withOrderKey("o1")`得到`TAGS=a`、`KEYS=k1 k2`、`DELAY=3`、`fleet_order_key=o1`。
  - `sendAfterCommitSendsOnlyAfterCommit`：事务里调用并在提交前清掉上下文；提交前没有消息，提交后有一条，头里是调用时的上下文。
  - `sendAfterCommitDropsOnRollback`、`sendAfterCommitWithoutTransactionSendsNow`。
  - `sendAfterCommitFailureIsLoggedNotThrown`：`StreamBridge`在提交后返回false或抛异常，调用方不收到异常；日志含绑定名与key，不含正文里的字符串。
  - `sendFailureThrowsMqSendException`：`StreamBridge.send`返回false时`send`抛`MqSendException`。
- [ ] **Step 2:** 运行该测试类。Expected：编译失败或全部失败。
- [ ] **Step 3:** 实现：提交后发送用`TransactionSynchronizationManager.registerSynchronization`；消息头在`MqTemplate`里组装。
- [ ] **Step 4:** `./gradlew test javadoc`。Expected：全过。
- [ ] **Step 5:** 提交`feat(mq): 新增MqTemplate发送工具（上下文消息头、tag与key、延时等级、顺序键、事务提交后发送）`，交付并读回CI。

### Task 6: utils消费端上下文还原

**Files:**
- Create: `utils/src/main/java/com/kellen/config/mq/MqConsumerContextInterceptor.java`（在`MqAutoConfiguration`里以`@GlobalChannelInterceptor(patterns = "*-in-*")`登记）
- Test: `utils/src/test/java/com/kellen/config/mq/MqConsumerContextInterceptorTest.java`

**Interfaces:**
- Consumes：任务5的`MqHeaders`。
- Produces：所有输入绑定的处理函数执行期间，`TenantContextHolder.getTenantId()`、`UserContextHolder.get()`（`SecurityUser`只填`userId`、`tenantId`、`deptId`，`authorities`与`dataScopeDeptIds`为空列表、`dataScope`为null）、`DynamicSourceTtl.get()`等于消息头的值；执行完恢复为执行前的值。

- [ ] **Step 1: 写失败测试**（测试binder的`InputDestination`）
  - `handlerSeesRestoredContext`：带三类头的消息，处理函数里读到`t1`、`u1`/`d1`/`t1`、`gray`。
  - `contextRestoredAfterHandler`：处理完，同一线程上的三类上下文回到处理前（为空）。
  - `restoresPreviousContextWhenHandlerThrows`：处理函数抛异常时上下文同样恢复，异常照常抛给binder。
  - `messageWithoutHeadersLeavesContextEmpty`。
  - `restoredUserCarriesNoAuthorities`。
- [ ] **Step 2–4:** 同任务5的Step 2–4。
- [ ] **Step 5:** 提交`feat(mq): 消费端按消息头还原租户、操作人与数据源`，交付并读回CI。

### Task 7: utils泳道隔离

**Files:**
- Create: `utils/src/main/java/com/kellen/config/mq/MqLaneRoutingEnvironmentPostProcessor.java`（`getOrder()`为`ConfigDataEnvironmentPostProcessor.ORDER + 1`，在配置数据处理器之后执行，能读到Nacos导入的配置；把改写结果放进最高优先级的属性源`fleetMqLaneRouting`），登记到`spring.factories`
- Test: `utils/src/test/java/com/kellen/config/mq/MqLaneRoutingEnvironmentPostProcessorTest.java`

**Interfaces:**
- Consumes：任务5的`MqInstanceLane.resolve`。
- Produces：对每个名字匹配`*-in-*`、配了`spring.cloud.stream.bindings.<绑定>.group`的绑定，按Global Constraints改写`group`与`spring.cloud.stream.rocketmq.bindings.<绑定>.consumer.subscription`；读原值时跳过`fleetMqLaneRouting`自身。

- [ ] **Step 1: 写失败测试**
  - `stableInstanceKeepsGroupAndFiltersStableLane`：group不变，订阅为`sql:(fleet_lane IS NULL OR fleet_lane = 'stable')`。
  - `canaryInstanceSuffixesGroupAndFiltersOwnTag`：元数据`traffic.lane=canary`、`canary.tag=kellen-local`，group为`g-canary-kellen-local`，订阅为`sql:(fleet_lane = 'canary' AND fleet_canary_tag = 'kellen-local')`。
  - `canaryInstanceWithoutTagFiltersLaneOnly`：group为`g-canary`，订阅`sql:(fleet_lane = 'canary')`。
  - `existingTagSubscriptionIsMergedIntoSql`：原订阅`a || b`得到`sql:(TAGS IN ('a', 'b')) AND (fleet_lane IS NULL OR fleet_lane = 'stable')`。
  - `outputBindingsUntouched`：`*-out-*`的配置不变。
  - `laneRoutingIsIdempotent`：后置处理器对同一环境执行两次，结果与执行一次相同。
  - `invalidLaneFailsStartup`：`traffic.lane`为`canary' OR '1'='1`时启动失败，报错信息指明元数据键。
  - `bindingGroupIsSuffixedInBindingService`：测试binder上下文里，`BindingServiceProperties.getGroup("userAccountCreated-in-0")`为带后缀的名字。
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
- Create: `utils/src/test/java/com/kellen/config/mq/RocketMqLiveTest.java`（`@EnabledIfEnvironmentVariable(named = "FLEET_MQ_TEST_NAMESRV", matches = ".+")`；真binder，账号`github-dev-utils-test`；每次运行的消费组名带时间戳）

- [ ] **Step 1: 写测试**
  - `sendAndReceiveWithContext`：经`MqTemplate`发到`github-dev-utils-live-test`，30秒内收到，处理函数里租户等于发送时的租户。
  - `canaryMessageSkipsStableConsumer`：一个stable实例的消费者与一个canary/`kellen-local`实例的消费者同时订阅；canary实例发出的消息30秒内只被后者收到，前者10秒内没有收到。
  - `delayLevelDelaysDelivery`：`SECONDS_5`的消息在发送4.5秒之后才到。
  - `orderKeyKeepsOrder`：同一顺序键的20条消息发到`github-dev-utils-live-test-orderly`（生产绑定`partition-count: 4`、`partition-key-expression: headers['fleet_order_key']`），顺序消费者（`consumer.push.orderly: true`）按发送顺序收到。
  - `wrongSecretIsRejected`：错误口令发送时抛`MqSendException`。
- [ ] **Step 2:** 加载`fleet-mq-test.env`后运行。Expected：全过；不过时按原因修NAS配置（回到任务2）或代码，台账记`Ruling:`。台账另记：按前缀授权消费组时，自动创建消费组是否足够。
- [ ] **Step 3:** 不加载私有env运行。Expected：该类全部跳过。
- [ ] **Step 4:** 提交`test(mq): 新增RocketMQ真库测试（收发、泳道过滤、延时、顺序、认证）`，交付并读回CI。

### Task 10: utils文档与第3阶段遗留注释

**Files:**
- Create: `utils/docs/ai-coding/MQ_SPEC.md`：Nacos配置模板（binder地址与账号、绑定、主题、消费组、重试、顺序的分区配置）、发送（`MqTemplate`与选项，事务提交后发送）、消费（`Consumer`函数、只支持单条、必须幂等、不调需要灰度授权的RPC、需权限的查询自行处理）、泳道规则、命名规则、`mq/message|producer|consumer`分包、禁止事项（业务不直接用`StreamBridge`或RocketMQ客户端类，消息里不放凭据与完整个人信息）。
- Modify: `utils/AGENTS.md`（必读加`MQ_SPEC.md`）、`utils/README.md`（消息模块一节）、`utils/docs/ai-coding/UTILS_TOOL_CATALOG.md`、`PROJECT_CODING_SPEC.md`、`TESTING_SPEC.md`（RabbitMQ改RocketMQ，写明真库测试变量）、`NACOS_CONFIG_SPEC.md`（新键）、`utils/docs/seata-live-contract.md`、`DynamicSourceTtl.java`注释里的Rabbit字样
- Modify: `utils/ops/github-dev/nacos/DEFAULT_GROUP/security-auth.yaml`第19行注释与`utils/ops/github-dev/tests/test_postgresql_configuration.py`第70行文档串（`creator`改`create_by`）；`utils/ops/github-dev/README.md`（删`/github-dev` vhost那句，补RocketMQ）；`utils/ops/github-dev/delivery-manifest.json`里的`rabbitmq.yaml`条目（核对是否已在退役清单，按现有退役条目的写法处理）

- [ ] **Step 1:** `rg -n -i 'rabbit|amqp' utils --glob '!**/build/**' --glob '!**/bin/**' --glob '!docs/superpowers/**' --glob '!**/archive/**'`。Expected：只剩`retired-dataids.json`与README历史版本说明里的记载（台账列出保留理由）。`rg -n 'creator' utils/ops`。Expected：无输出。
- [ ] **Step 2:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`。Expected：OK（含逐行注释检查）。
- [ ] **Step 3:** 提交`docs(mq): 消息模块用法与规范；RabbitMQ描述改为RocketMQ`，交付并读回CI。

### Task 11: 发布utils 1.7.2

**Files:**
- Modify: `utils/build.gradle`（`version = '1.7.2'`）、`utils/README.md`（1.7.2变更说明：RocketMQ消息模块、上下文不向新建线程继承、注释修正）

- [ ] **Step 1:** 核对远端tag、在途分支与私有Maven没有1.7.2。Expected：未占用。
- [ ] **Step 2:** `./gradlew clean test javadoc`、仓库密钥扫描、任务4 Step 4的四项`dependencyInsight`。Expected：全过，版本同任务4。
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
- Modify: `nacos/DEFAULT_GROUP/user-spring.yaml`：`spring.cloud.stream.output-bindings`、`bindings.userAccountCreated-out-0`（`destination`、`content-type: application/json`）、`rocketmq.binder`（`name-server: ${custom.infra-rocketmq-host}:${custom.infra-rocketmq-port}`、`access-key`/`secret-key`引用`GITHUB_ROCKETMQ_USER_*`）、`rocketmq.bindings.userAccountCreated-out-0.producer.group`。
- Modify: `nacos/DEFAULT_GROUP/message-spring.yaml`：`spring.cloud.function.definition`、`bindings.userAccountCreated-in-0`（`destination`、`group`、`content-type`、`consumer.max-attempts: 1`）、`rocketmq.binder`（引用`GITHUB_ROCKETMQ_MESSAGE_*`）、`rocketmq.bindings.userAccountCreated-in-0.consumer.push.max-reconsume-times: 3`。
- 每行紧贴注释；`security-auth.yaml`的注释改动随本任务发布。

- [ ] **Step 1:** `python3 -I -m unittest discover -s utils/ops/github-dev/tests`；`cd utils/ops/github-dev/scripts && python3 verify_configuration.py`。Expected：全过（新增键使基线比对不等价时，按第2阶段的做法更新基线并在台账说明）。
- [ ] **Step 2:** `nacos_inventory.py`只读盘点并备份；`nacos_prepare_previous.py`还原原文目录；`render_nacos.py --output <新空目录> --remote <原文目录> --keep-remote ai-agent-spring.yaml --keep-remote rag-spring.yaml`。Expected：21个候选。
- [ ] **Step 3:** `nacos_verify_candidate.py`。Expected：差异恰好是`reuse-configuration.yaml`的2个键、`user-spring.yaml`与`message-spring.yaml`新增的`spring.cloud`键，以及`security-auth.yaml`的一行注释。
- [ ] **Step 4:** `sync_nacos.py --publish --previous <原文目录>`，再`maintain_nacos_catalog.py --apply`；只读盘点复核。Expected：21项读回一致。正在运行的旧版本服务（没有Stream依赖）不受影响。
- [ ] **Step 5:** 提交`chore(ops): Nacos新增RocketMQ地址与user、message的Stream配置`，交付并读回CI。

### Task 14: user 1.4.5发“账号已创建”事件

**Files:**
- Create: `user/src/main/java/com/kellen/auth/mq/producer/UserAccountEventProducer.java`
- Modify: `user/src/main/java/com/kellen/auth/service/impl/AuthUserServiceImpl.java`（构造器注入生产者；`syncUserTenants`之后调用）
- Modify: `user/build.gradle`（`version='1.4.5'`、`utilsVersion='1.7.2'`、`rpcApiVersion='1.1.0'`）、`.github/workflows/ci.yml`（utils与rpc-api的`ref:`）、`AGENTS.md`（第54行改为1.7.2与1.1.0）、`README.md`（删`infra-rabbitmq-addr`示例，RabbitMQ改RocketMQ，补“账号已创建事件”一句）、`docs/ai-coding/`里的RabbitMQ字样
- Test: `user/src/test/java/com/kellen/auth/mq/producer/UserAccountEventProducerTest.java`、`user/src/test/java/com/kellen/auth/service/impl/AuthUserServiceImplEventTest.java`（Mockito）

**Interfaces:**
- Consumes：任务5的`MqTemplate`、`MqSendOptions`；任务12的`UserAccountCreatedEvent`。
- Produces：`UserAccountEventProducer.publishAccountCreated(AuthUser user, String tenantId)`：事件ID为UUID，`occurredAt`为当前时刻，调用`mqTemplate.sendAfterCommit("userAccountCreated-out-0", event, MqSendOptions.none().withKeys(eventId))`。

- [ ] **Step 1: 写失败测试**
  - `usesAfterCommitWithEventIdAsKey`：参数里的绑定名、key等于事件ID，事件的租户、用户ID、用户名、昵称来自入参。
  - `newUserPublishesAccountCreatedEvent`：`selectOne`返回null、`insert`给实体赋ID`u-new`时，生产者被调用一次，用户ID为`u-new`，租户为`bo.getTenantId()`。
  - `existingUsernameDoesNotPublish`：`selectOne`返回已有用户时不调用生产者。
- [ ] **Step 2–4:** 运行失败、实现、测试通过。
- [ ] **Step 5:** 检索关口`rg -n -i 'rabbit|amqp' user --glob '!**/build/**' --glob '!docs/superpowers/**'`。Expected：无输出。
- [ ] **Step 6:** `./gradlew test`（带与不带私有env）加仓库密钥扫描。Expected：全过。
- [ ] **Step 7:** 提交`feat(auth): 新建账号后发账号已创建事件（版本1.4.4 → 1.4.5）`，交付，手动触发CI并读回。

### Task 15: message 1.3.0消费事件写欢迎提醒

**Files:**
- Create: `message/src/main/resources/db/migration/V6__add_source_event_id.sql`：`message_user_message`加`source_event_id varchar(64) COLLATE fleet_general_ci`及注释；`CREATE UNIQUE INDEX IF NOT EXISTS uk_message_user_message_source_event ON message_user_message (source_event_id, receiver_user_id) WHERE source_event_id IS NOT NULL`
- Create: `message/src/main/java/com/kellen/message/mq/consumer/UserAccountCreatedConsumer.java`（Bean名`userAccountCreated`，类型`Consumer<Message<UserAccountCreatedEvent>>`）
- Modify: `UserMessage`（`String sourceEventId`）、`UserMessageService`与`UserMessageServiceImpl`（`boolean sendWelcomeNotice(UserAccountCreatedEvent event)`：按Global Constraints的欢迎提醒写一行；唯一冲突`DuplicateKeyException`返回false，不抛出）
- Modify: `message/build.gradle`（`version='1.3.0'`、`utilsVersion='1.7.2'`、`rpcApiVersion='1.1.0'`）、`ci.yml`、`AGENTS.md`（第53行版本；技术栈与禁止事项里的RabbitMQ改RocketMQ）、`README.md`（`RabbitMQ`一节改为RocketMQ与本消费者）、`docs/ai-coding/`与`.github/copilot-instructions.md`的RabbitMQ字样
- Test: `message/src/test/java/com/kellen/message/mq/UserAccountCreatedConsumerLiveTest.java`（`FLEET_PG_TEST_URL`，测试binder经`InputDestination`投递，用`PostgresqlSchemaFixture`的独立schema）；`PostgresqlSchemaLiveTest`

- [ ] **Step 1: 写失败测试**
  - `PostgresqlSchemaLiveTest.migratesEmptySchemaToLatestVersion`：成功版本`1–6`，`source_event_id`列与唯一索引存在。
  - `welcomeNoticeWrittenForNewAccount`：事件（租户`t`、用户`u`、昵称`小明`）加头`fleet_tenant_id=t`、`fleet_user_id=op`、`fleet_dept_id=d`，入库一行：`SYSTEM_NOTICE`、`sender_name=system`、`sender_user_id`为空、`receiver_user_id=u`、标题`欢迎使用`、正文`你好，小明，你的账号已创建。`、`SENT`、`UNREAD`、`tenant_id=t`、`owner_user_id=op`、`dept_id=d`、`create_by=op`、`create_dept=d`、`source_event_id`为事件ID。
  - `usernameUsedWhenNicknameBlank`：昵称为空时正文用用户名。
  - `duplicateEventInsertsOnlyOneNotice`：同一消息投递两次，只有一行，第二次不抛异常。
  - `consumesEventWithUnknownField`：负载JSON多一个未知字段时照常入库。
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
- [ ] **Step 2:** 请用户在IDEA起9个服务；核对端口归属与工作目录是个人GitHub的九个服务。Expected：健康UP；user、message日志里binder启动，消费组为`github-dev-message-user-account-created-canary-kellen-local`；没有`NoSuchBeanDefinitionException`、`ClassNotFoundException`或调度器相关报错；ai、ai-memory、rag、mcp、report的定时任务照常（各找一条定时任务的日志或数据变化为证，找不到的在台账注明）。
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
- [ ] **Step 4:** 用`llm-wiki-archive`归档（台账全文进证据页附录），以“已提交推送 <hash>”为准；台账目录在归档后由用户在终端删除。
