# 第2阶段utils改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** utils删掉51个过时、小众、从未生效或已被替代的类和十余个依赖，替换Dozer、修复`OkHttpUtils`、换Redis缓存序列化器、按Redisson主流用法优化Redis锁，按GitHub上同类最热门项目的主流用法修复与改写保留的工具和配置，错误码改英文命名、整理统一返回对象并补齐统一异常处理、修好javadoc后发布1.6.8；8个服务升级、改用Java toolchain，并整体验证。

**Architecture:** utils按18个任务依次提交，每个提交本机验证通过、精确提交CI为success后再做下一个，最后一次发布1.6.8。rag先在旧版下用测试固定POI解析输出，再升级；其余服务各一个提交，错误码改名与升级在同一个提交里。最后在本机跑整链复验。

**Tech Stack:** Gradle 9.9.1、JDK 21（Azul Zulu 21.0.13）、Spring Boot 4.0.9、Spring Data Redis 4.0.8、Hutool 5.8.48、OkHttp 5.5.1、Apache POI 6.0.0、JUnit 5。

**Spec:** `docs/superpowers/specs/2026-11-14-phase2-utils-refactor-design.md`

## Global Constraints

- 范围：只改个人仓库utils、rag、user、message、ai-memory、report、mcp、ai、gateway与kellen-workspace文档；美联项目不动；ai-agent不改代码。
- 删除的类（先删35个，再按「主流用法核对」删16个）、删除与移出的依赖、保留清单，以规格「现状」各表为准。POI 6.0.0只由rag声明；`spring-boot-starter-websocket`只由user、ai-memory、rag、message、report声明。
- 不升级、不改：MinIO 8.6.0、X File Storage 2.3.0、jjwt 0.9.1、Hutool 5.8.48、阿里云OSS 3.18.5、腾讯云COS 5.6.283、RedisTemplate的序列化方式、Nacos（`redis.yaml`已有`spring.data.redis.*`）；不清理Nacos遗留配置键与utils运维模板。
- 保留：mybatis-mate、OkHttp、hutool-all、`redisson-spring-boot-starter`、AspectJ、Seata；Jedis随Redis连接改为自动配置而删除。
- Redis分布式锁的行为不变：键名、等待时间与看门狗续期都不变；mcp的锁代码不改。防重复提交从未生效，整条删除。
- 保留工具的改写以规格「主流用法核对」各表为准；这些工具在服务里没有调用方的，改名不需要服务配合。
- 删除显式依赖后若它仍被其他库传递引入，以依赖树为准，解析版本不得低于原先显式声明的版本，否则改用版本约束锁定。
- 错误码：编码与文案一个不改（含成功码`"0"`），只改Java常量名与属性名；被引用的20个常量新名以任务15的表为准。
- 统一返回：`ApiResponse`只删`msg`、加`traceId`、固定GMT+8时间、`fail`改泛型，其余字段与HTTP状态码约定不变；admin-web不改。
- 异常处理：任务11删除5个异常后剩下的7类处理仍回HTTP 200、错误码不变；新增处理的状态码、错误码与文案以规格「异常处理的错误码与提示」表为准；Spring Security的鉴权异常原样抛出；ai自有的`AiChatResponse`异常处理不动。
- `EntityBase`只改两处接口文档说明，字段与数据库列不改名（第3阶段）。
- 版本：utils 1.6.7 → 1.6.8；服务按各仓`VERSIONING_SPEC`递增：gateway 1.2.0→1.2.1、user 1.4.1→1.4.2、ai 1.7.6→1.7.7、ai-memory 1.1.7→1.1.8、rag 1.3.0→1.3.1、mcp 1.2.6→1.2.7、message 1.2.6→1.2.7、report 1.2.6→1.2.7。推送前核对远端tag与已发布制品没有占用。
- 构建一律JDK 21：`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`。
- Git：开工、提交前、推送前都`git fetch`；每个任务从最新`origin/main`建短分支（前缀按各仓`BRANCHING_SPEC`），本机验证全过后快进合入`main`并推送、删短分支；读回该精确提交的CI为success再开下一个任务；不强推。
- 凭据：私有Maven凭据只经`~/.config/kellen-github/observability-20261106/run-maven-release.py`注入Gradle；NAS只用`ssh qw-nas`与`sudo -n docker`；Redis口令只在容器内引用；登录口令与灰度签名密钥不落盘、不打印。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-14-phase2-utils-refactor/progress.md`，同目录已放好`maven-readback.py`、`canary_walk.py`、`chain-verify.py`。

## Review Focus

1. 新加的兜底处理吞掉Spring Security的鉴权异常，未登录本应401、无权限本应403，却变成500，admin-web不再跳转登录页。期望原样抛出，由Spring Security处理。→ 任务17测试`securityExceptionsAreRethrown`。
2. 服务的`@RestControllerAdvice`子类与utils基类（含`ResponseEntityExceptionHandler`）对同一异常各有一个处理方法，Spring启动时报`Ambiguous @ExceptionHandler`，服务起不来。期望rag删掉重复的两个处理，其他服务的子类没有这类处理。→ 任务21第3步的`RagControllerSecurityTest`、任务21至23第1步的检查、任务24第2步。
3. Redis连接改由Spring Boot与Redisson官方starter自动配置后，登录令牌存取或mcp注册发布锁失败（连接参数、空口令处理与原先手工配置不同，或锁工具没装配上）。期望登录与mcp注册正常，Redis健康为UP。→ 任务14测试`registersLockerWhenRedissonClientPresent`、任务24第2、5步。
4. 数据权限改为直接构造表达式后，生成的条件与原先不同（别名、引号转义、`IN`列表），列表接口返回的数据变多或变少。期望条件文本与原先一致、出错就抛出。→ 任务12的`DataPermissionSqlHandlerTest`。
5. POI 6的日期按秒取整、公式取缓存值等变化，让rag解析文本漂移。期望与POI 5.5.1基线一致。→ 任务20、21的基线测试。

另两项由各自任务的测试与检查覆盖：`EntityBase`的`isDelete`等继承字段在Hutool与Dozer之间映射一致（任务6`copiesInheritedBaseFieldsIncludingIsDelete`）；删掉starter或类之后服务启动不缺Bean（任务24第2步）。

---

### Task 1: 提交设计与计划

**Files:**
- Create: `kellen-workspace/docs/superpowers/specs/2026-11-14-phase2-utils-refactor-design.md`
- Create: `kellen-workspace/docs/superpowers/plans/2026-11-14-phase2-utils-refactor.md`

- [ ] **Step 1:** `git fetch`后确认`main`与`origin/main`一致、工作区只有这两份新文件。
- [ ] **Step 2:** 运行`python3 -m unittest discover -s tests`，期望OK。
- [ ] **Step 3:** 提交`docs: 第2阶段utils改造设计与计划`并推送`main`；读回该提交CI为success。

### Task 2: utils删除钉钉与阿里云一组

**Files（均在`utils/src/main/java/com/kellen/`下）:**
- Delete: `aliyun/DingDing.java`、`aliyun/LiveStreaming.java`、`aliyun/Sms.java`、`aliyun/dingding/{AtMobiles,DingDingUtil,SendRebootUtil}.java`、`aliyun/dingding/markdown/{CustomMarkDown,MarkDownModel,MarkDownRebootModel,SimpleMarkdownBuilder}.java`、`aliyun/dingding/text/{ContentModel,TextRebootModel}.java`、`aliyun/live/CreateLiveUtils.java`、`aliyun/oss/OssUtils.java`、`aliyun/sms/SmsUtils.java`、`aliyun/workflow/{WorkflowRegion,WorkflowUtils}.java`、`config/aliyun/CommonAliyunProperties.java`、`config/wechat/CommonWechatProperties.java`、`utils/enumeration/SmsEnum.java`（共20个）
- Modify: `utils/build.gradle`（删`aliyunCoreVersion`、`aliyunFnfVersion`及`aliyun-java-sdk-core`、`aliyun-java-sdk-fnf`两条依赖与注释）
- Modify: `utils/src/test/java/com/kellen/aliyun/OptionalAliyunConfigurationTest.java`（删针对已删Bean的用例，保留`AliyunKey`、`Oss`相关用例）

- [ ] **Step 1:** 删除上述文件与依赖，调整测试。
- [ ] **Step 2:** `grep -rlE "DingDing|SendRebootUtil|LiveStreaming|CreateLiveUtils|\bSms\b|SmsUtils|SmsEnum|WorkflowUtils|WorkflowRegion|OssUtils\b|CommonAliyunProperties|CommonWechatProperties" utils/src`。Expected：无输出。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 4:** `./gradlew dependencyInsight --dependency aliyun-java-sdk-core --configuration runtimeClasspath`。Expected：仅由`aliyun-sdk-oss`传递引入，版本4.7.8；`aliyun-java-sdk-fnf`不再出现。
- [ ] **Step 5:** 提交`refactor: 删除钉钉与阿里云短信、直播、工作流等无用集成`，按全局Git约束交付并读回CI为success。

### Task 3: utils删除请求日志写ES与配套参数检查

**Files（`utils/src/main/java/com/kellen/`下）:**
- Delete: `aop/RequestRequiredAspect.java`、`utils/annotations/RequestRequired.java`、`config/elasticsearch/ElasticsearchConfig.java`、`log/entity/{ElasticSearchRequestLog,RequestLog}.java`、`log/mapper/ElasticSearchRequestLogMapper.java`、`log/service/ElasticSearchRequestLogService.java`、`log/service/impl/ElasticSearchRequestLogServiceImpl.java`、`utils/http/RequestUtil.java`、`utils/methods/{MethodsBean,MethodsJudge}.java`（共11个）
- Delete: `utils/src/test/java/com/kellen/aop/RequestRequiredAspectPrivacyTest.java`、`utils/src/test/java/com/kellen/log/entity/ElasticSearchRequestLogIndexTest.java`
- Modify: `utils/build.gradle`（删`spring-boot-starter-data-elasticsearch`）

- [ ] **Step 1:** 删除上述文件与依赖。
- [ ] **Step 2:** `grep -rlE "RequestRequired|ElasticSearchRequestLog|RequestLog\b|ElasticsearchConfig|RequestUtil|MethodsBean|MethodsJudge" utils/src`。Expected：无输出；`Methods`、`MethodsInit`、`MethodsParam`、`ReflectionUtils`仍在（随防重复提交在任务11删除）。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 4:** 提交`refactor: 删除从未生效的请求日志写ES与配套参数检查`，交付并读回CI。

### Task 4: utils删除PdfUtils、RabbitMQ配置、停用拦截器与空注解

**Files:**
- Delete: `utils/src/main/java/com/kellen/utils/file/PdfUtils.java`、`config/rabbit/RabbitMqConfig.java`、`config/actuator/ActuatorInterceptor.java`、`utils/annotations/DynamicDataSource.java`
- Delete: `utils/src/test/java/com/kellen/config/actuator/LegacyActuatorInterceptorTest.java`
- Modify: `utils/build.gradle`（删`itextVersion`、`fontAsianVersion`、`pdfboxVersion`及`itext7-core`、`font-asian`、`pdfbox`、`spring-boot-starter-amqp`）

- [ ] **Step 1:** 删除上述文件与依赖。
- [ ] **Step 2:** `grep -rlE "PdfUtils|RabbitMqConfig|ActuatorInterceptor|annotations\.DynamicDataSource|com\.itextpdf|org\.apache\.pdfbox|springframework\.amqp" utils/src`。Expected：无输出。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 4:** 提交`refactor: 删除PdfUtils、RabbitMQ配置、停用拦截器与空注解`，交付并读回CI。

### Task 5: utils删除无人使用的依赖

**Files:**
- Modify: `utils/build.gradle`（删`orikaVersion`、`javaxMailVersion`、`aviatorVersion`、`commonsCollectionsVersion`、`guavaVersion`及对应五条依赖与注释）
- Modify: `utils/src/main/java/com/kellen/config/redis/RedisCacheConfig.java`（`Maps.newHashMap()`改为`new HashMap<>()`，删Guava导入）

- [ ] **Step 1:** 改动如上。
- [ ] **Step 2:** `grep -rlE "ma\.glasnost|javax\.mail|googlecode\.aviator|commons\.collections4|com\.google\.common" utils/src`。Expected：无输出。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 4:** 对`guava`、`commons-collections4`各跑一次`dependencyInsight`。Expected：要么不再出现；要么仍被传递引入且版本不低于原先的33.8.0-jre与4.5.0，低于则加版本约束。
- [ ] **Step 5:** 提交`refactor: 删除Orika、javax.mail、Aviator、commons-collections4与Guava`，交付并读回CI。

### Task 6: utils替换Dozer

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/convert/GeneralConvertorTest.java`
- Modify: `utils/src/main/java/com/kellen/utils/convert/GeneralConvertor.java`
- Modify: `utils/build.gradle`（删`dozerVersion`与`dozer-core`）

**Interfaces:**
- Produces：`GeneralConvertor`保留的四个公开方法签名不变：`<T,S> List<T> convertor(List<S>, Class<T>)`、`<T,S> T convertor(S, Class<T>)`、`void convertor(Object, Object)`、`<T> void copyConvertor(T, Object)`；`<T,S> Set<T> convertor(Set<S>, Class<T>)`删除。

测试里定义两组测试用的类，形态照user、message的实际调用（BO/Query→实体、实体→VO）：基类含`String id`、`LocalDateTime createDateTime`、`Boolean isDelete`、`Integer version`、`String tenantId`；源类另有`String name`、枚举`state`、`Integer type`，以及只在源上的`List<Long> tenantIds`与`Long current`；目标类另有只在目标上的`String stateDesc`。另一组含嵌套对象与`List<String>`，用于深拷贝断言。

- [ ] **Step 1: 写测试**（在现有Dozer实现上运行）：
  - `convertsFlatFieldsLikeBoToEntity`：同名字段值全部相等；`tenantIds`、`current`不影响结果；`stateDesc`为`null`。
  - `copiesInheritedBaseFieldsIncludingIsDelete`：五个继承字段相等，`isDelete`的`true`与`null`都原样带过去。
  - `returnsNullForNullSource`：单对象与`List`重载传`null`都返回`null`。
  - `listOverloadKeepsOrderInArrayList`：结果`instanceof ArrayList`，顺序与源一致。
  - `deepCopiesNestedBeanAndCollection`：嵌套对象与列表`assertNotSame`源对象，内容相等。
  - `copyIntoExistingTargetOverwritesWithSourceNulls`：预先填值的目标，经`convertor(Object, Object)`与`copyConvertor`后，源为`null`的字段变为`null`。
  - `setOverloadRemoved`：`assertThrows(NoSuchMethodException.class, () -> GeneralConvertor.class.getMethod("convertor", Set.class, Class.class))`。
- [ ] **Step 2:** `./gradlew test --tests com.kellen.utils.convert.GeneralConvertorTest`。Expected：只有`setOverloadRemoved`失败。若其余用例与Dozer实际行为不符，按Dozer实际行为修正断言，并在台账记`Ruling:`。
- [ ] **Step 3:** 删除`Set`重载，再跑一次。Expected：全部通过。
- [ ] **Step 4:** 四个方法改用hutool-all实现（如`BeanUtil.copyProperties`配合必要的深拷贝），删除Dozer导入、`dozerVersion`与`dozer-core`。
- [ ] **Step 5:** `./gradlew test`。Expected：BUILD SUCCESSFUL，`GeneralConvertorTest`全部通过；`grep -r dozermapper utils/src utils/build.gradle`无输出。
- [ ] **Step 6:** 提交`refactor: GeneralConvertor改用Hutool实现并删除Dozer`，交付并读回CI。

### Task 7: utils修复OkHttpUtils

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/http/OkHttpUtilsTlsTest.java`
- Modify: `utils/src/main/java/com/kellen/utils/http/OkHttpUtils.java`
- Modify: `utils/build.gradle`（`testImplementation` 增加`com.squareup.okhttp3:mockwebserver3:${okhttpVersion}`、`com.squareup.okhttp3:okhttp-tls:${okhttpVersion}`）

**Interfaces:**
- Produces：`OkHttpClient getOkHttpClient()`签名不变，其余公开方法不变。

- [ ] **Step 1: 写测试**：
  - `rejectsSelfSignedHttpsServer`：用`HeldCertificate`签发自签名证书启动HTTPS的MockWebServer，经`getOkHttpClient()`请求，`assertThrows(SSLHandshakeException.class, ...)`。
  - `plainHttpRequestStillWorks`：HTTP的MockWebServer返回200，请求成功。
  - `returnsSameClientAcrossThreads`：8个线程并发调用`getOkHttpClient()`，得到同一个实例。
- [ ] **Step 2:** 只跑该测试类。Expected：`rejectsSelfSignedHttpsServer`失败（现实现信任所有证书）。
- [ ] **Step 3:** 删去信任所有证书的TrustManager、`SSLContext`与恒为真的主机名校验，使用OkHttp默认TLS；客户端改为线程安全的单例（如持有者类）；保留超时设置与其余方法。
- [ ] **Step 4:** `./gradlew test`。Expected：BUILD SUCCESSFUL，三个用例通过。
- [ ] **Step 5:** 提交`fix: OkHttpUtils恢复默认证书与主机名校验`，交付并读回CI。

### Task 8: utils移出WebSocket与POI

**Files:**
- Modify: `utils/build.gradle`（删`spring-boot-starter-websocket`、`poiVersion`与`poi-ooxml`）

- [ ] **Step 1:** 改动如上。
- [ ] **Step 2:** `grep -rlE "org\.springframework\.web\.socket|org\.apache\.poi" utils/src`。Expected：无输出。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 4:** 提交`build: WebSocket与POI改由使用它们的服务自行声明`，交付并读回CI。

### Task 9: utils Redis缓存序列化器

**Files:**
- Create: `utils/src/test/java/com/kellen/config/redis/RedisCacheConfigTest.java`
- Modify: `utils/src/main/java/com/kellen/config/redis/RedisCacheConfig.java`

**Interfaces:**
- Produces：`RedisCacheConfig`新增包内可见的`static RedisSerializer<Object> valueSerializer()`，私有`valuePair()`改为基于它；`@Bean CacheManager cacheManager(RedisConnectionFactory)`签名不变。

- [ ] **Step 1: 只读预检**：在`orb-redis`容器内，用`REDIS_ARGS`里`--requirepass`后的口令，对每个db统计`--scan --pattern 'cache:*'`的键数，只输出db号与数量。Expected：全部为0。不为0就停下，请用户决定清理还是换缓存前缀。
- [ ] **Step 2: 写测试**：
  - `usesJackson3GenericSerializer`：`valueSerializer()`是`GenericJacksonJsonRedisSerializer`。
  - `roundTripsTypedValue`：对含`String`、`Integer`、`LocalDateTime`的样例对象序列化再反序列化，类型与值相等。
- [ ] **Step 3:** 只跑该测试类。Expected：`usesJackson3GenericSerializer`失败。
- [ ] **Step 4:** 改用`GenericJacksonJsonRedisSerializer`，必要时为`LocalDateTime`配置时间模块。
- [ ] **Step 5:** `./gradlew test`。Expected：BUILD SUCCESSFUL，编译输出不再有`GenericJackson2JsonRedisSerializer`的移除告警。
- [ ] **Step 6:** 提交`refactor: Redis缓存序列化器换成Jackson 3实现`，交付并读回CI。

### Task 10: utils Redis锁优化（行为兼容）

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/redisson/RedissonLockerTest.java`
- Create: `utils/src/main/java/com/kellen/utils/redisson/LockAcquireException.java`
- Modify: `utils/src/main/java/com/kellen/utils/redisson/{Locker,RedissonLocker,LockUtil}.java`

**Interfaces:**
- Consumes：`ConfigClientController`继续创建`RedissonLocker`并调用`LockUtil.setLocker`（任务14改为自动配置，这两个调用不变）。
- Produces：`Locker`与`LockUtil`现有方法签名不变；`RedissonLocker(RedissonClient)`构造器不变；新增`<T> T executeWithLock(String lockKey, long waitTime, TimeUnit unit, Supplier<T> action)`与`void runWithLock(String lockKey, long waitTime, TimeUnit unit, Runnable action)`；拿不到锁抛`LockAcquireException`（`RuntimeException`），等待被中断时先恢复中断标记再抛它。防重复提交在任务11整条删除，本任务不改它。

- [ ] **Step 1: 写测试**（Mockito模拟`RedissonClient`与`RLock`）：
  - `unlockSkipsWhenNotHeldByCurrentThread`：`isHeldByCurrentThread()`为假时不调`unlock()`。
  - `unlockReleasesWhenHeldByCurrentThread`。
  - `executeWithLockRunsActionThenReleases`：`tryLock(10, SECONDS)`为真时返回action结果，`unlock()`调用一次。
  - `executeWithLockReleasesWhenActionThrows`：action抛异常时仍解锁，异常原样抛出。
  - `executeWithLockThrowsWhenNotAcquired`：`tryLock`为假时抛`LockAcquireException`，action与`unlock()`都不调用。
  - `executeWithLockRestoresInterruptFlag`：`tryLock`抛`InterruptedException`时，线程中断标记为真，抛`LockAcquireException`。
  - `executeWithLockUsesWatchdog`：只调用两参数的`tryLock(waitTime, unit)`，不调用带租约的三参数版本。
- [ ] **Step 2:** 只跑该测试类。Expected：新方法未定义导致编译失败，补上空实现后`unlockSkipsWhenNotHeldByCurrentThread`失败。
- [ ] **Step 3:** 实现：`unlock`先检查持有者；回调方法按上面的语义实现。
- [ ] **Step 4:** `./gradlew test`。Expected：BUILD SUCCESSFUL，七个用例通过。
- [ ] **Step 5:** 提交`refactor: Redis锁按Redisson主流用法补回调式加锁与安全解锁（行为兼容）`，交付并读回CI。

### Task 11: utils删除从未生效与无人使用的工具

**Files（`utils/src/main/java/com/kellen/`下）:**
- Delete: `utils/annotations/{PreventRepeat,Methods}.java`、`idempotent/PreventRepeatInit.java`、`utils/methods/{MethodsInit,MethodsParam}.java`、`utils/reflect/ReflectionUtils.java`、`utils/exception/{PreventRepeatException,DateException,ParameterNullException,SignException,SmsException}.java`、`config/actuator/ServiceShutDownEndpoint.java`、`config/file/MultipartConfig.java`、`utils/datasource/DataSourceUtil.java`、`config/sensitive/SensitiveStrategyConfig.java`（共15个）
- Modify: `utils/exception/ApiExceptionHandler.java`（删这5个异常的处理方法，剩`UserException`、`BusinessException`、`RpcException`、`SQLException`、`ConstraintViolationException`、`MethodArgumentNotValidException`、`BindException`七类）
- Modify: `config/swagger/Swagger.java`（删`dataSource`请求头的安全方案与安全项）
- Modify: `utils/src/test/java/com/kellen/config/actuator/ActuatorProtectionTestSupport.java`（删`ServiceShutDownEndpoint`所需的Nacos注册mock与相关说明）

- [ ] **Step 1:** 删除上述文件与处理方法，调整测试。
- [ ] **Step 2:** `grep -rlE "PreventRepeat|MethodsInit|MethodsParam|utils\.reflect\.ReflectionUtils|annotations\.Methods\b|DateException|ParameterNullException|SignException|SmsException|ServiceShutDownEndpoint|MultipartConfig|DataSourceUtil|SensitiveStrategyConfig|testStrategy" utils/src`。Expected：无输出。
- [ ] **Step 3:** `./gradlew test`。Expected：BUILD SUCCESSFUL。若mybatis-mate因缺少`ISensitiveStrategy` Bean启动失败，改为只注册内置策略的Bean，并在台账记`Ruling:`。
- [ ] **Step 4:** 提交`refactor: 删除从未生效的防重复提交与无人使用的工具`，交付并读回CI。

### Task 12: utils修复数据权限与异步上下文

**Files:**
- Create: `utils/src/test/java/com/kellen/datapermission/DataPermissionSqlHandlerTest.java`
- Create: `utils/src/test/java/com/kellen/observability/TraceContextTaskDecoratorTest.java`
- Modify: `utils/src/main/java/com/kellen/datapermission/DataPermissionSqlHandler.java`
- Modify: `utils/src/main/java/com/kellen/observability/TraceContextTaskDecorator.java`、`utils/src/main/java/com/kellen/config/async/AsyncConfig.java`（线程名前缀`gbw`改为`async-`）

**Interfaces:**
- Produces：`DataPermissionSqlHandler.getSqlSegment(Table, Expression, String)`签名不变，直接返回JSqlParser表达式：本人与部门为等值条件，部门树与自定义为`IN`，列带表别名（无别名时用表名），字符串值先把单引号转义再放进`StringValue`（写法同`MyBatisPlusConfig`的租户处理）；不再拼字符串、不再解析，内部异常不吞掉。`TraceContextTaskDecorator.decorate(Runnable)`返回`TtlRunnable.get(Context.current().wrap(runnable))`，类名不变。

测试里`DataPermissionProperties`开启，表规则为`auth_user`：用户列`id`、部门列`dept_id`；用户经`UserContextHolder.set(...)`放入、`finally`里清理。

- [ ] **Step 1: 写测试**：
  - `selfScopeFiltersByUserColumnWithAlias`：表`auth_user`别名`u`、用户`u1`、范围`SELF` → 条件文本为`u.id = 'u1'`。
  - `deptScopeFiltersByDeptColumn`：范围`DEPT`、部门`d1` → `u.dept_id = 'd1'`。
  - `deptTreeScopeBuildsInList`：范围`DEPT_TREE`、本部门`d1`、范围部门`d2` → `IN`条件含`'d1'`与`'d2'`。
  - `allScopeAddsNoCondition`：范围`ALL` → `null`。
  - `quotesInValuesAreEscaped`：用户ID为`a'b` → `u.id = 'a''b'`。
  - `unexpectedErrorsAreNotSwallowed`：用户的`getDataScopeDeptIds()`抛`IllegalStateException`时，`getSqlSegment`抛出该异常，不返回`null`。
  - `taskSeesSubmittersTenantContext`：核心与最大线程数都为1的`ThreadPoolTaskExecutor`配上该装饰器；先在租户`t1`下提交并等它完成，再在租户`t2`下提交，第二个任务读到`t2`。
  - `taskKeepsOtelContext`：提交时的活动span在任务里仍是当前span（span构造照`TraceContextTest`）。
- [ ] **Step 2:** 只跑这两个测试类。Expected：`unexpectedErrorsAreNotSwallowed`与`taskSeesSubmittersTenantContext`失败；条件文本类用例在现实现下已通过（字符串拼接结果相同），保留它们确认改写后条件不变。
- [ ] **Step 3:** 按Interfaces实现。
- [ ] **Step 4:** `./gradlew test`。Expected：BUILD SUCCESSFUL，八个用例通过，`TraceContextTest`不受影响。
- [ ] **Step 5:** 提交`fix: 数据权限出错不再放行，异步任务带上租户与用户上下文`，交付并读回CI。

### Task 13: utils工具类按主流改写

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/verify/PhoneValidatorTest.java`、`utils/src/test/java/com/kellen/utils/http/IpUtilsTest.java`、`utils/src/test/java/com/kellen/utils/http/OkHttpUtilsResponseTest.java`
- Create: `utils/src/main/java/com/kellen/config/json/JsonUtilAutoConfiguration.java`（登记到`META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`）
- Modify: `utils/src/test/java/com/kellen/utils/json/JsonUtilTest.java`（跟随改名）
- Modify（`utils/src/main/java/com/kellen/`下）: `utils/verify/Phone.java`、`utils/verify/check/PhoneValidator.java`、`utils/http/IpUtils.java`、`utils/http/OkHttpUtils.java`、`utils/enumeration/HttpType.java`、`utils/json/JsonUtil.java`、`utils/context/DynamicSourceTtl.java`

**Interfaces:**
- Consumes：任务7的`OkHttpUtils.getOkHttpClient()`。
- Produces：
  - `@Phone`：`@Target({FIELD, METHOD, PARAMETER, ANNOTATION_TYPE, TYPE_USE})`；校验用Hutool `PhoneUtil.isMobile`；提示语用注解的`message()`；`null`与空串视为通过。
  - `IpUtils`：`String getClientIp(HttpServletRequest)`（Hutool `JakartaServletUtil.getClientIP`取值，回环地址统一返回`127.0.0.1`）、`String getLocalIp()`、`boolean isInnerIp(String)`（Hutool `NetUtil.isInnerIP`）；删除`getIp(...)`与`localAddressIp`。
  - `JsonUtil`：`String toJsonStr(Object)`、`<T> T toBean(String, Class<T>)`、`<T> List<T> toList(String, Class<T>)`、`JsonMapper getJsonMapper()`、`static void init(JsonMapper)`；默认用Jackson 3默认配置的`JsonMapper`，`JsonUtilAutoConfiguration`在有`JsonMapper` Bean时调用`init`；空串与`null`输入返回`null`，异常包成`IllegalStateException`（与现状一致）。
  - `OkHttpUtils`：`String send(Request.Builder, HttpWay, String url, String params, HttpType)`在try-with-resources里执行并返回响应体文本，非2xx抛`IOException`（消息含状态码）；删除`execute(Request)`与两个浏览器标识常量；`createRequestParamsFrom`改名`createRequestParamsForm`；`request(...)`去掉不会抛出的`UnsupportedEncodingException`；`HttpType.FROM`改为`FORM`（值`form`）。
  - `DynamicSourceTtl`：上下文字段改为`private static final`，其余行为不变。

- [ ] **Step 1: 写测试**：
  - `PhoneValidatorTest`：`acceptsCurrentMobilePrefixes`（`16612345678`、`19912345678`、`13812345678`通过）；`rejectsMalformedNumbers`（`12345678901`、`1381234567`、`abc`不通过）；`usesAnnotationMessage`（`@Phone(message = "请填写手机号")`的违规提示为「请填写手机号」）；`blankIsValid`；`appliesToContainerElements`（`List<@Phone String>`里一个错号产生一条违规）。
  - `IpUtilsTest`：`innerIpRangesAreRecognized`（`10.1.2.3`、`172.16.0.1`、`192.168.1.1`为真，`8.8.8.8`为假）；`loopbackClientBecomes127`（remoteAddr为`0:0:0:0:0:0:0:1`时返回`127.0.0.1`）；`forwardedForFirstHopIsUsed`（`X-Forwarded-For: 203.0.113.7, 10.0.0.1`返回`203.0.113.7`）。
  - `OkHttpUtilsResponseTest`（MockWebServer）：`sendReturnsBodyText`（200、响应体`ok`）；`sendFailsOnNon2xx`（500时抛`IOException`，消息含`500`）；`formBodyUsesFormKeyValues`（`HttpType.FORM`发送`{"a":"1"}`，服务端收到`a=1`与`application/x-www-form-urlencoded`）。
  - `JsonUtilTest`：现有用例改用新方法名；新增`usesSpringMapperAfterInit`（`init`一个开启缩进输出的`JsonMapper`后`toJsonStr`输出含换行，测试结束恢复默认）。
- [ ] **Step 2:** 只跑这四个测试类。Expected：编译失败（新方法名与`HttpType.FORM`不存在）。
- [ ] **Step 3:** 按Interfaces实现。
- [ ] **Step 4:** `./gradlew test`；`grep -rnE "JsonUtil\.(bean|list|json)\(|HttpType\.FROM|localAddressIp|IpUtils\.getIp\(" utils/src`。Expected：BUILD SUCCESSFUL，新用例全部通过；grep无输出。
- [ ] **Step 5:** 提交`refactor: 手机号、IP、JSON与OkHttp工具按主流用法改写`，交付并读回CI。

### Task 14: utils配置按主流对齐

**Files:**
- Delete: `utils/src/main/java/com/kellen/config/actuator/ConfigClientController.java`
- Delete: `utils/src/test/java/com/kellen/config/actuator/ActuatorPackageExclusionTest.java`（`config.actuator`包删空后它测的排除已无意义；只为它存在的测试支撑一并删）
- Create: `utils/src/main/java/com/kellen/config/redis/RedisLockAutoConfiguration.java`（登记到`AutoConfiguration.imports`）
- Create: `utils/src/test/java/com/kellen/config/redis/RedisLockAutoConfigurationTest.java`
- Create: `utils/src/test/java/com/kellen/config/mybatis/MyBatisPlusInterceptorOrderTest.java`
- Modify: `utils/build.gradle`（删`redis.clients:jedis`）
- Modify: `utils/src/main/java/com/kellen/config/mybatis/MyBatisPlusConfig.java`（插件顺序）
- Modify: `utils/src/main/java/com/kellen/config/dubbo/DubboContextPropagationFilter.java`、`utils/src/main/java/com/kellen/config/datasource/DynamicDataSourceConfig.java`、`utils/src/test/java/com/kellen/config/dubbo/DubboContextPropagationFilterTest.java`（`io.seata.*`改为`org.apache.seata.*`）
- Modify: `utils/src/test/java/com/kellen/config/actuator/ActuatorProtectionTestApplication.java`（去掉对已删类的扫描排除）

**Interfaces:**
- Consumes：任务10的`RedissonLocker(RedissonClient)`与`LockUtil.setLocker(Locker)`。
- Produces：Redis连接、`StringRedisTemplate`与`RedissonClient`由Spring Boot与`redisson-spring-boot-starter`按`spring.data.redis.*`自动配置；`RedisLockAutoConfiguration`为`@AutoConfiguration(afterName = {"org.redisson.spring.starter.RedissonAutoConfigurationV2", "org.redisson.spring.starter.RedissonAutoConfigurationV4"})`加`@ConditionalOnBean(RedissonClient.class)`，提供`RedissonLocker`并调用`LockUtil.setLocker`；MyBatis-Plus插件顺序为租户 → 数据权限 → 分页 → 乐观锁 → 非法SQL检查 → 防全表更新删除。

- [ ] **Step 1: 写测试**：
  - `RedisLockAutoConfigurationTest`（`ApplicationContextRunner`）：`registersLockerWhenRedissonClientPresent`（有mock的`RedissonClient`时有`RedissonLocker`，调用`LockUtil`现有的加锁方法后，mock的`getLock`被调用）；`skipsWhenNoRedissonClient`（没有`RedissonClient`时不创建`RedissonLocker`，启动不报错）。
  - `MyBatisPlusInterceptorOrderTest`：`innerInterceptorsFollowOfficialOrder`（租户、数据权限、非法SQL、防全表四个开关都打开时，`getInterceptors()`的类型依次为`TenantLineInnerInterceptor`、`DataPermissionInterceptor`、`PaginationInnerInterceptor`、`OptimisticLockerInnerInterceptor`、`IllegalSQLInnerInterceptor`、`BlockAttackInnerInterceptor`）。
- [ ] **Step 2:** 只跑这两个测试类。Expected：`RedisLockAutoConfigurationTest`编译失败（类不存在），`innerInterceptorsFollowOfficialOrder`失败。
- [ ] **Step 3:** 实现：删`ConfigClientController`，加`RedisLockAutoConfiguration`并登记；调整插件顺序；改Seata包名；删Jedis；调整两个actuator测试类。
- [ ] **Step 4:** `./gradlew test`；`grep -rn "import io\.seata\." utils/src`；`./gradlew dependencyInsight --dependency jedis --configuration runtimeClasspath`。Expected：BUILD SUCCESSFUL，三个新用例通过；grep无输出；依赖树里没有jedis。
- [ ] **Step 5:** 提交`refactor: Redis连接交给Spring Boot与Redisson官方starter，插件顺序与Seata包名按官方`，交付并读回CI。

### Task 15: utils错误码改英文命名

**Files:**
- Create: `utils/src/test/resources/return-codes.tsv`（改名前从现有源码导出，每行`编码<TAB>文案`，按声明顺序）
- Create: `utils/src/test/java/com/kellen/utils/enumeration/ReturnCodeTest.java`
- Modify: `utils/src/main/java/com/kellen/utils/enumeration/ReturnCode.java`
- Modify: utils内引用错误码的文件（全部常量引用，以及`ApiResponse`里的`getState()`、`getName()`调用）

**Interfaces:**
- Produces：177个常量全部为英文大写下划线；`@Getter`生成`String getCode()`、`String getMessage()`；删除`getName(String)`、`getState(String)`、`getReturnCode(String)`。被引用的20个常量的新名，后续任务与服务任务都用这些名字：

| 原常量 | 编码 | 新常量 |
|---|---|---|
| 成功 | 0 | `SUCCESS` |
| 用户端错误 | A0001 | `CLIENT_ERROR` |
| 用户账户被冻结 | A0202 | `USER_ACCOUNT_FROZEN` |
| 用户密码错误 | A0210 | `USER_PASSWORD_ERROR` |
| 用户身份校验失败 | A0220 | `USER_AUTHENTICATION_FAILED` |
| 用户未获得第三方登录授权 | A0223 | `THIRD_PARTY_LOGIN_NOT_AUTHORIZED` |
| 用户登录已过期 | A0230 | `USER_LOGIN_EXPIRED` |
| 无效TOKEN | A0116 | `INVALID_TOKEN` |
| 无权限使用 | A0312 | `PERMISSION_DENIED` |
| 用户签名异常 | A0340 | `USER_SIGNATURE_ERROR` |
| RSA签名错误 | A0341 | `RSA_SIGNATURE_ERROR` |
| 用户请求参数错误 | A0400 | `REQUEST_PARAMETER_ERROR` |
| 请求必填参数为空 | A0410 | `REQUIRED_PARAMETER_MISSING` |
| 非法的时间戳参数 | A0414 | `INVALID_TIMESTAMP_PARAMETER` |
| 用户重复请求 | A0506 | `DUPLICATE_REQUEST` |
| 系统执行出错 | B0001 | `SYSTEM_EXECUTION_ERROR` |
| 中间件服务出错 | C0100 | `MIDDLEWARE_SERVICE_ERROR` |
| RPC服务出错 | C0110 | `RPC_SERVICE_ERROR` |
| 数据库服务出错 | C0300 | `DATABASE_SERVICE_ERROR` |
| 通知服务出错 | C0500 | `NOTIFICATION_SERVICE_ERROR` |

其余157个按文案意译，大写下划线；缩写只用RPC、RSA、TOKEN、SMS、CDN、DNS、ID这类通行写法。

- [ ] **Step 1:** 用`python3 -I`按正则`^\s+\S+\("([A-Z0-9]+)", "([^"]*)"\)[,;]`逐行读`ReturnCode.java`，按声明顺序写出`return-codes.tsv`。Expected：177行，首行`0<TAB>成功`，末行`C0503<TAB>邮件提醒服务失败`。
- [ ] **Step 2: 写测试**：
  - `codesAndMessagesMatchSnapshot`：`ReturnCode.values()`按声明顺序逐项的`getCode()`、`getMessage()`与`return-codes.tsv`完全一致，条数相等。
  - `constantNamesAreUpperSnakeCase`：每个`name()`匹配`^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$`。
  - `codesAreUnique`。
  - `referencedConstantsKeepTheirCodes`：上表20个新常量的`getCode()`与表中编码一致。
- [ ] **Step 3:** 只跑该测试类。Expected：编译失败（没有`getCode()`与新常量名）。
- [ ] **Step 4:** 按上表与命名规则改名，属性改为`code`、`message`，删除三个静态查找方法，改utils内全部引用。
- [ ] **Step 5:** `./gradlew test`；`grep -rnE 'ReturnCode\.[^ .(),;:a-zA-Z_]' utils/src`。Expected：BUILD SUCCESSFUL，四个用例通过；grep无输出。
- [ ] **Step 6:** 提交`refactor: 错误码常量改英文命名（编码与文案不变）`，交付并读回CI。

### Task 16: utils统一返回对象

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/response/ApiResponseTest.java`
- Modify: `utils/src/main/java/com/kellen/utils/response/ApiResponse.java`

**Interfaces:**
- Consumes：任务15的`ReturnCode.SUCCESS`、`SYSTEM_EXECUTION_ERROR`、`getCode()`、`getMessage()`；`TraceContextSnapshot.capture().traceId()`。
- Produces：字段为`success`、`code`、`data`、`errorMessage`、`traceId`、`timestamp`，没有`msg`；`public static ApiResponse<Void> success()`、`public static <T> ApiResponse<T> success(T data)`不变；改为`public static <T> ApiResponse<T> fail(ReturnCode returnCode)`与`public static <T> ApiResponse<T> fail(ReturnCode returnCode, String error)`。

- [ ] **Step 1: 写测试**（用Jackson 3的`JsonMapper`序列化后断言JSON；活动span照`TraceContextTest`用`Span.wrap(...).makeCurrent()`构造）：
  - `failureHasNoMsgField`：`fail(USER_PASSWORD_ERROR, "用户名或密码错误")`的JSON里`success`为false、`code`为`A0210`、`errorMessage`为「用户名或密码错误」，没有`msg`。
  - `successHasNoErrorMessageOrMsg`：`success("x")`的JSON里`code`为`0`、`data`为`x`，没有`errorMessage`与`msg`。
  - `traceIdComesFromCurrentSpan`：在活动span内构造的响应，`traceId`等于该span的trace ID。
  - `traceIdOmittedWithoutSpan`：没有活动span时JSON不含`traceId`。
  - `timestampIsGmt8RegardlessOfJvmZone`：默认时区设为UTC（`finally`里恢复）后构造响应，`timestamp`按`yyyy-MM-dd HH:mm:ss`解析并视为+08:00，与当前时刻相差不超过60秒。
  - `failCanBeReturnedFromTypedEndpoint`：`ApiResponse<String> r = ApiResponse.fail(ReturnCode.SYSTEM_EXECUTION_ERROR);`可编译，`r.getData()`为`null`。
- [ ] **Step 2:** 只跑该测试类。Expected：编译失败（`fail`不是泛型、没有`getTraceId()`）。
- [ ] **Step 3:** 实现：删`msg`；`init`里`traceId`取`TraceContextSnapshot.capture().traceId()`；`timestamp`改为`LocalDateTime.now(ZoneOffset.ofHours(8))`，注解不变；两个`fail`改泛型。
- [ ] **Step 4:** `./gradlew test`。Expected：BUILD SUCCESSFUL，六个用例通过。
- [ ] **Step 5:** 提交`refactor: ApiResponse去掉重复的msg，加traceId并固定GMT+8时间`，交付并读回CI。

### Task 17: utils统一异常处理补齐

**Files:**
- Create: `utils/src/test/java/com/kellen/utils/exception/ApiExceptionHandlerTest.java`
- Modify: `utils/src/main/java/com/kellen/utils/exception/ApiExceptionHandler.java`

**Interfaces:**
- Consumes：任务15的`REQUEST_PARAMETER_ERROR`、`CLIENT_ERROR`、`SYSTEM_EXECUTION_ERROR`、`REQUIRED_PARAMETER_MISSING`、`USER_PASSWORD_ERROR`；任务16的泛型`fail`。
- Produces：`public class ApiExceptionHandler extends ResponseEntityExceptionHandler`，仍不是Bean，由各服务的`@RestControllerAdvice`子类启用。
  - 任务11删除5个异常后剩下的6个处理方法（`UserException`、`BusinessException`、`RpcException`、`SQLException`、`ConstraintViolationException`、`BindException`）的方法名、参数、HTTP 200与错误码不变；删除`handlerMethodArgumentnotvalidException`，改为覆写`protected ResponseEntity<Object> handleMethodArgumentNotValid(MethodArgumentNotValidException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request)`，仍回200、`REQUIRED_PARAMETER_MISSING`与字段提示列表。
  - 覆写`protected ResponseEntity<Object> handleExceptionInternal(Exception ex, Object body, HttpHeaders headers, HttpStatusCode statusCode, WebRequest request)`：先调父类，父类返回`null`（响应已提交）就返回`null`；否则沿用父类的状态码与响应头，报文按规格「异常处理的错误码与提示」表换成`ApiResponse`；4xx记warn，5xx记error。
  - 新增`@ExceptionHandler(Exception.class) public ResponseEntity<Object> unhandledException(Exception e, WebRequest request) throws Exception`：`AccessDeniedException`、`AuthenticationException`原样抛出；其余记带堆栈的error日志，再交给`handleExceptionInternal`按500处理。

测试用`MockMvcBuilders.standaloneSetup(...)`，配测试内的控制器与继承`ApiExceptionHandler`的`@RestControllerAdvice`。控制器提供：`POST /probe/json`（`@RequestBody`）、`GET /probe/param`（必填`@RequestParam q`）、`POST /probe/valid`（`@Valid @RequestBody`，字段`@NotBlank`）、`GET /probe/user`（抛`new UserException(ReturnCode.USER_PASSWORD_ERROR, "x")`）、`GET /probe/boom`（抛`IllegalStateException`）、`GET /probe/denied`（抛`AccessDeniedException`）、`GET /probe/unauthenticated`（抛`BadCredentialsException`）、`GET /probe/partial`（先写出`partial`并`flushBuffer()`，再抛`IllegalStateException`）。

- [ ] **Step 1: 写测试**：
  - `unreadableJsonReturns400WithUnifiedBody`：`POST /probe/json`内容`{bad` → 400，`success`为false，`code`为`A0400`，`errorMessage`为「请求体 JSON 格式不正确」，没有`msg`。
  - `missingParameterReturns400WithUnifiedBody`：`GET /probe/param` → 400、`A0400`、「请求参数不正确」。
  - `unknownPathReturns404WithUnifiedBody`：`GET /probe/none` → 404、`A0001`、「请求的接口不存在」。
  - `unsupportedMethodReturns405WithUnifiedBody`：`DELETE /probe/param` → 405、`A0001`、「请求无法处理」，响应头有`Allow`。
  - `unexpectedExceptionReturns500WithUnifiedBody`：`GET /probe/boom` → 500、`B0001`、「系统处理失败，请查看服务日志」。
  - `committedResponseIsLeftAlone`：`GET /probe/partial`的响应体只有`partial`。
  - `bodyValidationStillReturns200`：`POST /probe/valid`空字段 → 200、`A0410`。
  - `businessExceptionStillReturns200`：`GET /probe/user` → 200、`A0210`、`errorMessage`为`x`。
  - `securityExceptionsAreRethrown`：`/probe/denied`与`/probe/unauthenticated`的请求抛出`ServletException`，cause分别是`AccessDeniedException`与`BadCredentialsException`。
- [ ] **Step 2:** 只跑该测试类。Expected：前六个失败；后三个回归用例在现实现下已通过（没有兜底时异常本来就会抛出），保留它们防止新兜底改坏行为。
- [ ] **Step 3:** 按Interfaces实现。
- [ ] **Step 4:** `./gradlew test`。Expected：BUILD SUCCESSFUL，九个用例通过，现有测试不受影响。
- [ ] **Step 5:** 提交`feat: 统一异常处理补齐Spring MVC标准异常与兜底`，交付并读回CI。

### Task 18: utils修好javadoc

**Files:**
- Modify: `utils/build.gradle`（`failOnError = false`改为`true`）
- Modify: javadoc报错涉及的源文件

- [ ] **Step 1:** `./gradlew javadoc`，记下错误数与文件（第1阶段记录为31个）。
- [ ] **Step 2:** 逐个修正，`failOnError`改为`true`。
- [ ] **Step 3:** `./gradlew javadoc test`。Expected：BUILD SUCCESSFUL，javadoc无error。
- [ ] **Step 4:** 提交`docs: 修复javadoc报错并改为报错即失败`，交付并读回CI。

### Task 19: utils文档、版本1.6.8与发布

**Files:**
- Modify: `utils/build.gradle`（`version = '1.6.8'`）
- Modify: `utils/README.md`（坐标`com:utils:1.6.8`与本版说明）、`utils/docs/ai-coding/UTILS_TOOL_CATALOG.md`（删已删工具，更新`GeneralConvertor`、`OkHttpUtils`、`JsonUtil`、`IpUtils`、`@Phone`、Redis缓存、Redis连接自动配置与锁的回调式用法、数据权限与统一异常处理的说明）
- Modify: `utils/docs/ai-coding/PROJECT_CODING_SPEC.md`（第27行响应字段改为`success`、`code`、`data`、`errorMessage`、`traceId`、`timestamp`；补错误码命名规则：英文大写下划线，编码与文案定下后不改）
- Modify: `utils/AGENTS.md`（写入规格「判定口径」的长期规则；utils没有`UTILS_PUBLIC_SPEC.md`）
- Modify: `utils/src/main/java/com/kellen/entity/EntityBase.java`（只改两处注解说明：`id`为`@Schema(description = "主键ID")`；`version`去掉`hidden = true`，说明为「版本号（乐观锁，更新时回传查询到的值）」）

- [ ] **Step 1:** 改动如上；核对远端tag与私有Maven上没有1.6.8。
- [ ] **Step 2:** `./gradlew clean test javadoc`，加上仓库的密钥扫描脚本。Expected：全过。
- [ ] **Step 3:** 提交`chore: utils 1.6.8`，交付并读回该精确提交CI为success，记下完整SHA（后续服务的CI固定它）。
- [ ] **Step 4:** 在该提交上发布：`python3 ~/.config/kellen-github/observability-20261106/run-maven-release.py <0600日志> :publish`。Expected：BUILD SUCCESSFUL。
- [ ] **Step 5:** `python3 <台账目录>/maven-readback.py utils 1.6.8 .`。Expected：pom、jar、sources、javadoc四件MATCH，坐标`com:utils:1.6.8`，metadata最新为1.6.8。

### Task 20: rag固定POI 5.5.1下的解析输出

**Files:**
- Create: `rag/src/test/java/com/kellen/rag/parser/KnowledgeDocumentParserPoiBaselineTest.java`

测试文件在测试代码里用POI现场生成，不提交二进制文件；解析器的构造方式照现有`KnowledgeDocumentParserTest`。

- [ ] **Step 1: 写测试**（在utils 1.6.7、POI 5.5.1下）：
  - `xlsxFormulaWithCachedValueKeepsBaselineText`：先求值再写出的公式单元格。
  - `xlsxFormulaWithoutCachedValueKeepsBaselineText`：只设公式、不求值。
  - `xlsxDateAndNumberFormatsKeepBaselineText`：日期（含非整秒）、百分比、千分位数字。
  - `docxParagraphsAndTablesKeepBaselineText`：标题、普通段落、表头加两行的表格。
  - `docxInlineContentControlTextIsKept`：段落里一个行内内容控件（`CTSdtRun`）包着的文字出现在输出里。
  - `docxBlockContentControlKeepsBaselineText`：正文里一个块级内容控件（`CTSdtBlock`）。

  每个用例断言切片文本等于在POI 5.5.1下实际得到的值（先运行取值，再写成断言）。
- [ ] **Step 2:** `./gradlew test --tests com.kellen.rag.parser.KnowledgeDocumentParserPoiBaselineTest`。Expected：全部通过。若行内内容控件文字在5.5.1下就缺失，按现状写断言，并在台账记`Ruling:`。
- [ ] **Step 3:** 提交`test: 固定POI 5.5.1下的xlsx与docx解析输出`，交付并读回CI。

### Task 21: rag升级utils 1.6.8与POI 6.0.0

**Files:**
- Modify: `rag/build.gradle`（`utilsVersion = '1.6.8'`；`implementation "org.apache.poi:poi-ooxml:6.0.0"`；`implementation 'org.springframework.boot:spring-boot-starter-websocket'`；`sourceCompatibility`/`targetCompatibility`两行换成`toolchain { languageVersion = JavaLanguageVersion.of(javaReleaseVersion as int) }`；`version = '1.3.1'`）
- Modify: `rag/.github/workflows/ci.yml`（`Checkout utils dependency`步骤的`ref:`改为任务19的完整SHA）
- Modify: `rag/src/main/java/com/kellen/bean/GlobalExceptionHandler.java`（删`unreadableRequestBodyException`与`unhandledException`及不再使用的导入；图谱、403、502三类处理保留）
- Modify: rag主代码与测试里6处错误码引用，按任务15的表改为`REQUEST_PARAMETER_ERROR`、`SYSTEM_EXECUTION_ERROR`、`PERMISSION_DENIED`、`MIDDLEWARE_SERVICE_ERROR`
- Modify: `rag/docs/ai-coding/UTILS_PUBLIC_SPEC.md`（`ReturnCode.成功`改为`ReturnCode.SUCCESS`）
- Modify: `rag/src/main/resources/application.yml`（声明`spring.servlet.multipart.max-file-size: 1024MB`与`max-request-size: 1024MB`，替代utils删掉的`MultipartConfig`，保持现有上限）

- [ ] **Step 1:** `grep -rlE "com\.google\.common|commons\.collections4|com\.aliyuncs|dozermapper|springframework\.amqp|data\.elasticsearch|com\.itextpdf|org\.apache\.pdfbox" rag/src`，再grep规格删除表中的35个类名。Expected：无输出。再`grep -rn '@ExceptionHandler' rag/src/main`，逐条确认改动后子类不再映射`Exception`或`ResponseEntityExceptionHandler`已处理的异常类型。Expected：只剩图谱、403、502三类。
- [ ] **Step 2:** 改动如上。
- [ ] **Step 3:** `./gradlew test`加仓库密钥扫描；`grep -rnE 'ReturnCode\.[^ .(),;:a-zA-Z_]' rag/src`。Expected：全过，任务20的六个基线测试不改断言即通过，`RagControllerSecurityTest`里JSON解析失败（400、A0400）与兜底（500、B0001）的断言不改即通过；grep无输出。基线测试不通过时修改解析器恢复原输出；确属修正原有错误的差异，在台账记`Ruling:`后再改断言。
- [ ] **Step 4:** 提交`build(deps): utils 1.6.8、POI 6.0.0与toolchain，错误码改名（版本1.3.0 → 1.3.1）`，交付并读回CI。

### Task 22: user、message升级

**Files:**
- Modify: `user/build.gradle`、`message/build.gradle`：`utilsVersion = '1.6.8'`，声明`spring-boot-starter-websocket`，改toolchain（同任务21写法），版本user 1.4.2、message 1.2.7
- Modify: 两仓`.github/workflows/ci.yml`的utils检出`ref:`
- Modify: user主代码与测试里57处错误码引用（10个常量，按任务15的表改名）；`user/src/test/java/com/kellen/auth/controller/AuthResourceControllerTest.java`第112、146行的`$.code`改用`getCode()`，第113、147行改为`jsonPath("$.msg").doesNotExist()`
- Modify: 文档：`user/README.md`第432行（删`msg`一行，补`traceId`一行「当前请求的追踪号，没有时不输出」）、`user/docs/ai-coding/UTILS_PUBLIC_SPEC.md`第62行、`user/docs/ai-coding/PROJECT_CODING_SPEC.md`第418、421行；message的`docs/ai-coding/UTILS_PUBLIC_SPEC.md`与`PROJECT_CODING_SPEC.md`里同类的行
- Modify: user涉及Redis的4个测试类，若依赖`ConfigClientController`产出的Bean，按任务14的自动配置调整

- [ ] **Step 1:** 两仓分别做任务21第1步的依赖grep；再`grep -rn '@ExceptionHandler' src/main`。Expected：依赖grep无输出；两仓的`GlobalExceptionHandler`子类没有处理方法。
- [ ] **Step 2:** 改动如上。
- [ ] **Step 3:** 两仓分别`./gradlew test`加密钥扫描；`grep -rnE 'ReturnCode\.[^ .(),;:a-zA-Z_]' src`；`grep -rnE 'ReturnCode\.[^ .(),;:a-zA-Z_]|\bmsg\b' --include='*.md' . | grep -v -E '/archive/|/build/'`。Expected：测试全过；两个grep无输出（文档grep若剩与响应字段无关的`msg`，逐条确认后保留）。
- [ ] **Step 4:** 两仓各提交`build(deps): utils 1.6.8与toolchain，错误码改名（版本…）`，分别交付并读回CI。

### Task 23: ai-memory、report、mcp、ai、gateway升级

**Files:**

| 仓库 | utils | websocket | toolchain | 版本 |
|---|---|---|---|---|
| ai-memory | `utilsVersion = '1.6.8'` | 声明 | 改 | 1.1.8 |
| report | `utilsVersion = '1.6.8'` | 声明 | 改 | 1.2.7 |
| mcp | `utilsVersion = '1.6.8'` | 不需要 | 改 | 1.2.7 |
| ai | `versions.utils`改为1.6.8 | 不需要 | 已是toolchain | 1.7.7 |
| gateway | 不依赖utils | 不需要 | 改 | 1.2.1 |

各仓`.github/workflows/ci.yml`若有utils检出，`ref:`同步更新（gateway的utils检出若无用也不删，只更新`ref:`）。

- ai：主代码与测试里2处错误码引用改为`USER_AUTHENTICATION_FAILED`、`PERMISSION_DENIED`；自有的`AiChatResponse`异常处理不动；`ai/src/test/java/com/ai/TestAiApplication.java`的扫描排除规则去掉已删的类，Redisson自动配置的排除保留。
- mcp：涉及Redis的2个测试类，若依赖`ConfigClientController`产出的Bean，按任务14的自动配置调整。
- 文档：ai的`docs/ai-coding/UTILS_PUBLIC_SPEC.md`第53行；ai-memory、report、mcp的`docs/ai-coding/UTILS_PUBLIC_SPEC.md`与`PROJECT_CODING_SPEC.md`里同类的行。

- [ ] **Step 1:** 依赖utils的四个仓分别做任务21第1步的依赖grep与`grep -rn '@ExceptionHandler' src/main`；ai另外删除`ai/src/test/java/com/ai/TestAiApplication.java`里排除`RequestRequiredAspect`的扫描过滤正则。Expected：依赖grep无输出；ai-memory、report、mcp的`GlobalExceptionHandler`子类没有处理方法，ai只有自己的`com.kellen.exception.ApiExceptionHandler`。
- [ ] **Step 2:** 按上表与上面两条改动。
- [ ] **Step 3:** 各仓`./gradlew test`加密钥扫描；四个依赖utils的仓分别跑任务22第3步的两个grep。Expected：测试全过，grep无输出（文档里与响应字段无关的`msg`逐条确认后保留）。
- [ ] **Step 4:** 各仓提交并交付，逐个读回CI为success。

### Task 24: 整体验证

- [ ] **Step 1:** 确认本机运行的是个人项目的服务。若7100等端口被其他项目占用，先请用户切换，不往其他项目的服务发请求。
- [ ] **Step 2:** 在IDEA启动8个服务与ai-agent。Expected：启动日志无`NoSuchBeanDefinitionException`、`ClassNotFoundException`、`NoClassDefFoundError`；各服务`/actuator/health`为UP。
- [ ] **Step 3:** 经网关调用user的租户、角色、部门、用户、资源、OAuth客户端列表，以及message的消息列表（走Query→实体与实体→VO转换）。Expected：全部200、业务码成功。
- [ ] **Step 4:** 统一存储抽查：经现有服务接口签名上传一个合成文本对象，读回逐字节一致后清理。Expected：上传、读回、清理均成功。另经rag的文档上传接口传一个约2MB的合成文本文档，确认被接受后删除。Expected：上传成功（超过Spring Boot默认的1MB上限），清理成功。
- [ ] **Step 5:** 先在`chain-verify.py`登录之后加两个请求：经网关按user的路由前缀，对一个`@RequestBody`接口发送`{bad`，再请求一个不存在的路径；断言分别为400与404，报文`success`为false、`code`为`A0400`与`A0001`、没有`msg`，报文里的`traceId`与响应头`traceId`一致（两处都没有时记为本机未开启链路）。再运行整链复验：`KW_PROBE_PASSWORD=<用户提供的本机测试口令> python3 -I <台账目录>/chain-verify.py`。Expected：两个错误请求断言通过；登录、message→user、资源、RAG、AI对话全部200，对话无error事件，最后一行`phase 2 chain verification OK`。
- [ ] **Step 6:** 台账记录各项证据（提交、CI、制品读回、启动与接口结果）。

### Task 25: 收尾

- [ ] **Step 1:** 核对规格每一节都有对应证据，台账汇总所有`Ruling:`与推迟的小问题。
- [ ] **Step 2:** 按执行方式做全分支最终复查。
- [ ] **Step 3:** 清理各仓已合入的短分支；用户要求时归档到Wiki。
