# 第4阶段 RocketMQ 消息设计

## 已确认目标

用户2026-11-13定：个人GitHub微服务按6个阶段升级，兼容性优先，尤其注意Nacos与各组件的版本依赖，消息当时定为Spring Cloud Stream（2026-11-20改为rocketmq-spring，见修订记录）。第4阶段内容：NAS部署RocketMQ 5.5与rocketmq-dashboard并固定版本；utils加消息模块（RocketMQ收发工具类）；删除NAS上的RabbitMQ容器（utils里的RabbitMQ配置第2阶段已删）。第1–3阶段已完成；本文覆盖第4阶段。

用户2026-11-20在会话里依次确认（新加坡时间11:17–13:32）：

- 「开工」。
- 第一条真正收发消息的业务选「新建账号发欢迎提醒」：user新建账号后发“账号已创建”事件，message消费后给新账号写一条系统提醒站内信。
- 可靠性选「能接受：提交后再发」：建号事务提交后才发，发送失败只记日志，极少数情况下这条提醒会缺。
- 消息模块位置选「方案二：并进utils主制品」，不另建子模块。
- 11:41「RocketMQ写一个utils工具模块吧，到时候方便ai调用使用，可以参考github的主流项目完成。」：模块按主流项目写成通用工具，ai等服务以后接入只加配置和自己的收发代码。
- 第3阶段推迟的3个小问题选「三条都修」，随本阶段的utils发版与服务升级一起做。
- 13:06设计通过；13:19选本会话内联执行。
- 13:32「如果Stream不需要用可以不要，以主流方式RocketMQ完成消息队列功能和工具类」。

范围只有个人GitHub项目，美联项目不动。

为什么现在做：RabbitMQ 3.11早已停止维护，也没有任何服务在用；RocketMQ是国内同类项目的主流选择（芋道等）。在第5、6阶段之前把消息基础设施、上下文传递和泳道隔离定下来，以后的业务只写收发代码。

## 修订记录

- 2026-11-20 13:32（用户）：同意不用Spring Cloud Stream。核对后改用主流项目多数采用的rocketmq-spring（发送`RocketMQTemplate`、消费`@RocketMQMessageListener`）：只接RocketMQ一种队列，Stream换队列的好处用不上；SCA binder只有18个延时等级、没有消费端钩子，泳道与上下文都要绕路；把Stream放进utils主制品，会给所有服务带上Spring Integration与函数自动绑定。下文已按此改写，原Stream版本见Git历史`6b23281`。
- 2026-11-20 20:04（用户）：上线后 NAS 交换区已用 4073MB，比上线前多 991MB，超过“增量不超过 500MB”。核对：可用内存 5129MB；RocketMQ 自身几乎不用交换（broker 37MiB），涨的是闲置服务被挤出的冷页（nacos 652MiB、conductor 376MiB 等）；vmstat 采样只有零星换入、没有换出。用户选择接受：这次读数记为新基线，以后按“可用内存不低于 3.5GB、持续采样没有换出”判断，不动任何容器。

## 目标与反目标

目标：

1. NAS运行RocketMQ 5.5.0（namesrv、broker）与dashboard 2.1.0，镜像按摘要固定；broker开认证，dashboard开登录；数据放Docker命名卷。
2. utils主制品加RocketMQ消息模块：发送工具（普通、带tag与key、任意时长延时、顺序、事务提交后发送）、收发两端的上下文钩子、泳道隔离；用法写进utils工具目录与AI编码文档，AI写业务时照着用。
3. 第一条业务“新建账号发欢迎提醒”上线，链路脚本能验证。
4. 删除RabbitMQ（先备份）以及各仓库文档里的RabbitMQ过时描述。
5. 各仓库精确提交CI通过，运行态整体验证通过，删除第3阶段测试库，归档Wiki。

反目标（即使能跑也算失败）：

- 不收发消息的服务因为带上rocketmq-spring而创建生产者或监听容器、连接RocketMQ，或启动与健康检查受影响。
- 灰度实例发出的消息被稳定实例或别的灰度标签的实例消费；消费端写错数据源、租户或操作人。
- 消费线程或发送线程带着别的请求的用户、租户上下文写库。
- 凭据、真实NAS地址进入Git、Wiki或日志。
- 业务代码直接用RocketMQ原生客户端（`DefaultMQProducer`、`DefaultMQPushConsumer`）收发，绕过上下文与泳道。

## 判定口径

- 主流做法：对照多个主流项目，以多数为准，不只看芋道。收发接口按多数用rocketmq-spring；用法以rocketmq-spring官方文档、示例与芋道的写法为准。
- 版本：只用官方发布的制品与镜像，版本写死、镜像按摘要固定。组件版本以Spring Cloud Alibaba 2025.1.0.0的依赖管理为准，现有Nacos客户端3.3.0的强制对齐不变；RocketMQ客户端锁5.5.1，与服务端同一版本线。
- 安全：沿用现有边界——中间件不直接全网暴露，管理台要登录，凭据只在私有env与Nacos。

## 依据

以下为2026-11-20读取的点时值。

- rocketmq-spring 2.3.6（2026-06-01，当前最新）：
  - 父POM写的是Spring Boot 2.7.18、Java 1.8，没有声明支持Boot 4；GitHub没有Boot 4兼容问题的记录。
  - 制品只用`AutoConfiguration.imports`登记自动配置，只引用Boot的`ConditionalOnBean`与`ConditionalOnMissingBean`，不用javax注解，依赖Jackson 2（`jackson-databind`）。
  - 生产者在配了`rocketmq.name-server`与`rocketmq.producer.group`时才创建，`RocketMQTemplate`依赖生产者；监听容器只为带`@RocketMQMessageListener`的Bean创建。
  - `RocketMQTemplate`：同步、异步、单向、顺序（`syncSendOrderly`）、按秒或毫秒延时（`syncSendDelayTimeSeconds`/`Mills`，RocketMQ 5的定时消息）、定时投递（`syncSendDeliverTimeMills`）、事务、请求应答；目的地写`主题:tag`；发送时`KEYS`等单独处理，其余不与系统保留名冲突的消息头写成RocketMQ用户属性。
  - `@RocketMQMessageListener`：`topic`、`consumerGroup`、`selectorType`（TAG或SQL92）、`selectorExpression`、`consumeMode`（并发或顺序）、`messageModel`、`maxReconsumeTimes`等，值可用`${...}`；`accessKey`、`secretKey`、`nameServer`缺省取`rocketmq.consumer.*`与`rocketmq.name-server`。
  - 监听容器`DefaultRocketMQListenerContainer`可改消费组与订阅表达式，能取到原生消费者。
- rocketmq-client：5.3.1、5.3.2的`rocketmq-common`依赖fastjson 1.x与fastjson2；5.5.1只依赖fastjson2。5.5.1的`vipChannelEnabled`缺省false。
- Spring Cloud Alibaba 2025.1.0.0（当前最新）：BOM里`rocketmq.version` 5.3.1、`nacos.client.version` 3.1.1（fleet强制为3.3.0）。`spring-cloud-starter-stream-rocketmq` 2025.1.0.0依赖`spring-cloud-stream` 5.0.0与`rocketmq-client` 5.3.1；源码里只支持18个延时等级、不开放消费端钩子。Spring Cloud Stream 5.0.4的`spring.cloud.stream.function.autodetect`缺省true。
- RocketMQ：5.5.1（2026-08-20）是维护版，fastjson2升到2.0.63修安全问题、发行包移除fastjson 1.x；5.5.0（2026-04-10）新增Lite Mode。Docker Hub官方`apache/rocketmq`最新5.5.0（amd64、arm64），还没有5.5.1。
- rocketmq-dashboard：3.0.0（2026-10-29）改名RocketMQ Studio，主打AI对话控制台、MCP接入与多云实例管理，Docker Hub没有官方镜像。官方镜像`apacherocketmq/rocketmq-dashboard`最新2.1.0（2025-08-28，amd64、arm64）；2.1.0默认端口8082，配置项有`namesrvAddrs`、`isVIPChannel`（默认true）、`loginRequired`、`authMode`（file或acl）、`accessKey`/`secretKey`。
- 主流项目（2026-11-20读源码）：
  - 芋道yudao-cloud（GitHub 1.98万星）：master为Spring Boot 2.7.18加rocketmq-spring 2.3.6。`TenantRocketMQInitializer`是Bean后置处理器，给`RocketMQTemplate`的生产者注册`TenantRocketMQSendMessageHook`（发送前把租户写进用户属性），给每个`DefaultRocketMQListenerContainer`的消费者注册`TenantRocketMQConsumeMessageHook`（消费前设置租户、消费后清理，只认单条消息）；各业务模块按`mq/message`、`mq/producer`、`mq/consumer`分包。
  - mall4cloud（6.2千星）：公共模块`mall4cloud-common-rocketmq`（`RocketMqAdapter`、`RocketMqConstant`）基于`RocketMQTemplate`；部署文档带namesrv与broker的compose。
  - lilishop（4.4千星）：按业务域的tag枚举（`OrderTagsEnum`等）与延时触发器，基于`RocketMQTemplate`。
  - RuoYi-Cloud-Plus（GitHub 1.3千星，主仓在Gitee）：示例模块用`RocketMQTemplate`演示普通、带tag、延时与事务消息。
  - Spring Cloud Alibaba官方示例（2.9万星）：基于Stream binder的普通收发、广播、延时等级、顺序、SQL过滤、事务。
  - 结论：4家直接用rocketmq-spring，是多数。
- NAS现场（11:21只读）：
  - x86_64、4核，负载约1.2–1.5；内存15.7GB，可用6.16GB、空闲1.36GB；交换区10.9GB已用3.08GB；`vm.swappiness=1`、`vm.max_map_count=1048576`。
  - Docker 27.3.1、Compose 2.29.7、cgroup v2。Docker根目录在btrfs上；编排目录在zfsv3的FUSE文件系统上，该存储2026-11-17断开过一次挂载，导致全部容器被重启。
  - 运行中的容器合计约7.16GB，最多的是Elasticsearch 1.66GB、Nacos 0.94GB（无上限、换出532MB）、Neo4j 0.80GB。
  - 端口：8080、18080已被占用；9876、10909、10911、10912、8081、8082空闲。
  - RabbitMQ：`rabbitmq:3.11-management`，没有连接；vhost `/`下两个空队列（`userInsuranceMsgQ`、`test`），`/github-dev`下没有队列；数据目录（绑定挂载）180KB。
- 代码（只读）：13个仓库没有任何收发消息的代码；RabbitMQ只出现在README、AGENTS、AI编码文档与`.github/copilot-instructions.md`里；Nacos github-dev 11-20的快照里没有rabbit字样（`rabbitmq.yaml`第2阶段已退役）。

## 方案比较

1. **收发接口**
   - 采用：rocketmq-spring 2.3.6（`rocketmq-spring-boot-starter`；发送用`RocketMQTemplate`，消费用`@RocketMQMessageListener`），即主流项目多数的写法；`rocketmq-client`锁5.5.1。2.3.6没有声明支持Boot 4，按上面的制品核对风险低；实施时第一个utils任务就在Boot 4.0.9上实测，不行就停下报告。
   - 未采用：Spring Cloud Stream加SCA binder（11-13原定，用户13:32同意不用）。只接一种队列，换队列的好处用不上；binder只有18个延时等级、没有消费端钩子；放进utils主制品会给所有服务带上Spring Integration与函数自动绑定。
   - 未采用：RocketMQ 5的gRPC客户端加Proxy。官方新客户端，主流项目用得少，NAS还要多跑一个Proxy。
2. **模块放哪**
   - 采用：utils主制品（用户选方案二）。rocketmq-spring以`api`依赖传给所有依赖utils的服务；没配`rocketmq.*`的服务不创建生产者与监听容器，不连RocketMQ。ai等服务以后只加配置就能用。
   - 未采用：utils子模块，只让收发消息的服务依赖（用户没选）。
   - 未采用：各服务自己接rocketmq-spring。与「utils加消息模块」不符，上下文与泳道要各写一遍。
3. **可靠性**
   - 采用：事务提交后再发送（用户选）。发送失败按客户端自带的重试，仍失败就记一条错误日志，只记目的地、消息key与事件ID，不记正文。broker文件保留48小时，消费方停机超过48小时的消息也会丢，同样接受。
   - 未采用：RocketMQ事务消息（半消息加回查）、发件箱表。等有业务要求不丢时再加。
4. **泳道隔离**
   - 采用：消费组按泳道分开，broker按消息属性过滤（阿里MSE消息灰度的做法；rocketmq-spring支持SQL92选择器）。发送端把**发送方实例自己的**泳道与灰度标签写进消息属性，不取请求头（请求的泳道来自`X-Traffic-Lane`头、缺省为stable，与实例泳道不一定一致）。稳定实例只收不带泳道或泳道为stable的消息；其他泳道的实例只收本泳道、本标签的消息，消费组名带泳道与标签后缀。没有对应的消费者时，消息留在broker，不回落到稳定实例，与现有的严格灰度一致。
   - 未采用：每条泳道一套主题（主题数翻倍，发送端要按泳道换目的地）；不隔离（灰度实例发出的消息会被稳定版本的代码处理）。
5. **上下文传递**
   - 采用：做法同芋道。一个Bean后置处理器给`RocketMQTemplate`的生产者注册发送钩子，给每个监听容器的消费者注册消费钩子。发送钩子在消息上没有上下文属性时，按当前线程补上租户、操作人（用户ID、部门ID）、数据源标记、本实例的泳道与标签；消费钩子在处理前按属性还原租户、操作人与数据源标记，处理后恢复原值。工具类的发送（含事务提交后发送）按调用时的上下文直接写属性，不依赖钩子。
   - 不传：灰度授权（45秒过期，消费时多半已失效）；Seata XID（消息不参与全局事务）；请求的流量治理上下文（路由按实例泳道走，不需要）。消费端还原的操作人只用于审计列，不带权限和数据范围。
6. **部署**
   - 采用：namesrv加broker，走remoting协议、不开Proxy；dashboard 2.1.0。镜像用官方5.5.0，官方发布5.5.1镜像后再换。
   - 未采用：broker内嵌Proxy（rocketmq-spring走remoting协议，用不上；NAS的8080已被占用）；dashboard 3.0（没有官方镜像，AI控制台与多云管理用不上，发布不到一个月）；自建5.5.1镜像（官方只发了二进制包，自建镜像要自己维护）。
7. **认证**
   - 采用：broker开RocketMQ 5的认证与授权（ACL 2.0）。user、message各一个账号，只授权自己用的主题与消费组；管理账号只给dashboard与初始化任务；dashboard开登录。
   - 未采用：不开认证。多数开发环境的compose不开，但NAS经多种网络可达，不符合“中间件不直接全网暴露”。
8. **主题与消费组**
   - 采用：broker关闭自动建主题，主题、账号与权限由一次性初始化任务幂等创建（与现有init任务同一做法）；消费组保留RocketMQ默认的自动创建，因为灰度消费组名随实例的泳道与标签变化，权限按前缀授予。
   - 未采用：主题也自动创建（拼错名字会悄悄建出新主题）。
9. **数据存储**
   - 采用：Docker命名卷（btrfs）。
   - 未采用：绑定到编排目录（zfsv3的FUSE会放大写入，11-17断过挂载）。
10. **事件契约**
    - 采用：事件类放rpc-api。契约只在rpc-api演进，生产方与消费方不复制契约源码。
11. **重复投递**
    - 采用：站内信表加来源事件ID列与唯一索引，同一事件重复投递时插入冲突即视为已处理（RocketMQ只保证至少投递一次）。
    - 未采用：Redis去重（多一个依赖，过期后仍可能重复）。

## 现状（2026-11-20核对）

- 版本：Spring Boot 4.0.9、Spring Cloud 2025.1.4、Spring Cloud Alibaba 2025.1.0.0、Dubbo 3.3.7、Nacos客户端3.3.0（utils强制）、JDK 21。utils 1.7.1、rpc-api 1.0.9、user 1.4.4、message 1.2.9；依赖utils的另有ai 1.8.1、ai-memory 1.2.1、rag 1.3.4、mcp 1.2.9、report 1.2.8；gateway、ai-agent不依赖utils。
- 消息：没有任何服务收发消息。utils第2阶段已删`RabbitMqConfig`与`spring-boot-starter-amqp`。
- 建号：`AuthUserServiceImpl.save`（`@Transactional`）是唯一插入用户的地方，只由`POST /auth/users`调用；同名用户已存在时直接返回旧ID。方法内设置租户上下文、在finally里清理，事务在方法返回后才提交，所以提交后的回调里已经没有租户上下文。用户有`username`、`nickname`、`deptId`等字段。
- 站内信：表`message_user_message`（V1–V5）；`send`同步入库即视为已发送；类型有`SYSTEM_NOTICE`、`NORMAL_MESSAGE`；admin-web在用列表、未读数、已读接口，没有删除接口。
- 上下文：Dubbo过滤器透传数据源标记（`DynamicSourceTtl`）、租户、Seata XID、流量治理上下文（版本、泳道、标签、权重）与灰度授权。HTTP请求不设数据源标记，实例默认走master；只有Dubbo调用带了数据源标记时，提供方才切换。HTTP入口由`ReqInterceptor`建流量治理上下文：泳道取`X-Traffic-Lane`头（缺省stable），灰度标签取灰度授权里的标签。实例泳道是属性`traffic.governance.instance.lane`：`local-canary.yaml`设为canary，各服务dev profile以它写Nacos注册元数据`traffic.lane`（缺省canary）；本机灰度标签写在元数据`canary.tag`（取`local-canary.tag`，缺省`kellen-local`）。本机IDEA实例都注册在灰度泳道。
- 审计：utils填充器从`UserContextHolder`取用户ID与部门；没有登录用户时不填。
- Nacos：`reuse-configuration.yaml`按中间件放`custom.infra-*-host`与`custom.infra-*-port`；服务的Spring配置在`{服务}-spring.yaml`；一棵配置前缀树只放一个dataId。
- 观测：链路靠OTel Java agent，库只读当前span；IDEA日常不挂agent。

## 改动设计

### NAS（改编排前备份）

1. 新增服务，沿用`orb-`前缀：
   - `orb-rocketmq-namesrv`：`apache/rocketmq:5.5.0`按摘要固定，`mqnamesrv`，堆256MB，容器上限512MB，端口9876。
   - `orb-rocketmq-broker`：同一镜像，`mqbroker -c <broker.conf>`，堆512MB、直接内存256MB，容器上限1.5GB，端口10911。不开放10909：客户端缺省不走VIP通道，dashboard也关掉。broker.conf要点：
     - 单主：`brokerRole=ASYNC_MASTER`、`flushDiskType=ASYNC_FLUSH`；
     - `brokerIP1`取客户端访问NAS用的地址，私有值，与现有中间件同一来源；
     - `autoCreateTopicEnable=false`；`enablePropertyFilter=true`（泳道SQL过滤要用）；
     - 文件保留48小时；开ACL 2.0的认证与授权；
     - 存储与日志用命名卷。
   - `orb-rocketmq-dashboard`：`apacherocketmq/rocketmq-dashboard:2.1.0`按摘要固定，堆256MB，容器上限768MB；`isVIPChannel=false`、`loginRequired=true`（用户文件放命名卷，口令私有）、ACL用管理账号；宿主端口实施时选空闲的。
   - `orb-rocketmq-init`：一次性任务，退出0为成功。broker就绪后用`mqadmin`幂等创建主题（含utils真库测试用的主题）、账号与权限。
2. broker.conf、dashboard配置与用户文件按现有编排的内联配置做法写入命名卷，不读编排目录；编排里带sha256校验的内联配置同步更新摘要。
3. RabbitMQ：先把数据目录打包、导出definitions、另存编排原文到`backups/rabbitmq-retire-<日期>/`（0700，附SHA256SUMS）并读回核对；再删除容器、编排里的服务段、数据目录与镜像。备份保留到用户确认不再回退。
4. 内存：新增3个常驻容器的上限合计约2.8GB，预计实际占用1.5–2GB。上线后只读复核可用内存与交换区，不动其他容器。

### 命名规则

- 主题：`github-dev-<生产方>-<事件>`，如`github-dev-user-account-created`；环境前缀与`GITHUB_DEV_DUBBO`、`github_dev_rag`同一思路。
- 消费组：`github-dev-<消费方>-<事件>`；非稳定实例由模块自动加`-<泳道>-<标签>`后缀（没有标签时只加泳道）。生产者组：`github-dev-<服务>-producer`。
- 主题与消费组写在各服务的Nacos配置里，代码里的`@RocketMQMessageListener`与发送目的地用`${...}`引用。
- 账号：`github-dev-<服务>`。

### utils（1.7.1 → 1.7.2，一次发版；推送前按远端顺延）

1. **依赖**：`api 'org.apache.rocketmq:rocketmq-spring-boot-starter:2.3.6'`；`org.apache.rocketmq`下的客户端模块（`rocketmq-client`、`rocketmq-acl`及它们传递的`rocketmq-*`，`rocketmq-spring-*`除外）用`resolutionStrategy`锁5.5.1，写法同现有的Nacos客户端锁定。用`dependencyInsight`确认Netty、Jackson等与现有版本一致、没有冲突。
2. **发送工具**（`MqTemplate`，底层是`RocketMQTemplate`，方法形状参考它）：
   - 同步发送：`send(主题, 负载)`。可选项：tag（拼成`主题:tag`）、key（可多个）、延时（`Duration`，按秒用`syncSendDelayTimeSeconds`，上限按broker缺省3天）、顺序键（`syncSendOrderly`，同一顺序键进同一个队列）。发送失败抛出模块自己的异常。
   - 事务提交后发送：调用时立刻抓取上下文快照（租户、操作人、数据源标记），提交后带着快照发送；回滚不发；没有事务时立即发。提交后发送失败只记错误日志（目的地、key、事件ID），不抛出。
   - 写入的消息属性：租户、操作人的用户ID与部门ID、数据源标记（有才写），以及本实例的泳道与灰度标签。属性名统一用一个前缀（如`fleet_`），可以直接用在SQL过滤里。
3. **收发钩子**（照芋道的`TenantRocketMQInitializer`）：Bean后置处理器给`RocketMQTemplate`的生产者注册发送钩子，消息上没有上下文属性时按当前线程补上（业务直接用`RocketMQTemplate`同步发送时也带上下文）；给每个监听容器的消费者注册消费钩子：处理前还原租户（`TenantContextHolder`）、操作人（只含用户ID、租户、部门的`SecurityUser`）与数据源标记，处理后恢复原值。只支持单条消费（与芋道一致）。需要按数据权限查询的消费逻辑要自己处理权限，文档写明。
4. **泳道隔离**：同一个后置处理器读本实例的泳道（Nacos注册元数据`traffic.lane`，缺省stable）与灰度标签（元数据`canary.tag`），处理每个监听容器：
   - 稳定实例：消费组名不变，订阅`fleet_lane IS NULL OR fleet_lane = 'stable'`；
   - 其他泳道：消费组名加后缀，订阅本泳道且本标签（实例没有标签时只按泳道）；
   - 容器原有tag订阅时合并成同一条SQL；消费组在容器初始化前改，订阅在消费者创建后、启动前重新设置。
5. **约定**（写进文档）：消息类放`mq/message`，发送方放`mq/producer`，消费方放`mq/consumer`（芋道的分包）；跨服务的事件类放rpc-api；按上面的命名规则命名；消费必须幂等；消费逻辑不调需要灰度授权的RPC；业务不直接用RocketMQ原生客户端。
6. **测试**：
   - CI里的单元测试：发送工具（模拟`RocketMQTemplate`，核对目的地、属性与调用的方法；提交才发、回滚不发、无事务立即发；提交后失败不抛出）；两个钩子（上下文写入、还原与恢复，异常时也恢复）；后置处理器（各泳道的消费组与订阅表达式，重复处理不叠加）；没配`rocketmq.*`时不创建生产者与监听容器；Boot 4.0.9上下文里rocketmq-spring自动配置能正常装配。
   - 真库测试（NAS的RocketMQ；地址与测试账号来自本机私有env，CI里跳过，与第3阶段`FLEET_PG_TEST_URL`同一做法）：收发与上下文还原、泳道SQL过滤、延时、顺序、错误口令被拒。
7. **文档**：README、`UTILS_TOOL_CATALOG.md`、AI编码文档新增消息模块用法（配置模板、发送、消费、泳道、幂等、禁止事项）；`TESTING_SPEC.md`等处的RabbitMQ改为RocketMQ。
8. **第3阶段推迟的小问题（用户选三条都修）**：
   - 运维模板`security-auth.yaml`的注释与`test_postgresql_configuration.py`的文档串把默认本人列改为`create_by`，只改注释，值不变。
   - `UserContextHolder`、`TenantContextHolder`不再向新建线程继承（TTL的`childValue`返回null；线程池传递仍靠`TtlRunnable`、`TtlExecutors`）。先写会失败的测试再改；同时核对现有代码里哪些地方依赖新建线程继承上下文（`new Thread`、`CompletableFuture`默认线程池、Reactor线程等），改为显式传递，例如ai在第3阶段已改为显式绑定的`AiRequestIdentity`。
   - 7个服务AGENTS里的utils版本随本阶段升级改为新版本。

### rpc-api（1.0.9 → 1.1.0，推送前按远端顺延）

- 新增事件`UserAccountCreatedEvent`，放`com.kellen.rpc.user`下的事件子包：事件ID、发生时间、租户ID、用户ID、用户名、昵称。不含密码与权限信息。

### user（1.4.4 → 1.4.5）

- 升级utils与rpc-api。
- `AuthUserServiceImpl.save`真正插入新用户后，经`mq/producer`下的发送类以“事务提交后发送”发出事件：key为事件ID，租户取入参的`tenantId`。同名用户已存在时不发。
- 生产者随`RocketMQTemplate`在启动时创建。
- 测试：新建账号提交后发一条且属性正确；同名账号不发。

### message（1.2.9 → 1.3.0）

- 升级utils与rpc-api。
- `mq/consumer`下的`UserAccountCreatedConsumer`（`@RocketMQMessageListener`，主题与消费组取Nacos配置，重投3次后进死信）给新账号写一条站内信：类型`SYSTEM_NOTICE`，发件人`system`，标题“欢迎使用”，正文带称呼（有昵称用昵称，否则用用户名），租户取自事件，发送状态`SENT`、未读；创建人与创建部门由填充器按还原的操作人写入。文案以后可改。
- Flyway `V6`：`message_user_message`加可空的`source_event_id`与唯一索引（来源事件ID、接收人，来源事件ID非空时生效）。重复投递时唯一冲突视为已处理。
- 测试：消费后入库一条且字段正确；同一事件两次只入一条；负载多一个未知字段时照常消费。

### 其余依赖utils的服务（ai、ai-memory、rag、mcp、report，各升一次版本）

- 只升级utils，不加收发和配置。验证启动后不创建RocketMQ客户端、健康检查UP、原有定时任务照常执行。

### 各仓库文档

- 把README、AGENTS、AI编码文档、`.github/copilot-instructions.md`里的RabbitMQ描述改为RocketMQ或删掉，例如message的「RabbitMQ消息生产与消费」与`## RabbitMQ`一节、ai-memory的「RabbitMQ异步索引」、user README的`infra-rabbitmq-addr`示例、utils运维README的`/github-dev` vhost。随所在仓库的提交改，不单独升版本；ai-agent只改文档。

### Nacos（github-dev）

- `reuse-configuration.yaml`：新增`custom.infra-rocketmq-host`、`custom.infra-rocketmq-port`，值来自私有dev.env。
- `user-spring.yaml`：`rocketmq.name-server`（引用上面两个键）、`rocketmq.producer.group`、`rocketmq.producer.access-key`/`secret-key`；`fleet.mq`下的主题。
- `message-spring.yaml`：`rocketmq.name-server`、`rocketmq.consumer.access-key`/`secret-key`；`fleet.mq`下的主题与消费组。
- 逐行注释；账号口令来自私有dev.env。不新建共享的`rocketmq.yaml`：两个服务的账号不同，且一棵配置前缀树只放一个dataId。
- `security-auth.yaml`：只改注释（默认本人列`create_by`），值不变。
- 发布沿用现有步骤：只读盘点并备份、保值渲染、逐键比对、CAS发布、读回、同步目录。

### 运维与验证工具

- `~/.config/kellen-github/tools`：链路脚本增加消息步骤；新增RocketMQ只读查询脚本（经`qw-nas`在容器里执行`mqadmin`，查主题、消费组进度与堆积）。
- 私有dev.env新增RocketMQ相关变量；README只写变量名，不写值。

## 验证

1. **先实测RocketMQ，不碰业务**：NAS起RocketMQ后，用utils真库测试验证收发、泳道过滤、延时、顺序与认证；dashboard能登录，能看到主题与消费组。
2. **utils**：全套测试、javadoc、依赖树（rocketmq-spring 2.3.6、rocketmq-client 5.5.1都在，Netty、Jackson等没有冲突）、私有Maven制品读回。
3. **各服务**：本机测试通过，精确提交的CI通过。
4. **运行态**：用户在IDEA起9个服务。
   - 两条全链路（带、不带灰度授权）照常通过。
   - 消息步骤：链路脚本新建测试账号，限定时间内该账号出现一条欢迎提醒，租户、接收人、创建人（登录用户）、创建部门正确；随后清理账号与提醒。
   - 这条消息只被灰度消费组（带泳道与标签后缀）消费，堆积为0；本机没有稳定实例，不应出现稳定泳道的消费组。
   - ai、ai-memory、rag、mcp、report没有RocketMQ连接，健康检查UP，定时任务照常。
   - 同一条消息再投递一次，仍只有一条提醒。
5. **NAS**：只读复核内存与交换区；RabbitMQ已删除，备份可读。
6. **收尾**：删除第3阶段测试库`fleet_pg_test_phase3`、同名角色与本机私有env；归档Wiki。

## 回退

- 服务与utils：回到上一版本（utils 1.7.1、user 1.4.4、message 1.2.9等）。message的V6只加可空列与索引，旧版本可以直接使用。
- Nacos：按发布前的备份恢复。
- NAS：恢复编排备份，删除RocketMQ的容器与卷；需要RabbitMQ时按备份恢复编排、数据目录与definitions。

## 不在本阶段

- 事务消息、发件箱：等有业务要求不丢时再加（rocketmq-spring自带事务消息，到时直接用）。
- RocketMQ 5的gRPC客户端、Proxy与Lite Topic：rocketmq-spring走remoting协议；ai以后要用Lite Topic时另议。
- broker指标接Prometheus与Grafana：先用dashboard看堆积，另行安排。
- 多broker、主从、DLedger。
- ai等其他服务的收发业务。
- 熔断限流、镜像交付与k8s、容器化集成测试、环境隔离（6个阶段之外的缺口）。
- 美联项目。

## 留给实施确定的细节

- 镜像摘要；5.5.0镜像的启动命令与JVM参数写法；ACL 2.0在5.5.0里的确切配置项与`mqadmin`命令；dashboard 2.1.0镜像的配置挂载方式。
- 容器里（dashboard、初始化任务）能否经`brokerIP1`连到broker；连不上就改用宿主网络或调整地址。
- 后置处理器改消费组与订阅的确切时机（容器初始化前后），要求启动前完成、重复处理不叠加。
- 属性的具体名字、主题的队列数、重试次数与死信处理。
- 消息轨迹：rocketmq-spring缺省开启；broker开轨迹主题并给账号授权，或者关闭。
- 链路脚本查提醒与清理的方式（站内信没有删除接口，可能要直接删库里的测试行）。
- 各仓库的版本号在推送时按远端顺延；fleet按VERSIONING_SPEC在补丁位到9时进位（user 1.3.9→1.4.0、utils 1.6.9→1.7.0）。
- 真库测试的组织方式。
