# 第2阶段utils改造设计

## 已确认目标

用户2026-11-13定：个人GitHub微服务按6个阶段升级（底座升级、utils改造、Flyway、RocketMQ、Keycloak、前端），兼容性优先，每一步验证通过再进入下一步。第1阶段已于2026-11-14完成并收尾，本文只覆盖第2阶段。第1阶段设计原把MinIO SDK 9、iText 9、POI 6及删除orika、dozer、javax.mail放到本阶段；本阶段在此基础上做utils瘦身。

用户2026-11-14逐项确认（新加坡时间）：

- 16:10「先留 8.6.0」：MinIO本阶段不升级，原因见「现状」。
- 16:13「按建议分」：第1阶段推迟项中，Redis缓存序列化器、utils的javadoc报错、7个服务改用toolchain并入本阶段；ai存储表时区类型放到第3阶段与Flyway一起做；Docker镜像构建验证另做。
- 16:15「一次发版」：utils只发一次版。
- 16:18「TreeSet这个pdf处理，还需要吗？……另外pdf都是中间件在处理」：删除`PdfUtils`与iText，不再升级iText；`GeneralConvertor`的`Set`重载无人调用，一并删除。
- 16:20「检查工具类里那些过时了，不是非主流的，没有在用了，就应该删了。别保留了」「当需求需要在可以按主流和github的关注度，在加对应的工具」：盘点utils，删掉过时、小众、没有在用的工具；以后有需求再按主流程度和GitHub关注度选库。
- 16:30「分布事物都要用的啊」：Seata保留。
- 20:16「mybatis-mate 需要保留。OkHttpUtils不好吗？切换数据源有主流的了？hutool-all需要的吧。」：mybatis-mate、hutool-all、`OkHttpUtils`、数据源切换工具保留，`OkHttpUtils`修掉不安全的写法。
- 20:20「按这版改写」：按下文清单执行。
- 20:36「我记得有关Redis的锁，不要影响了，可以优化。采用主流的工具类方式」：Redis分布式锁与防重复提交保持行为不变，按Redisson的主流用法优化锁工具类。
- 20:54「按建议写进」：统一返回对象`ApiResponse`、统一异常处理、错误码`ReturnCode`按下文改；`EntityBase`本阶段只改两处接口文档说明，字段与数据库列改名放到第3阶段与Flyway一起做。
- 21:13「utils所有工具也都检查修改一下，要按主流使用方法以github关注度最高的项目做参考」：保留的77个类逐个对照同类里GitHub上最热门项目的用法核对（Hutool、OkHttp、Redisson、MyBatis-Plus、Spring Boot、芋道ruoyi-vue-pro、若依），结果见「主流用法核对」。
- 21:27「按建议写进」：按「主流用法核对」改。由此改判三项：防重复提交从未生效，整条删除（不再是「行为不变」）；`ServiceShutDownEndpoint`、`DataSourceUtil`由保留改为删除；Jedis随Redis连接改由Spring Boot自动配置而删除。

范围只有个人GitHub项目，美联项目不动（本次只读参考过美联kellen项目：PDF、钉钉、短信、直播、工作流、防重复提交注解、请求日志注解、RabbitMQ与Spring缓存在那边同样无人使用）。

为什么现在做：第1阶段定下了Java 21与Spring Boot 4.0.9等基线，后面的Flyway、RocketMQ、Keycloak都建立在utils上；utils会被每个服务组件扫描，空转的配置和依赖在每个服务里都生效并拖累升级，先清掉再往下走。

## 判定口径

- 删除：过时、小众、已被其他方案替代、只是占位，且没有在用的类与依赖。
- 保留：通用、主流、以后大概率会用的工具，即使暂时没有调用方。
- 「在用」的判断：从7个服务的源码引用与utils资源文件（Dubbo SPI、自动配置清单）中登记的类出发，加上真正发挥作用的Spring组件，沿utils内部引用能连到的类都算在用。各服务启动类都在`com.kellen`包下，utils里的配置类会被扫描进每个服务，所以不能只看有没有import。
- 只有个别服务用到的依赖，由该服务自己声明，不放在utils。
- 以后有新需求，再按主流程度与GitHub关注度选库加入。
- 保留的工具，写法与命名对照同类里GitHub关注度最高的项目的主流用法；能直接用这些库现成能力的，不自己实现。

## 方案比较

- 采用：一次发版。utils每项改动各一个提交、各带测试，按顺序做，每个提交的CI通过再做下一个，最后发布一次utils 1.6.8；再逐个服务升级并验证。只发一次版，符合「同批只升一次」；各项影响的服务各不相同，出问题能定位到具体一项；某个服务有问题可以暂时停在1.6.7。代价是回退时整版一起退。
- 未采用：两次发版（先发不改变行为的删除与替换，再发POI 6）。风险更集中，但服务要升两轮，提交和CI翻倍。
- 未采用：每项一次发版。改动之间互不依赖，没有必要让服务升多轮。
- MinIO未采用：升到9.0.4并在utils里自写MinIO适配器，或整体去掉X File Storage。前者要自己实现上传、下载、删除、复制、列举、签名并重跑统一存储验收；后者等于重写已验收的统一存储，违背兼容优先。

## 现状

核对时间2026-11-14，依据为各仓库源码、utils `build.gradle`、Maven Central元数据，以及只读的引用分析。

utils根项目主代码112个类：先按「在用」口径删除35个；21:13起再按主流用法核对保留的77个，又删除16个（见「主流用法核对」），最终保留61个，另新增两个自动配置类。`traffic-canary`、`fleet-observability`两个子项目是近期新建且在用的独立制品，不在本次盘点范围。

### 删除的类（35个）

| 一组 | 类 | 理由 |
|---|---|---|
| 钉钉机器人（10） | `aliyun.DingDing`；`aliyun.dingding`的`AtMobiles`、`DingDingUtil`、`SendRebootUtil`；`markdown`的`CustomMarkDown`、`MarkDownModel`、`MarkDownRebootModel`、`SimpleMarkdownBuilder`；`text`的`ContentModel`、`TextRebootModel` | 小众厂商集成，无人使用；告警已由Grafana负责 |
| 阿里云短信（3） | `aliyun.Sms`、`aliyun.sms.SmsUtils`、`utils.enumeration.SmsEnum` | 基于过时的aliyun-java-sdk-core 4.x，无人使用；以后需要再按官方新版SDK接入 |
| 阿里云直播（2） | `aliyun.LiveStreaming`、`aliyun.live.CreateLiveUtils` | 小众，无人使用 |
| 阿里云函数工作流（2） | `aliyun.workflow.WorkflowRegion`、`WorkflowUtils` | 小众，已由Conductor替代 |
| OSS旧工具（1） | `aliyun.oss.OssUtils` | 已被统一存储替代；仍在用的`aliyun.oss.token`保留 |
| 已无对应功能的配置（2） | `config.aliyun.CommonAliyunProperties`、`config.wechat.CommonWechatProperties` | 短信、直播、微信小程序功能已不存在 |
| PDF（1） | `utils.file.PdfUtils` | PDF解析由rag引擎与OCR完成，渲染转换由Gotenberg完成 |
| 请求日志写ES与配套参数检查（11） | `aop.RequestRequiredAspect`、`utils.annotations.RequestRequired`、`config.elasticsearch.ElasticsearchConfig`、`log.entity.ElasticSearchRequestLog`、`log.entity.RequestLog`、`log.mapper.ElasticSearchRequestLogMapper`、`log.service.ElasticSearchRequestLogService`、`log.service.impl.ElasticSearchRequestLogServiceImpl`、`utils.http.RequestUtil`、`utils.methods.MethodsBean`、`utils.methods.MethodsJudge` | 切面只作用于标了`@RequestRequired`的类，服务里没有，从未生效；日志已走OTel进ES，参数校验已用Bean Validation |
| RabbitMQ配置（1） | `config.rabbit.RabbitMqConfig` | 各服务无RabbitMQ收发；第4阶段换RocketMQ |
| 停用拦截器（1） | `config.actuator.ActuatorInterceptor` | 注释已写明是停用的历史拦截器，Actuator由独立安全链保护 |
| 空标记注解（1） | `utils.annotations.DynamicDataSource` | 没有属性、没有代码读取，与dynamic-datasource自带的`@DS`重复 |

### 保留但暂无调用方的通用工具

`IpUtils`、`JsonUtil`、`DynamicSourceTtl`（在dynamic-datasource之上加TTL透传，供灰度库切换）、`Phone`与`PhoneValidator`、`AsyncConfig`与`TraceContextTaskDecorator`、`RedisCacheConfig`、`OkHttpUtils`与`HttpType`、`HttpWay`，以及Seata集成（`DubboContextPropagationFilter`、`DynamicDataSourceConfig`）。其中多项按「主流用法核对」改写。原列在这里的`DataSourceUtil`、防重复提交一组、`ServiceShutDownEndpoint`、mybatis-mate的`SensitiveStrategyConfig`改为删除。

### 依赖处理

| 处理 | 依赖 |
|---|---|
| 删除 | `spring-boot-starter-amqp`、`spring-boot-starter-data-elasticsearch`、`aliyun-java-sdk-core`的显式声明（`aliyun-sdk-oss` 3.18.5仍以同一版本4.7.8传递引入）、`aliyun-java-sdk-fnf`、`itext7-core`、`font-asian`、`pdfbox`、`dozer-core`（替换后）、`aviator`、`javax.mail:mail`、`orika-core`、`commons-collections4`、`guava`（`RedisCacheConfig`唯一一处`Maps.newHashMap`改JDK写法）、`redis.clients:jedis`（Redis连接改由Spring Boot与Redisson官方starter自动配置后不再需要） |
| 移到服务 | `spring-boot-starter-websocket`移到user、ai-memory、rag、message、report；`poi-ooxml`移到rag并升到6.0.0（utils自身都不使用） |
| 保留 | mybatis-mate、OkHttp、hutool-all、`redisson-spring-boot-starter`、AspectJ（utils删去最后一个切面后自身不再用，但mcp、rag有业务观测切面；移到这两个服务会改变其他服务的代理方式，需单独确认，本阶段不动）、Seata，以及其余仍在用的依赖 |
| 不变 | MinIO 8.6.0、X File Storage 2.3.0、jjwt 0.9.1（第5阶段由Keycloak取代） |

MinIO留在8.6.0的原因：X File Storage最新发布版2.3.0（2025-06-22，main分支就是这次发版）按MinIO 8.5.2编译，开发中的dev分支（2026-05-07仍有提交）也固定在8.5.2。它的MinIO适配器用到`CopySource`、`ComposeSource`等类，而MinIO 9已删除这两个类，升级后统一存储的MinIO平台会在运行时出错。8.6.0是8.x最后一个版本。

POI 6.0.0（2026-10-22发布）最低要求JDK 17。rag的`KnowledgeDocumentParser`自己解析xlsx与docx（Word用`getBodyElements()`加`paragraph.getText()`，Excel用`DataFormatter`加`FormulaEvaluator`），pdf、png、jpg交给引擎。POI 6有两处行为变化可能影响输出：`DataFormatter`对公式单元格默认改用缓存值，`XWPFParagraph.getRuns()`不再包含行内内容控件。

依赖utils的服务共7个：user、ai、ai-memory、rag、mcp、message、report。gateway、ai-agent、rpc-api不依赖utils。用`sourceCompatibility`而非toolchain的7个服务：gateway、user、ai-memory、rag、mcp、message、report。本阶段要改的服务是两份名单的并集共8个，ai-agent不动。`GeneralConvertor`删除后的调用方只有user（12个文件）与message（2个文件）。

Redis锁现状：
- utils的`utils/redisson`有`Locker`接口、基于Redisson `RLock`的`RedissonLocker`与静态门面`LockUtil`，`LockUtil`的锁对象由`ConfigClientController`创建`RedissonLocker`后设入。
- 各服务里只有mcp的`McpRegistryPublicationService`用锁，它直接用`RedissonClient`：`tryLock`最多等10秒、不传租约（Redisson看门狗续期）、解锁前检查`isHeldByCurrentThread()`。
- 防重复提交用`StringRedisTemplate.setIfAbsent`加过期时间。
- 与主流用法相比，`RedissonLocker.unlock`不检查持有者，租约到期后解锁会抛`IllegalMonitorStateException`；`LockUtil`没有「加锁、执行、必定释放」的回调式写法；防重复提交释放时先`get`再`delete`。

统一返回、异常处理与错误码现状（对照阿里手册、Ant Design Pro返回约定与若依、芋道等主流写法核对）：
- `ApiResponse`输出`success`、`code`、`msg`、`data`、`errorMessage`、`timestamp`。失败时`errorMessage`一定有值（没传就取错误码文案），`msg`与它重复；成功时`msg`只是「成功」。各服务主代码没有读`msg`的地方，admin-web按`errorMessage || msg`取提示；user的`AuthResourceControllerTest`有两处断言`msg`。
- `timestamp`是`LocalDateTime.now()`，`@JsonFormat`上的`timezone = "GMT+8"`对`LocalDateTime`不起作用，输出的是JVM所在时区的时间，本机为+8所以目前碰巧正确。
- 追踪号只在响应头`traceId`里（`ReqInterceptor`取自当前OTel链路）。`fail(...)`只能返回`ApiResponse<Void>`。
- `ApiExceptionHandler`本身不是Bean，user、message、mcp、report、ai-memory、rag各自用`@RestControllerAdvice`子类启用它。它处理12类业务与校验异常，一律HTTP 200；没有兜底，也不处理请求体JSON写错、缺参数、参数类型不对、请求方法不对、404等Spring MVC标准异常，这些情况落到Spring Boot默认错误输出，格式与统一报文不同。rag的子类自己补了JSON解析失败（400、A0400、「请求体 JSON 格式不正确」）与兜底（500、B0001、「系统处理失败，请查看服务日志」），另有403、502与图谱相关处理。ai另有自己的`com.kellen.exception.ApiExceptionHandler`，返回ai自己的`AiChatResponse`，不走utils的统一处理。
- utils以`api`引入`spring-boot-starter-security`，所有依赖utils的服务运行时都有Spring Security。各服务没有`SseEmitter`等流式接口，也没有抛`ResponseStatusException`的地方。
- `ReturnCode`共177个常量：成功码是`"0"`（阿里手册为`"00000"`），另176个按阿里手册分A、B、C三类，编码无重复。常量用中文命名，阿里手册「命名风格」明确禁止；属性叫`state`（编码）与`name`（文案），`name`与枚举自带的`name()`撞名；三个静态查找方法`getName(String)`、`getState(String)`、`getReturnCode(String)`没有调用方。引用共86处（utils 21、user 57、rag 6、ai 2，含测试），涉及20个常量。只有「无效TOKEN」的文案（「无效token，请重新登录！」）与常量名不同。
- 分页：服务直接返回MyBatis-Plus的`Page`；3.5.18.1只输出`records`、`total`、`size`、`current`、`pages`，与admin-web声明的类型一致。
- `EntityBase`：`createDateTime`、`modifyDateTime`、`createName`、`modifyName`、`isDelete`都不是主流叫法（主流为`createTime`、`updateTime`、`creator`/`createBy`、`updater`/`updateBy`，逻辑删除字段`deleted`、列`is_deleted`）；自动填充只填两个时间与租户，各服务也没设置`createName`、`modifyName`，程序写入的数据里这两列一直为空。admin-web与各VO都没用到这五个字段；`sorting`有3个VO与admin-web 18处在用。主键说明写成了「序列」，`version`在接口文档里被隐藏，而规范要求更新接口回传它。

### 主流用法核对（21:13起）

对照的项目：Hutool、OkHttp、Redisson、MyBatis-Plus、Spring Boot、芋道ruoyi-vue-pro、若依。

有缺陷，必须改：

| 工具 | 问题 | 改法（参考） |
|---|---|---|
| `DataPermissionSqlHandler` | 先拼SQL字符串再解析，解析出错时吞掉异常、不加条件，等于出错就放开全部数据；user正在用 | 直接用JSqlParser表达式对象构造条件，出错就抛出（MyBatis-Plus官方示例、芋道） |
| `AsyncConfig`与`TraceContextTaskDecorator` | 只传OTel链路，不传租户、用户等TransmittableThreadLocal上下文；线程复用时沿用创建该线程那次请求的租户，可能串租户（各服务暂无`@Async`） | 包装任务时同时套`TtlRunnable`（芋道）；线程名前缀`gbw`改为`async-` |
| `@Phone`与`PhoneValidator` | 正则`^1(3\|4\|5\|7\|8)\d{9}$`拒绝16、19开头的真实号码；注解的`message`被写死覆盖；只能标在字段上 | Hutool `PhoneUtil.isMobile`，用注解自己的提示语，支持参数、方法与容器元素 |
| `OkHttpUtils`（证书校验之外） | `send`返回未关闭的响应体，调用方不关就漏连接；不看状态码，4xx、5xx也当成功 | OkHttp官方示例：try-with-resources用完即关，非2xx抛出；删过时的浏览器标识常量，`HttpType.FROM`改`FORM` |

从未生效或无人使用，删除（16个类）：

| 工具 | 原因 |
|---|---|
| 防重复提交：`PreventRepeat`、`PreventRepeatInit`、`PreventRepeatException`、`Methods`、`MethodsInit`、`MethodsParam`、`ReflectionUtils` | 唯一驱动者是本阶段删除的`RequestRequiredAspect`，它只作用于标了`@RequestRequired`的类，服务里没有，所以从未生效；也没有服务用`@PreventRepeat`。以后需要时按芋道`@Idempotent`的切面写法加 |
| `DateException`、`ParameterNullException`、`SignException`、`SmsException`及统一异常处理里的对应方法 | 没有任何地方抛出；短信功能已删 |
| `ServiceShutDownEndpoint` | 自写的「先下线、等120秒再退出」端点，无调用方；主流做法是Spring Boot自带的优雅停机，加Spring Cloud自带的`serviceregistry`端点先下线 |
| `MultipartConfig` | 在utils里把所有服务的上传上限写死为1GB，只有rag有上传接口；改由rag自己配`spring.servlet.multipart.*`（仍为1GB），其他服务回到Spring Boot默认值 |
| `DataSourceUtil` | 删掉RabbitMQ配置后无人使用，只转发`DynamicSourceTtl` |
| `SensitiveStrategyConfig` | 只注册了演示用的`testStrategy`，没有实体使用；mybatis-mate依赖保留 |
| `ConfigClientController` | 见下表Redis连接；其中的`redisTemplateObject`用JDK序列化，没有任何地方注入 |

能用但不是主流写法，改：

| 工具 | 改法（参考） |
|---|---|
| Redis连接（`ConfigClientController`，名字像控制器，实际是配置类） | 现在手工用Jedis建连接、手工建`RedissonClient`，读自定义的`redis.*`，连接超时1分钟。改为交给Spring Boot与`redisson-spring-boot-starter`按`spring.data.redis.*`自动配置，只保留锁工具的装配（新的自动配置类）。Nacos共享的`redis.yaml`已有`spring.data.redis.*`（由`redis.*`引用而来），Nacos不用改 |
| `JsonUtil` | 方法名`bean`、`list`、`json`改为Hutool `JSONUtil`的`toBean`、`toList`、`toJsonStr`；改用Spring Boot配好的`JsonMapper`，不再自带一套不同的配置（芋道JsonUtils） |
| `IpUtils` | 内网判断只认`192.168.`，改用Hutool `NetUtil.isInnerIP`；客户端是本机时返回的是服务器局域网IP，改为`127.0.0.1`（若依）；方法名改为`getClientIp`、`getLocalIp`、`isInnerIp` |
| MyBatis-Plus插件顺序 | 按官方文档：租户、数据权限 → 分页、乐观锁 → SQL规范检查、防全表更新删除；现在后两个排在分页前 |
| Seata包名 | `io.seata.*`是兼容包，Apache Seata 2.x正式包名为`org.apache.seata.*`（2.6.0两套都有） |
| `DynamicSourceTtl` | 上下文字段为`public static`且可改写，改为`private static final` |
| Swagger | 删掉`dataSource`请求头的安全项：读这个头的代码随`RequestRequiredAspect`一起删除 |

核对后不改：租户与用户上下文（TTL，与芋道相同）、Dubbo上下文透传（Dubbo 3新API）、OSS与COS直传签名（各家官方文档）、统一存储（X File Storage官方用法）、请求拦截器与安全过滤器的上下文清理、Actuator令牌常量时间比较、`MyMetaObjectHandler`（创建人填充随第3阶段）。

## 改动设计

### utils（按顺序，每项一个提交，提交前本机构建与测试通过，CI通过再做下一项）

两条通用要求：删除显式依赖后若它仍被其他库传递引入，以依赖树为准，解析版本不得低于原先显式声明的版本，否则改用版本约束锁定；删除类时，一并删除或调整引用它们的测试，包括各服务测试里的残留引用（如ai测试启动类排除`RequestRequiredAspect`的扫描过滤正则）。

1. **删除钉钉与阿里云一组**：上表前六组共20个类，连同`aliyun-java-sdk-core`、`aliyun-java-sdk-fnf`。
2. **删除请求日志写ES与配套参数检查**：上表11个类，连同`spring-boot-starter-data-elasticsearch`。
3. **删除其余无用项**：`PdfUtils`（连同`itext7-core`、`font-asian`、`pdfbox`）、`RabbitMqConfig`（连同`spring-boot-starter-amqp`）、`ActuatorInterceptor`、`@DynamicDataSource`。
4. **删除无人使用的依赖**：`orika-core`、`javax.mail:mail`、`aviator`、`commons-collections4`；`RedisCacheConfig`的`Maps.newHashMap`改为JDK写法后删除`guava`。
5. **替换Dozer**：删除`Set`重载；其余四个公开方法签名与行为不变，内部改用hutool-all已有能力，然后删除`dozer-core`。必须保持：新建对象的两个方法遇到`null`源返回`null`；`List`结果为按原顺序的`ArrayList`；深拷贝，嵌套对象与集合生成新实例、不与源共享引用；拷入已有对象时，源字段为`null`与Dozer默认一致，覆盖目标字段；user与message实际转换的类型对，字段映射与类型转换结果与Dozer逐项一致。先用这些类型对写对照测试，在Dozer实现上跑通，再换实现，测试保持通过后才删Dozer。
6. **修复`OkHttpUtils`**：去掉信任所有证书的TrustManager与恒为真的主机名校验，改用JDK默认的证书与主机名校验；客户端改为线程安全的单例初始化。测试用OkHttp的MockWebServer（测试依赖）：对自签名证书的HTTPS服务请求必须失败，普通请求照常成功。以后需要访问自签名内部服务时，为该服务单独配置信任库。
7. **POI移出utils**：删除`poiVersion`与`poi-ooxml`，由rag自己声明6.0.0（见「服务改动」）。
8. **Redis缓存序列化器**：`RedisCacheConfig`改用`GenericJacksonJsonRedisSerializer`（Spring Data Redis 4.0.8，Jackson 3）。切换前只读统计NAS Redis里`cache:*`键数：预期为0，直接切换；不为0就停下来请用户决定清理这些键还是换缓存前缀。RedisTemplate的序列化本阶段不动。
9. **Redis锁（行为兼容的优化）**：`Locker`与`LockUtil`现有方法的签名与语义不变；`RedissonLocker.unlock`只在`isHeldByCurrentThread()`为真时解锁；`Locker`与`LockUtil`新增回调式的`executeWithLock`：在等待时间内`tryLock`，不传租约，由Redisson看门狗续期，在`finally`里只释放自己持有的锁，拿不到锁抛明确的异常，写法与mcp现有用法一致。mcp的锁代码不改。防重复提交见第10项，整条删除。
10. **删除从未生效与无人使用的工具**：「主流用法核对」删除表中除`ConfigClientController`外的15个类，连同统一异常处理里5个异常的处理方法与Swagger的`dataSource`安全项，调整引用它们的测试。rag自己声明上传上限（见「服务改动」）。
11. **修复数据权限与异步上下文**：`DataPermissionSqlHandler`直接返回JSqlParser表达式（本人与部门用等值条件，部门树与自定义用`IN`，列带表别名，字符串值先转义单引号），生成的条件与现在一致，内部异常不再吞掉；`TraceContextTaskDecorator`同时传递OTel上下文与TransmittableThreadLocal上下文；线程名前缀改为`async-`。
12. **工具类按主流改写**：`@Phone`、`IpUtils`、`JsonUtil`、`OkHttpUtils`的响应处理与`HttpType`、`DynamicSourceTtl`按「主流用法核对」改。这些工具在服务里都没有调用方，改名不影响服务。`JsonUtil`新增一个自动配置类，有`JsonMapper` Bean时改用它。
13. **配置按主流对齐**：删除`ConfigClientController`，Redis连接、`StringRedisTemplate`与`RedissonClient`交给Spring Boot与`redisson-spring-boot-starter`自动配置；新增锁工具的自动配置类，有`RedissonClient`时才装配`RedissonLocker`并设入`LockUtil`；删除Jedis。MyBatis-Plus插件按官方顺序；Seata改用`org.apache.seata.*`。登录鉴权与mcp的注册发布锁依赖Redis，整体验证重点验。
14. **错误码改英文命名**：177个常量按文案意译为英文大写下划线，编码与文案一个不改；属性改为`code`与`message`（`getCode()`、`getMessage()`）；删除三个无人调用的静态查找方法。改名前先把现有编码与文案导出成测试资源，测试逐项比对，保证改名不碰编码与文案。成功码保持`"0"`，不改成`"00000"`：那是对外契约变化，不在本阶段。
15. **统一返回对象**：删掉`msg`；新增`traceId`，取当前请求的OTel追踪号（与响应头同值），没有时不输出；`timestamp`固定按GMT+8取值，不再依赖JVM时区，格式不变；两个`fail(...)`改为泛型`<T>`，对现有调用源码与二进制都兼容。其余字段名与含义不变。admin-web不改：它优先读`errorMessage`，`msg`只是兜底。
16. **统一异常处理补齐**：`ApiExceptionHandler`改为继承Spring的`ResponseEntityExceptionHandler`，Spring MVC标准异常保持Spring给的HTTP状态码，报文换成统一格式（见下表）；新增兜底，未知异常回HTTP 500并在日志里记录堆栈；Spring Security的`AccessDeniedException`与`AuthenticationException`原样抛出，仍由Spring Security处理（rag自己的403处理保留）；响应已经开始输出时不再写报文。第10项删除5个异常后剩下的7类处理，HTTP 200与错误码不变，其中参数校验失败（`MethodArgumentNotValidException`）改由基类对应方法处理，结果不变。
17. **javadoc**：修完现有的doclint错误（第1阶段记录为31个），`failOnError`改为`true`。
18. **文档与发版**：utils 1.6.7 → 1.6.8（按`VERSIONING_SPEC`）；同步README、`docs/ai-coding/UTILS_TOOL_CATALOG.md`、`docs/ai-coding/PROJECT_CODING_SPEC.md`等文档，删掉已删工具的说明，更新响应字段与错误码命名规则，并把「判定口径」里的长期规则写进utils的AI编码规范；`EntityBase`改两处接口文档说明：主键由「序列」改为「主键ID」，`version`在文档中显示，字段与列名不改。main精确提交CI通过后，用现有发布脚本发布到私有Maven，远程制品SHA-256读回一致。

#### 异常处理的错误码与提示

| 情况 | HTTP | 错误码 | errorMessage |
|---|---|---|---|
| 请求体JSON无法解析 | 400 | A0400 | 请求体 JSON 格式不正确 |
| 其他400（缺参数、参数类型不对、方法参数校验等） | 400 | A0400 | 请求参数不正确 |
| 接口不存在 | 404 | A0001 | 请求的接口不存在 |
| 其他4xx（请求方法、内容类型、上传大小等） | 沿用Spring | A0001 | 请求无法处理 |
| 5xx标准异常与未知异常 | 沿用Spring，未知异常为500 | B0001 | 系统处理失败，请查看服务日志 |

第一行与最后一行的状态码、错误码和文案与rag现有处理一致，rag删掉自己的这两个处理后测试不用改。

### 服务改动（每个服务一个提交）

- user、ai、ai-memory、rag、mcp、message、report：升到utils 1.6.8，服务版本按各自规范递增，CI中固定的utils检出提交同步更新（沿用第1阶段做法）。
- gateway、user、ai-memory、rag、mcp、message、report：`sourceCompatibility`改为Java toolchain 21，写法与ai、ai-agent一致。
- user、ai-memory、rag、message、report：自行声明`spring-boot-starter-websocket`。
- user、rag、ai：错误码常量改用新名（user 57处、rag 6处、ai 2处，含测试）；user的`AuthResourceControllerTest`改为断言不再输出`msg`。服务必须在升到1.6.8的同一个提交里改名，否则编译不过。
- rag：删掉`GlobalExceptionHandler`里与utils重复的JSON解析失败与兜底两个处理（不删的话同一异常有两个处理方法，服务启动失败）；403、502与图谱相关处理保留。
- ai：自有的`AiChatResponse`异常处理不动。
- rag：utils删掉`MultipartConfig`后，在自己的`application.yml`声明`spring.servlet.multipart.max-file-size`与`max-request-size`为1024MB，保持现有上限。
- user、mcp：涉及Redis的测试若依赖`ConfigClientController`产出的Bean，按新的自动配置调整；ai测试启动类的扫描排除规则去掉已删的类。
- 各服务`docs/ai-coding`与README里把`msg`列为响应字段、写中文错误码常量的地方同步更新；`archive/`下的历史文件不改。
- rag：自行声明`poi-ooxml` 6.0.0，分两个提交：第一个提交在utils 1.6.7（POI 5.5.1）下加测试，把解析输出固定下来；第二个提交再升utils与POI。测试文件至少覆盖：Excel带缓存值的公式、无缓存值的公式、日期与数字格式；Word普通段落、表格、行内内容控件中的文字、块级内容控件。升到6.0.0后输出必须一致；内容控件文字丢失或公式输出变化时，修改解析器恢复原输出，确属修正原有错误的差异在台账记录裁决后接受。已入库的知识不重新解析。

## 验证

- utils：全套测试，其中错误码的编码与文案逐项比对导出的快照，`ApiResponse`与异常处理按上表逐项断言；`javadoc`（`failOnError = true`）；依赖树核对，不再出现上表删除与移走的库，MinIO与X File Storage版本不变；精确提交CI；远程Maven SHA-256读回。
- 各服务：精确提交CI通过；本机IDEA启动8个服务（ai-agent一起跑整链），启动无缺失Bean报错，`/actuator/health`为UP（删掉RabbitMQ与ES的starter后，健康检查不再包含这两项）。
- 定向检查：
  - user、message：对照测试已在utils覆盖，运行时走一遍用到转换的接口；
  - rag：上述POI测试，外加用测试文件经真实Java解析器解析一次；
  - 统一存储：MinIO与X File Storage未变，抽查一次签名上传与读回；
  - `OkHttpUtils`：上述TLS测试；
  - Redis锁：单元测试覆盖安全解锁与回调式加锁；mcp的注册发布锁的键名与等待时间不变；
  - 统一报文：经网关对user发一个请求体JSON写错的请求和一个不存在的路径，分别得到400与404的统一报文，含`traceId`，没有`msg`；
  - Redis：登录（令牌存取）与mcp的注册发布锁在新的自动配置下正常，Redis健康检查为UP；
  - rag上传：约2MB的合成文档仍能上传（超过Spring Boot默认的1MB）；
  - 数据权限与异步上下文：单元测试覆盖生成的条件与现在一致、出错不放行、异步任务拿到提交时的租户。
- 整链：沿用第1阶段的灰度复验（登录、message→user、资源、RAG、AI对话），全部200且对话无错误事件。
- 本机运行验证需要个人项目的服务在跑；若当时本机端口被其他项目占用，先请用户切换。

## 回退

- 任一服务可单独退回utils 1.6.7，私有Maven保留旧版制品；错误码改名与rag异常处理的删减和升级在同一个服务提交里，回退时一起退。
- utils整版回退时，各服务把依赖与CI检出提交改回1.6.7对应值；移到服务里的websocket与POI声明可以保留，不影响旧版。
- 若切换时清理过`cache:*`键，缓存条目按需重建，无需恢复。
- Redis连接方式的改动不涉及Nacos，退回1.6.7即恢复原连接方式。

## 不在本阶段

- MinIO 9与X File Storage：等X File Storage发布支持MinIO 9的版本再评估。
- jjwt：第5阶段由Keycloak取代。
- ai存储表时区类型：第3阶段与Flyway一起处理。
- `EntityBase`字段与数据库列改名（`create_time`、`update_time`、`creator`、`updater`，逻辑删除字段`deleted`、列`is_deleted`）以及自动填充创建人、修改人：第3阶段与Flyway一起做。`code`、`description`、`label`、`sorting`是否移出基类、`sorting`是否改名，届时由用户定；创建人存用户ID，ID来源随第5阶段Keycloak一并考虑。
- 成功码由`"0"`改为`"00000"`、响应头`traceId`的命名、HTTP状态码约定（业务失败回200；401、403与未知异常用HTTP状态码），以及改用RFC 9457 ProblemDetail：都不改。
- 新增分页返回类型：`Page`现有输出已满足admin-web，按「有需要再加」不新增。
- ai自有的`AiChatResponse`异常处理；admin-web。
- AspectJ移到mcp、rag：会改变其他服务的自动代理方式，需单独确认后再做。
- 数据源改用dynamic-datasource原生配置（含Seata代理）、DDL改Flyway：第3阶段；OSS直传签名升V4会改前端上传字段，不在本阶段。
- Docker镜像构建验证：另做。
- Hutool（已是最新5.8.48）、阿里云OSS 3.18.5、腾讯云COS 5.6.283等其他依赖不升级；RedisTemplate序列化方式、Spring Boot 4.1。
- Nacos里已删功能遗留的配置键（如RabbitMQ、请求日志索引、短信与钉钉参数），以及utils运维模板里的`elasticsearch.yaml`、`rabbitmq.yaml`，本阶段不清理；删除后这些键不再被读取。各服务启动类只排除`DataSourceAutoConfiguration`，删除RabbitMQ与ES的starter不会触发「排除的自动配置类不存在」的启动错误。
- 美联项目。

## 留给实施确定的细节

- `GeneralConvertor`新实现的具体写法，以对照测试全部通过为准；测试暴露出Dozer特有的转换时，在实现里补齐。
- POI 6下rag解析器是否需要调整，由固定输出的测试决定。
- `OkHttpUtils`线程安全初始化的具体写法。
- 拿不到锁时抛出的异常类型命名。
- 各服务的具体新版本号。
- `cache:*`键的检查结果与处理方式。
- javadoc错误的逐个修法。
- 未被引用的157个错误码常量的英文名（按文案意译、大写下划线；被引用的20个以计划为准）。
- `JsonUtil`自动配置的具体写法；JSqlParser表达式的具体构造方式（以生成的条件文本与现在一致为准）。
- 删掉`SensitiveStrategyConfig`后若mybatis-mate启动缺Bean，改为只注册内置策略的Bean，台账记录裁决。
