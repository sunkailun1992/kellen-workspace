# Dubbo 调用端统一重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全部同步 Dubbo 调用在请求没发出时透明重试，直到成功或这次调用的超时用完；提供方声明了幂等的方法，请求发出后的连接类失败按次数上限、退避与预算重试；去掉 utils 2.1.0 的调用点重试，发 utils 2.1.1；user、ai-memory、rag 声明幂等方法；8 个调用方升级并在本机验证。

**Architecture:** utils 加两个 Dubbo 扩展。协议层 `UnprocessedRequestFilter` 在发请求前查连接：没连上就不发，标记“未发出”，并记下本次所选提供方的幂等声明。集群层 `ConsumerRetryClusterFilter` 包住整次调用：每次尝试都把 Dubbo 自己的重试设成 0，用超时倒计时把剩余时间交给下层，再按分类结果决定是否退避重试；第二层还受次数上限与令牌桶预算约束。分类、预算与退避参数是独立的小类，先写单元测试；过滤器先用进程内的假提供方做组件测试，再用真实 Triple 连接加测试代理做集成测试。

**Tech Stack:** JDK 21、Gradle 9.9.1、Spring Boot 4 / Spring Framework 7.0.10（`org.springframework.util.backoff.ExponentialBackOff`）、Apache Dubbo 3.3.7（Triple）、Micrometer（`Metrics.globalRegistry`）、JUnit 5、AssertJ、Logback `ListAppender`。

**Spec:** `docs/superpowers/specs/2026-11-24-dubbo-consumer-retry-design.md`（维护者 2026-11-24 09:09 批准，e64ef33）

## Global Constraints

- 范围：utils 主模块（包 `com.kellen.config.dubbo`；不动 traffic-canary、fleet-observability 子模块）；user、ai-memory、rag（提供方声明并升 utils）；message、ai、ai-agent、mcp、report（只升 utils）；kellen-workspace（本计划与台账）。不改 Dubbo 源码、网络路径、Nacos 取值与 Dubbo 全局默认值；不动 rpc-api。
- 主流规则（用户 2026-11-20）：实现选择以 GitHub 多个主流项目的多数做法，以及官方文档、RFC、论文为依据；偏离设计或主流做法，要在台账写 `Ruling:` 并附依据。
- 设计定死的值：
  - 退避：首次 100 毫秒，每次翻倍，最长 1 秒，抖动 ±20%。
  - 第二层最多 3 次尝试；第一层不计次数，也不扣、不查预算。
  - 预算：按（调用方应用，提供方服务）各一个令牌桶，初值与上限 10；“发出后暂时失败”扣 1，成功加 0.1；令牌不高于 5 时不做第二层重试。
  - 不重试：超时（错误码 2、8，Triple DEADLINE_EXCEEDED）与业务异常。
  - 只处理同步调用。
  - 幂等参数名 `idempotent`：由提供方声明，调用端的同名参数优先。
- 写计划时定的（执行时台账各记一条 `Ruling:`）：
  - **过滤器顺序：**两个过滤器的顺序都取 `100_000`，在各自链里都是最内层。
    - 协议层：在全部内置调用端过滤器之内（其中最大的是 genericimpl 的 20000）。
    - 集群层：在 consumercontext、metricsConsumerFilter、灰度（-10000）、metricsClusterFilter、monitor、router-snapshot、future 之内。这些过滤器把整个重试循环当成一次调用，与 Dubbo 自带失败转移在集群调用端内部循环的位置一致，FutureFilter 的回调也只触发一次。
  - **退避实现：**用 Spring Framework 7 的 `ExponentialBackOff`，初始 100、倍数 2.0、上限 1000、抖动 20 毫秒。
    - Spring 的抖动随倍数放大，即当前间隔的 ±20%，但结果夹在 [初始, 上限] 内：第一次 100–120 毫秒，到上限后 800–1000 毫秒。
    - 与设计的差别只在两端被夹住。依据 utils 规则“能直接用这些库现成能力的不自己实现”；2.1.0 也用 Spring 的重试。
  - **令牌桶：**照 grpc-java `RetriableStream.Throttle` 写：以千分之一个令牌为单位存在 `AtomicInteger` 里，用 CAS 更新。
  - **截止时间：**
    - 进入集群过滤器时，调用上下文附件里已有 `TimeoutCountDown`（ConsumerContextFilter 从上游请求带进来的倒计时）就沿用；没有就按 `RpcUtils.getTimeout(url, 方法, RpcContext.getClientAttachment(), invocation, CommonConstants.DEFAULT_TIMEOUT)` 新建。这与 `RpcUtils#calculateTimeout` 的取值优先级一致。
    - 倒计时放进附件后，Dubbo 会把剩余时间以 `_TO` 附件发给提供方，这是 Dubbo 超时倒计时的原生行为。
    - 剩余时间不大于本次退避时间就放弃，不睡到截止时间，也不做一次注定没有时间的尝试。这与 Spring Framework 7 `RetryTemplate#checkIfTimeoutExceeded` 一致：已用时间加下一次等待达到时限即中止。
  - **预算计数：**
    - “发出后暂时失败”不论方法是否声明幂等都扣预算，让预算反映这个服务的整体状况（A6 按失败的调用计数）。
    - 成功的调用（含返回业务异常的）都加 0.1。
  - **指标：**照 Envoy 的 `upstream_rq_retry`、`_success`、`_overflow`、`_limit_exceeded`，注册在 `Metrics.globalRegistry` 上（Spring Boot 默认把自动配置的注册表接到全局注册表；utils 已带 actuator 与 Prometheus 注册表）：
    - `dubbo.consumer.retries`：标签 service、method、layer（`unprocessed` 或 `sent`）
    - `dubbo.consumer.retry.recoveries`：标签 service、method
    - `dubbo.consumer.retry.giveups`：标签 service、method、reason（`deadline`、`attempts`、`budget`、`not_idempotent`、`interrupted`、`not_retryable`）
  - **运行时读幂等声明：**集群过滤器第一次看到某个方法的声明，或声明变了，按 INFO 记一行“Dubbo 调用端 {服务}#{方法} 的幂等声明：{值}”。
  - **“转发请求后复位”的集成测试：**由提供方测试桩在执行方法时调用代理的 `resetAll()`。这时请求已到提供方、响应还没写出，比在代理里按字节判断 HTTP/2 帧更确定。
  - **整体复查的时机：**本计划跨 9 个仓库，utils 要先合入并发布，调用方才能固定新提交。所以 utils 分支的整体复查放在合入 utils 之前（任务 7），其余仓库的改动在最后一并复查（任务 10）。
- 版本：推送前按远端 main 顺延，补丁位到 9 进位（x.y.9→x.(y+1).0）。

  | 仓库 | 版本 |
  |---|---|
  | utils | 2.1.0→2.1.1 |
  | user | 1.5.9→1.6.0 |
  | ai-memory | 1.3.6→1.3.7 |
  | rag | 1.4.6→1.4.7 |
  | message | 1.4.8→1.4.9 |
  | ai | 1.9.7→1.9.8 |
  | ai-agent | 0.4.6→0.4.7 |
  | mcp | 1.4.6→1.4.7 |
  | report | 1.3.6→1.3.7 |

  - 各调用方 CI 里固定的 utils 提交从 `a71e571`（2.1.0）改为 2.1.1 的发布提交，注释写 `# com:utils 2.1.1`。
  - utils 版本沿用各仓库的写法：多数是 `utilsVersion`，ai 与 ai-agent 是 `versions.utils`。
- 测试（utils `docs/ai-coding/TESTING_SPEC.md`）：
  - 先写测试，看它失败再实现；结果只由输入决定；网络测试本机连跑三轮；等待用有总时限的条件等待。
  - Dubbo 测试每个用例新建、结束即销毁 `FrameworkModel`；组件测试给调用端 URL `setScopeModel(新应用)`。不碰默认模型，预算不跨用例。
- 日志不记参数、返回值、附件和异常消息全文，失败原因只写异常类名、Dubbo 错误码与 Triple 状态码。对话与台账里不出现 IPv4 地址，也不出现 9 位以上的数字串。
- 代码与文档：Javadoc 和注释用简体中文，照 utils `docs/ai-coding/AI_COMMENT_STYLE_GUIDE.md`；仓库文件里不写个人绝对路径。
- Git：
  - 开工、提交前、推送前都 `git fetch`。
  - 每个仓库从最新 `origin/main` 建短分支 `codex/dubbo-retry`（`--no-track`）。本机验证全过后快进合入 `main`、推送，读回精确提交的 CI，再删短分支。不强推。
  - 提交信息以 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 结尾；提交前跑 `bash scripts/check-secrets.sh`。
- 制品：utils 合入 main 且 CI 全绿后，从 main 用 `~/.config/kellen-github/observability-20261106/run-maven-release.py <日志> <gradle 参数>` 发布到远程私库并读回；不在 main 上时只 `publishToMavenLocal`。
- 运行态：9 个服务由用户在 IDEA 里启停；运行配置不挂 JVM 参数；不用 IDEA MCP 的 `execute_run_configuration` 运行受管配置。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-24-dubbo-consumer-retry/progress.md`。

## Review Focus

1. **嵌套调用的上游超时：**提供方处理一次入站 Dubbo 请求时再调下游，上游带来的超时倒计时比引用的超时短。重试必须在上游剩余时间内停下（任务 3 `upstreamCountdownBoundsTheDeadline`）。
2. **并发更新预算：**多个线程同时对同一服务失败和成功，令牌数不能低于 0 或高于 10，阈值判断不出错（任务 1 `concurrentUpdatesStayWithinBounds`）。
3. **上下文不外泄：**一次调用结束后（成功或失败），同一线程的下一次无关调用不能继承 `retries`、`timeout-countdown` 附件（任务 3 `nothingLeaksIntoTheNextCallOnTheSameThread`）。现在靠最外层的 ConsumerContextFilter 在每次调用结束时清掉客户端附件，这个用例防的是以后过滤器顺序变化。
4. **退避时被中断：**线程在退避等待时被中断，要立即停止重试、保留中断标记，并抛出最后一次失败（任务 3 `interruptDuringBackoffStopsAndKeepsTheFlag`）。
5. **调用端覆盖提供方的声明：**提供方声明幂等、调用端同名参数写 false 时，以调用端为准，不做第二层重试（任务 2 `consumerParameterOverridesProviderDeclaration`，用 Dubbo 3.3.7 的 `InstanceAddressURL` 锁定）。

---

### Task 1: 失败分类、退避参数与重试预算

**Files:**
- Create: `utils/src/main/java/com/kellen/config/dubbo/DubboFailureCategory.java`
- Create: `utils/src/main/java/com/kellen/config/dubbo/DubboFailureClassifier.java`
- Create: `utils/src/main/java/com/kellen/config/dubbo/DubboRetryPolicy.java`
- Create: `utils/src/main/java/com/kellen/config/dubbo/DubboRetryBudget.java`
- Test: `utils/src/test/java/com/kellen/config/dubbo/DubboFailureClassifierTest.java`、`DubboRetryPolicyTest.java`、`DubboRetryBudgetTest.java`

**Interfaces:**
- Produces（都是包内可见）：
  - `enum DubboFailureCategory { UNPROCESSED, NO_WAITABLE_PROVIDER, SENT_TRANSIENT, TIMEOUT, OTHER }`，分别对应设计分类表的“未发出”“无可等待的提供方”“发出后暂时失败”“超时”“其它”。
  - `final class DubboFailureClassifier`：`static DubboFailureCategory classify(RpcException failure, boolean markedNotSent, boolean awaitingRecheck)`
  - `final class DubboRetryPolicy`：
    - 常量 `IDEMPOTENT_KEY = "idempotent"`、`MAX_SENT_ATTEMPTS = 3`、`BUDGET_MAX_TOKENS = 10`、`BUDGET_TOKEN_RATIO = 0.1`、`INITIAL_BACKOFF_MILLIS = 100`、`BACKOFF_MULTIPLIER = 2.0`、`MAX_BACKOFF_MILLIS = 1000`、`BACKOFF_JITTER_MILLIS = 20`
    - `static BackOffExecution startBackOff()`
  - `final class DubboRetryBudget`：构造方法 `DubboRetryBudget(int maxTokens, double tokenRatio)`；方法 `boolean allowsRetry()`、`void recordFailure()`、`void recordSuccess()`、`double tokens()`

- [ ] **Step 1: 建分支。**utils 里 `git fetch`，再 `git switch -c codex/dubbo-retry --no-track origin/main`。Expected：`git status -sb` 显示 `## codex/dubbo-retry`。
- [ ] **Step 2: 写失败测试。**下面的 `wrap(x)` 指 `new RpcException(UNKNOWN_EXCEPTION, "wrapped", new ExecutionException(x))`，与 `AbstractInvoker#waitForResultIfSync` 的包法相同。
  - 分类器，每行一例：
    - `markedNotSentIsUnprocessed`：`classify(new RpcException(NETWORK_EXCEPTION, "x"), true, false)` 为 UNPROCESSED。
    - `noProviderWhileOneAwaitsRecheckIsUnprocessed`：错误码 6、`awaitingRecheck = true`，为 UNPROCESSED。
    - `noProviderWithNothingToAwaitFailsFast`：错误码 6、`awaitingRecheck = false`，为 NO_WAITABLE_PROVIDER。
    - `tripleUnavailableOrCancelledIsSentTransient`：`wrap(TriRpcStatus.UNAVAILABLE.asException())` 与 `wrap(TriRpcStatus.CANCELLED.asException())` 都是 SENT_TRANSIENT。
    - `remotingFailureIsSentTransient`：`new RpcException(NETWORK_EXCEPTION, "x", new RemotingException((InetSocketAddress) null, null, "closed"))` 为 SENT_TRANSIENT。
    - `timeoutsAreNeverRetried`：以下都是 TIMEOUT：
      - 错误码 2；
      - 错误码 8；
      - `wrap(TriRpcStatus.DEADLINE_EXCEEDED.asException())`；
      - 错误码 2、原因链里有 UNAVAILABLE 状态（外层超时优先）；
      - 原因链里有 `org.apache.dubbo.remoting.TimeoutException`。
    - `explicitCodesAreOther`：错误码 3、4、5、7、9、10、11、12、13 都是 OTHER。
    - `noTransportEvidenceIsOther`：不带原因的错误码 1、`wrap(TriRpcStatus.INTERNAL.asException())`、`wrap(new IllegalStateException())` 都是 OTHER。
    - `cyclicOrDeepCauseChainsTerminate`：自引用的原因链与 20 层的链都在 8 层内停下，返回 OTHER。
  - 退避（各取 200 次样本，断言落在范围内）：
    - `firstDelayIsBetween100And120`
    - `delaysDoubleWithin20Percent`：第 2、3、4 次分别落在 [160,240]、[320,480]、[640,960]。
    - `delayStaysWithinTheCap`：第 5 到 10 次都落在 [800,1000]。
  - 预算：
    - `startsFullAndAllowsRetry`：`tokens() == 10`，`allowsRetry()` 为真。
    - `fiveFailuresReachTheThresholdAndStopRetries`：4 次失败后 `allowsRetry()` 为真；第 5 次后为假，`tokens() == 5`。
    - `successAddsATenthUpToTheCap`
    - `tokensNeverGoBelowZero`
    - `concurrentUpdatesStayWithinBounds`：16 个线程各随机记 1000 次失败或成功，之后 0 ≤ `tokens()` ≤ 10。
- [ ] **Step 3: 跑测试，看它失败。**命令：`cd utils && ./gradlew test --tests 'com.kellen.config.dubbo.DubboFailureClassifierTest' --tests 'com.kellen.config.dubbo.DubboRetryPolicyTest' --tests 'com.kellen.config.dubbo.DubboRetryBudgetTest'`。Expected：编译失败，类还不存在。
- [ ] **Step 4: 实现。**
  - 分类器按下面的顺序判，第一条成立就返回：
    1. `markedNotSent` → UNPROCESSED。
    2. 最外层错误码 6 → `awaitingRecheck` 为真是 UNPROCESSED，否则 NO_WAITABLE_PROVIDER。
    3. 最外层错误码 2 或 8 → TIMEOUT。
    4. 最外层错误码不是 0、1 → OTHER。
    5. 从最外层起沿原因链往下找（含最外层自身，最多 8 层，遇到见过的异常就停），取第一个能判定的：
       - remoting 的 `TimeoutException` → TIMEOUT；
       - `StatusRpcException`：DEADLINE_EXCEEDED → TIMEOUT，UNAVAILABLE 或 CANCELLED → SENT_TRANSIENT，其它状态 → OTHER；
       - 其它 `RemotingException` → SENT_TRANSIENT；
       - 错误码不是 0、1 的 `RpcException`：2、8 为 TIMEOUT，其余为 OTHER。
    6. 都没有 → OTHER。
  - `startBackOff()`：用 `new ExponentialBackOff(INITIAL, MULTIPLIER)`，再 `setMaxInterval`、`setJitter`。不设次数与总时长，截止时间由过滤器管。
  - 预算：用以千分之一为单位的 `AtomicInteger`，上限 10000，失败减 1000、成功加 100，阈值 5000；CAS 循环更新。`allowsRetry()` 返回“令牌 > 阈值”。
- [ ] **Step 5: 跑测试，看它通过。**命令同 Step 3。Expected：3 个测试类全部通过。
- [ ] **Step 6: 提交**，提交信息 `feat(dubbo): 调用端重试的失败分类、退避与预算`。

### Task 2: 协议层过滤器：未发出标记与幂等声明

**Files:**
- Create: `utils/src/main/java/com/kellen/config/dubbo/UnprocessedRequestFilter.java`
- Modify: `utils/src/main/resources/META-INF/dubbo/org.apache.dubbo.rpc.Filter`（加一行 `kellenUnprocessedRequest=com.kellen.config.dubbo.UnprocessedRequestFilter`）
- Create: `utils/src/test/java/com/kellen/config/dubbo/RetryFixtures.java`（本任务先放 `FakeProvider`）
- Test: `utils/src/test/java/com/kellen/config/dubbo/UnprocessedRequestFilterTest.java`

**Interfaces:**
- Consumes：`DubboRetryPolicy.IDEMPOTENT_KEY`
- Produces：
  - `public class UnprocessedRequestFilter implements Filter`，`@Activate(group = CONSUMER, order = 100_000)`。
  - 包内常量 `static final Object NOT_SENT`、`static final Object IDEMPOTENT`：`RpcInvocation` 本地属性的键，值是 `Boolean`。
  - 测试辅助 `RetryFixtures.Service`：接口，方法 `String read(String key)`、`String write(String key)`。
  - 测试辅助 `RetryFixtures.FakeProvider implements Invoker<RetryFixtures.Service>`：
    - 构造方法 `FakeProvider(URL url)`；
    - `availableAfter(Duration)`：在给定时长之前 `isAvailable()` 为假；
    - `thenThrow(RpcException...)`、`thenReturn(Function<Invocation, Result>)`：按次序指定每次调用的结果，用完后返回成功；
    - `calls()`：调用次数；
    - `countdownsSeen()`：`List<Long>`，每次被调用时客户端附件里倒计时的剩余毫秒数（没有倒计时记 null）。

- [ ] **Step 1: 写失败测试。**假提供方照 `DubboCanaryRoutingTest.RecordingProvider` 写，用 `DefaultFilterChainBuilder.buildInvokerChain(provider, REFERENCE_FILTER_KEY, CONSUMER)` 包上调用端过滤器链。
  - `unavailableProviderIsNotInvokedAndTheRequestIsMarkedNotSent`：同步调用、提供方不可用时：
    - 抛 `RpcException`，`getCode() == NETWORK_EXCEPTION`，`getCause() == null`，消息含“请求未发出”；
    - `invocation.get(NOT_SENT) == TRUE`；
    - 提供方被调用 0 次。
  - `availableProviderIsInvokedOnceWithItsDeclarationRecorded`：提供方 URL 带 `read.idempotent=true` 时，调用 1 次，`invocation.get(IDEMPOTENT) == TRUE`，`invocation.get(NOT_SENT) == null`。
  - `undeclaredMethodIsRecordedAsNotIdempotent`：URL 不带该参数时 `IDEMPOTENT == FALSE`。
  - `asyncInvocationIsPassedThroughUntouched`：`setInvokeMode(InvokeMode.ASYNC)`、提供方不可用时，照样调用 1 次，不抛“请求未发出”，也不写这两个属性。
  - `filterIsInnermostAmongConsumerFilters`：调用端 URL 带 `generic=true`、`actives=1`、`deprecated=true` 时，`getActivateExtension(url, REFERENCE_FILTER_KEY, CONSUMER)` 返回的最后一个是 `UnprocessedRequestFilter`。
  - `consumerParameterOverridesProviderDeclaration`（Review Focus 5）：用 `MetadataInfo#addService` 与 `DefaultServiceInstance` 建 `InstanceAddressURL`，提供方元数据里放 `read.idempotent=true`：
    - `RpcContext.getServiceContext().setConsumerUrl(...)` 设一个带 `read.idempotent=false` 的调用端 URL 时，`getMethodParameter("read", "idempotent", false)` 为假；
    - 调用端 URL 不带这个参数时为真。
- [ ] **Step 2: 跑测试，看它失败。**命令：`./gradlew test --tests 'com.kellen.config.dubbo.UnprocessedRequestFilterTest'`。Expected：编译失败。
- [ ] **Step 3: 实现。**
  - 判断同步调用用 `RpcUtils.getInvokeMode(invoker.getUrl(), invocation) == InvokeMode.SYNC`。协议层的调用模式要到 `AbstractInvoker#prepareInvocation` 才写入，这个方法在还没写入时按 URL 与返回类型推断。
  - 同步时，先写 `IDEMPOTENT`：取 `invoker.getUrl().getMethodParameter(RpcUtils.getMethodName(invocation), IDEMPOTENT_KEY, false)`。
  - 再查 `invoker.isAvailable()`。不可用就写 `NOT_SENT`，并抛 `new RpcException(NETWORK_EXCEPTION, "请求未发出：提供方 " + 地址 + " 的连接不可用")`。
  - Javadoc 写明为什么用本地属性而不用异常子类：失败转移会重新包装异常，只留错误码和原因链。
- [ ] **Step 4: 跑测试，看它通过。**再跑 `./gradlew test --tests 'com.kellen.config.dubbo.*'`。Expected：全部通过，`DubboCanaryRoutingTest` 不受影响。
- [ ] **Step 5: 提交**，提交信息 `feat(dubbo): 协议层过滤器在连接不可用时不发请求并标记未发出`。

### Task 3: 集群层过滤器：重试循环

**Files:**
- Create: `utils/src/main/java/com/kellen/config/dubbo/ConsumerRetryClusterFilter.java`
- Create: `utils/src/main/resources/META-INF/dubbo/org.apache.dubbo.rpc.cluster.filter.ClusterFilter`（内容 `kellenConsumerRetry=com.kellen.config.dubbo.ConsumerRetryClusterFilter`）
- Modify: `utils/src/test/java/com/kellen/config/dubbo/RetryFixtures.java`（加 `cluster(...)` 与 `EmptyDirectory`）
- Test: `utils/src/test/java/com/kellen/config/dubbo/ConsumerRetryClusterFilterTest.java`（两层与分类）、`ConsumerRetryClusterFilterDeadlineTest.java`（截止时间、中断、上下文）

**Interfaces:**
- Consumes：任务 1 的全部；任务 2 的 `NOT_SENT`、`IDEMPOTENT` 与 `FakeProvider`。
- Produces：
  - `public class ConsumerRetryClusterFilter implements ClusterFilter`，`@Activate(group = CONSUMER, order = 100_000)`。
  - 测试辅助：
    - `RetryFixtures.cluster(ApplicationModel application, URL consumer, Invoker<Service>... providers)`：返回 `Invoker<Service>`。
    - `RetryFixtures.EmptyDirectory`：继承 `AbstractDirectory`，一个提供方都没有（`StaticDirectory` 不接受空列表）。

- [ ] **Step 1: 写测试辅助。**
  - `cluster(...)` 照 `DubboCanaryRoutingTest#directory` 与 `#join` 写：建 `StaticDirectory`，再 `new FailoverCluster().join(directory, true)`，集群过滤器链会带上新过滤器。
  - 每个测试先 `new FrameworkModel().newApplication()`，调用端 URL 用 `setScopeModel` 绑这个应用，`@AfterEach` 里销毁。
- [ ] **Step 2: 写失败测试。**下面的 `UNAVAILABLE_AFTER_SEND` 指 `wrap(TriRpcStatus.UNAVAILABLE.asException())`。调用端 URL 默认 `timeout=3000`、`retries=0`，让 Dubbo 自己的失败转移盖不住要测的行为；只有 `dubboFailoverTriesOnlyOncePerAttempt` 用 Dubbo 默认的 `retries`。
  - 两层与分类（`ConsumerRetryClusterFilterTest`）：
    - `unprocessedCallWaitsUntilTheProviderIsBack`：提供方 300 毫秒内不可用、方法非幂等。结果成功，提供方被调用 1 次。中间会经历“提供方被移出可用列表、等目录复查”，错误码 6 的这一段也要等。
    - `noProviderFailsFast`：用 `EmptyDirectory`。抛错误码 6，用时 < 200 毫秒。
    - `canaryTagWithoutMatchingProviderFailsFast`：照 `DubboCanaryRoutingTest.missingMatchingProviderNeverFallsBackToStable`，绑灰度授权、只有稳定实例。抛错误码 6，用时 < 200 毫秒，稳定实例被调用 0 次。
    - `retriesKeepTheCanaryGrantAndRouting`：灰度实例与稳定实例各一个，灰度实例 300 毫秒内不可用，绑灰度授权后调用。最终由灰度实例返回结果，它收到的授权就是绑定的那个，稳定实例被调用 0 次。
    - `idempotentMethodIsRetriedAfterASentFailure`：URL 带 `read.idempotent=true`，第 1 次抛 `UNAVAILABLE_AFTER_SEND`、第 2 次成功。结果成功，调用 2 次。
    - `undeclaredMethodIsNotRetriedAfterASentFailure`：同上但不带声明。抛出该异常，调用 1 次。
    - `sentRetriesStopAfterThreeAttempts`：方法幂等、每次都抛。调用恰好 3 次。
    - `exhaustedBudgetStopsSentRetries`：对同一服务先调 5 次 `write`（不幂等），每次抛一次 `UNAVAILABLE_AFTER_SEND`，令牌降到 5；第 6 次调 `read`（已声明幂等）也抛一次。`read` 只被调用 1 次就失败，提供方共被调用 6 次。
    - `timeoutIsNotRetried`：抛 `new RpcException(TIMEOUT_EXCEPTION, "x")`。调用 1 次。
    - `businessExceptionIsReturnedAsIs`：返回 `AsyncRpcResult.newDefaultAsyncResult(new IllegalStateException("biz"), invocation)`。结果 `hasException()`，异常就是原对象，调用 1 次。
    - `asyncCallIsPassedThrough`：`setInvokeMode(ASYNC)`，方法已声明幂等，提供方抛 `UNAVAILABLE_AFTER_SEND`。调用 1 次，用时 < 100 毫秒。
    - `dubboFailoverTriesOnlyOncePerAttempt`：URL 不设 `retries`（Dubbo 默认 2）、方法非幂等、抛 `UNAVAILABLE_AFTER_SEND`。调用 1 次，证明每次尝试 Dubbo 自己只试一次。
  - 截止时间、中断与上下文（`ConsumerRetryClusterFilterDeadlineTest`）：
    - `deadlineBoundsTheTotalTime`：URL `timeout=300`、提供方一直不可用。抛出最后一次失败，100 ≤ 用时 ≤ 450 毫秒。
    - `upstreamCountdownBoundsTheDeadline`（Review Focus 1）：URL `timeout=3000`，调用前 `RpcContext.getServerAttachment().setObjectAttachment(TIME_COUNTDOWN_KEY, TimeoutCountDown.newCountDown(200, MILLISECONDS))`（ConsumerContextFilter 会把它带进客户端附件），提供方一直不可用。100 ≤ 用时 ≤ 350 毫秒。`@AfterEach` 里清 `RpcContext`。
    - `eachAttemptGetsTheRemainingTime`：方法已声明幂等，第 1 次抛 `UNAVAILABLE_AFTER_SEND`、第 2 次成功。`countdownsSeen()` 有两个值，都大于 0、小于 3000，且第 2 个小于第 1 个。
    - `backoffNeverOvershootsTheDeadline`：URL `timeout=150`、提供方一直不可用。100 ≤ 用时 ≤ 200 毫秒：睡过第一次退避，但不睡过截止时间。
    - `interruptDuringBackoffStopsAndKeepsTheFlag`（Review Focus 4）：提供方一直不可用、`timeout=3000`，50 毫秒后中断调用线程。50 ≤ 用时 < 300 毫秒，抛出的是最后一次失败，`Thread.interrupted()` 为真。
    - `nothingLeaksIntoTheNextCallOnTheSameThread`（Review Focus 3）：先做一次重试后成功、一次重试后失败的调用，之后 `RpcContext.getClientAttachment()` 里的 `retries`、`timeout-countdown` 都是 null。
- [ ] **Step 3: 跑测试，看它失败。**命令：`./gradlew test --tests 'com.kellen.config.dubbo.ConsumerRetryClusterFilter*Test'`。这时过滤器还不存在，测试照样能编译（过滤器经 SPI 启用）。Expected：
  - 失败：`unprocessedCallWaitsUntilTheProviderIsBack`、`retriesKeepTheCanaryGrantAndRouting`、`idempotentMethodIsRetriedAfterASentFailure`、`sentRetriesStopAfterThreeAttempts`、`dubboFailoverTriesOnlyOncePerAttempt`，以及截止时间组里除 `nothingLeaksIntoTheNextCallOnTheSameThread` 外的全部。
  - 通过：其余用例。它们是防止过滤器多做事的护栏（不该等的不等、不该重试的不重试），实现前后都应通过。
  - 实际结果与此不符，就先查测试，并在台账记一笔。
- [ ] **Step 4: 实现 `invoke`。**下面是测试定不了的处理次序：

```text
非同步（RpcUtils.getInvokeMode(url, invocation) != SYNC）→ 原样下传
截止 = 客户端附件里已有的 TimeoutCountDown；没有就按 RpcUtils.getTimeout(...) 新建（见 Global Constraints）
预算 = budgets.computeIfAbsent(url.getApplication() + "/" + url.getServiceKey(), 新建 10 / 0.1 的桶)
退避 = DubboRetryPolicy.startBackOff()；已发出的失败次数 = 0
循环：
  清掉 invocation 的 NOT_SENT、IDEMPOTENT；客户端附件 retries = 0、timeout-countdown = 截止
  调用成功（没有抛 RpcException，含业务异常结果）→ 预算.recordSuccess()；返回结果
  调用失败 → 类别 = classify(失败, NOT_SENT 为真, 等待复查)
    等待复查 = invoker 是 ClusterInvoker、目录是 AbstractDirectory，
               且 getAllInvokers().size() > getValidInvokers().size()
    UNPROCESSED → 可重试
    SENT_TRANSIENT → 预算.recordFailure()；已发出的失败次数 += 1
                     方法不幂等 → 放弃（not_idempotent）
                     已发出的失败次数达到 MAX_SENT_ATTEMPTS → 放弃（attempts）
                     !预算.allowsRetry() → 放弃（budget）
    其它类别 → 原样抛出（第一次之后才出现的，记为放弃 not_retryable）
    可重试 → 等待 = 退避.nextBackOff()
             截止.timeRemaining(MILLISECONDS) <= 等待 → 放弃（deadline）
             Thread.sleep(等待)；被中断 → 恢复中断标记，放弃（interrupted）
             再来一次
  放弃 → 抛出最后一次失败
```

  放弃原因在本任务只用来决定是否继续，任务 4 再接上指标与日志。
- [ ] **Step 5: 跑测试，看它通过。**再跑 `./gradlew test --tests 'com.kellen.config.dubbo.*' --tests 'com.kellen.security.oauth2.*'`。Expected：全部通过。这时 2.1.0 的转换器重试还在，两层叠加不影响结果。
- [ ] **Step 6: 提交**，提交信息 `feat(dubbo): 集群层过滤器按两层规则重试并守住调用超时`。

### Task 4: 集群层过滤器：指标与日志

**Files:**
- Modify: `utils/src/main/java/com/kellen/config/dubbo/ConsumerRetryClusterFilter.java`
- Test: `utils/src/test/java/com/kellen/config/dubbo/ConsumerRetryClusterFilterObservabilityTest.java`

**Interfaces:**
- Consumes：任务 3 的过滤器、放弃原因与 `RetryFixtures`。
- Produces：指标名与标签见 Global Constraints；日志的 logger 就是这个类。

- [ ] **Step 1: 写失败测试。**
  - 指标（把 `SimpleMeterRegistry` 加进 `Metrics.globalRegistry`，用完移除）：
    - `recoveredCallCountsRetriesAndOneRecovery`：两次未发出后成功，`dubbo.consumer.retries{layer=unprocessed}` 为 2，`dubbo.consumer.retry.recoveries` 为 1。
    - `giveUpsCountTheirReason`：attempts、budget、not_idempotent、deadline 各一例，各自计 1。
  - 日志（Logback `ListAppender`）：
    - `recoveryIsLoggedAtInfoWithoutArguments`：一条 INFO，含服务#方法、尝试次数、用时、第一次失败的类别；不含参数值，也不含异常消息。
    - `giveUpIsLoggedAtWarnWithTheReason`
    - `declarationIsLoggedOncePerMethodAndAgainWhenItChanges`
- [ ] **Step 2: 跑测试，看它失败。**命令：`./gradlew test --tests 'com.kellen.config.dubbo.ConsumerRetryClusterFilterObservabilityTest'`。Expected：新测试全部失败。
- [ ] **Step 3: 实现。**
  - 指标：名字与标签按 Global Constraints，service 取 `url.getServiceInterface()`，method 取 `RpcUtils.getMethodName(invocation)`。每次真的再试一次时计 `retries`，重试后成功计 `recoveries`，放弃时计 `giveups`。
  - 日志：重试后成功记 INFO，放弃记 WARN，每次重试记 DEBUG；失败原因只写异常类名、Dubbo 错误码与 Triple 状态码。
  - 声明日志：用 `ConcurrentHashMap<String, Boolean>` 记每个方法上一次的值，第一次看到或值变了才记 INFO。
- [ ] **Step 4: 跑测试，看它通过。**再跑 `./gradlew test`。Expected：utils 全部测试通过。
- [ ] **Step 5: 提交**，提交信息 `feat(dubbo): 调用端重试的指标与日志`。

### Task 5: 真实 Triple 连接的集成测试

**Files:**
- Move: `utils/src/test/java/com/kellen/security/oauth2/InterruptingTcpProxy.java` → `utils/src/test/java/com/kellen/config/dubbo/InterruptingTcpProxy.java`（类和方法改为 `public`；`FleetSecurityContextDroppedConnectionTest` 改导入）
- Create: `utils/src/test/java/com/kellen/config/dubbo/RetryProbeService.java`：方法 `String read(String key)`、`String write(String key)`、`CompletableFuture<String> readAsync(String key)`
- Create: `utils/src/test/java/com/kellen/config/dubbo/RetryProbeProvider.java`：
  - 按方法记调用次数；
  - `onNextCall(Runnable)`：下一次方法执行时先跑这个回调；
  - key 为 `"boom"` 时抛 `IllegalStateException`，为 `"slow"` 时睡 1500 毫秒。
- Create: `utils/src/test/java/com/kellen/config/dubbo/ConsumerRetryTripleIntegrationTest.java`

**Interfaces:**
- Consumes：任务 2 到 4 的两个过滤器，经 SPI 自动启用；`InterruptingTcpProxy` 的 `start`、`port`、`resetAll`、`acceptedConnections`、`close`。

- [ ] **Step 1: 写测试。**
  - 搭法照 `FleetSecurityContextDroppedConnectionTest`：
    - 独立的 `FrameworkModel`，`dubbo.protocol.triple.rest.enabled=false`，`shutwait=100`；
    - 提供方只绑回环地址，调用端直连代理；
    - 引用 `check=false`、`lazy=true`、`timeout=3000`；
    - `read` 用 `MethodConfig` 参数 `idempotent=true` 声明幂等，`write` 不声明；
    - `@BeforeEach` 里先成功调一次 `write("warm")`。
  - 用例：
    - `undeclaredCallRightAfterAResetWaitsForTheReconnect`：`resetAll()` 后，等 Triple 调用端变为不可用（同 2.1.0 测试的 `awaitUntil`），再调 `write("a")`。结果成功，`acceptedConnections()` 为 2，`write` 共调用 2 次。
    - `callWhileTheProviderIsOutOfTheAvailableListWaitsForTheRecheck`：同 2.1.0 的第二个用例，改用 `write`。
    - `idempotentCallResetAfterTheRequestIsRetried`：`onNextCall(proxy::resetAll)` 后调 `read("a")`。结果成功，`read` 共调用 2 次。
    - `undeclaredCallResetAfterTheRequestFailsOnce`：同上，改用 `write`。抛 `RpcException`，`write` 这次调用 1 次。
    - `timeoutIsNotRetried`：另建一个 `timeout=500` 的引用，调 `read("slow")`。抛错误码 2，用时 < 1000 毫秒，`read` 调用 1 次。
    - `businessExceptionIsNotRetried`：`read("boom")` 抛 `IllegalStateException`，调用 1 次。
    - `asyncCallFailsWithoutWaitingForTheReconnect`：`resetAll()` 并等调用端变为不可用后，`readAsync("a")` 的 future 在 500 毫秒内异常完成。
    - `providerGoneForGoodFailsAtTheDeadline`：另建一个 `timeout=1000` 的引用，`proxy.close()` 后调 `write("a")`。抛 `RpcException`，500 ≤ 用时 ≤ 1150 毫秒。
- [ ] **Step 2: 跑测试。**命令：`./gradlew test --tests 'com.kellen.config.dubbo.ConsumerRetryTripleIntegrationTest'`。Expected：全部通过。
  - 如果“转发请求后复位”的两个用例，失败不是 UNAVAILABLE 或 CANCELLED、被判成了 OTHER：先打出异常链（类名与 Triple 状态码）。
  - 只有能确定是连接或传输层的失败，才给分类器补规则，同时补单元测试，并在台账记 `Ruling:`。
  - 判断不了就不补：把这个用例改为断言实际行为，并在交付说明里写明。
- [ ] **Step 3: 本机连跑三轮。**命令：`for i in 1 2 3; do ./gradlew test --tests 'com.kellen.config.dubbo.*' --tests 'com.kellen.security.oauth2.FleetSecurityContextDroppedConnectionTest' --rerun-tasks > <台账目录>/triple-$i.log 2>&1 || break; done`。Expected：三轮都 BUILD SUCCESSFUL。
- [ ] **Step 4: 提交**，提交信息 `test(dubbo): 真实 Triple 连接下两层重试的集成测试`。

### Task 6: 去掉 2.1.0 的调用点重试，文档与版本

**Files:**
- Delete: `utils/src/main/java/com/kellen/security/oauth2/SecurityContextLookupRetry.java`、`utils/src/test/java/com/kellen/security/oauth2/FleetJwtAuthenticationConverterRetryTest.java`
- Modify: `utils/src/main/java/com/kellen/security/oauth2/FleetJwtAuthenticationConverter.java`：恢复为 2.0.9 的直接调用，即 `git show 499f1b6:<该文件>` 的内容。2.0.9 之后只有 a71e571 改过它。
- Modify: `utils/src/main/java/com/kellen/security/oauth2/FleetSecurityContextDubboAutoConfiguration.java`：`retries = 0` 的说明改为“连接类失败由调用端统一重试过滤器处理，它在每次尝试里也把 Dubbo 的重试设为 0”。
- Modify: `utils/src/test/java/com/kellen/security/oauth2/FleetSecurityContextDroppedConnectionTest.java`：类说明改为“转换器不再重试，由两个过滤器保证”。
- Modify: `utils/build.gradle`：`version = '2.1.1'`。
- Modify: `utils/README.md`：加 2.1.1 说明。
- Modify: `utils/docs/ai-coding/AUTH_SPEC.md`：改写“连接类失败重试（2.1.0 起）”一条。
- Modify: `utils/docs/ai-coding/UTILS_TOOL_CATALOG.md`：登记两个过滤器。
- Modify: `utils/docs/ai-coding/RPC_API_CODING_SPEC.md`：写明提供方怎么声明幂等、什么情况下不能声明、没声明的方法只有第一层保护。

- [ ] **Step 1: 删除并恢复转换器，再证明是过滤器让复现测试通过的。**给 `FleetSecurityContextDroppedConnectionTest` 的引用临时加 `reference.setFilter("-kellenUnprocessedRequest,-kellenConsumerRetry")`（协议层与集群层过滤器都按 `reference.filter` 排除），跑这个测试。Expected：两个用例都失败（返回 503 或不可用）。
- [ ] **Step 2: 去掉临时设置再跑。**命令：`./gradlew test --tests 'com.kellen.security.oauth2.*'`。Expected：全部通过。
- [ ] **Step 3: 改注释与文档，升版本。**README 的 2.1.1 说明要写：
  - 行为变化：全部同步 Dubbo 调用都有两层重试，去掉了业务上下文查询的调用点重试；
  - 接口兼容；
  - 提供方怎么声明幂等；
  - 回滚到 2.1.0 的后果。
- [ ] **Step 4: 全量验证。**依次跑 `./gradlew clean build`、`./gradlew publishToMavenLocal`、`bash scripts/check-secrets.sh`。Expected：全部通过。
- [ ] **Step 5: 提交**，提交信息 `feat(dubbo)!: Dubbo 调用端统一重试，去掉业务上下文查询的调用点重试（2.1.1）`。

### Task 7: utils 整体复查、合入、发布 2.1.1

- [ ] **Step 1: 整体复查。**
  - 按 `superpowers:executing-plans` 的 Final Review，对 `origin/main..codex/dubbo-retry` 打复查包，派最强模型的全新审阅代理；附上本计划的 Review Focus 和台账里的 `Ruling:` 行。
  - Critical 与 Important 问题一轮修完，每条先写复现测试看它失败再修；修完全量测试要全绿。
- [ ] **Step 2: 合入。**
  - `git fetch`，远端前进了就 rebase，并重跑 `./gradlew clean build`。
  - 然后 `git switch main && git merge --ff-only codex/dubbo-retry && git push origin main`。
  - Expected：推送成功。
- [ ] **Step 3: 读回 CI。**用 `gh run list --commit <SHA>` 查到运行完成。Expected：全部 success；红了马上修，修复走同一流程。
- [ ] **Step 4: 发布。**
  - 发布前用 `~/.config/kellen-github/tools/maven-versions.py` 确认远程私库还没有 `com:utils:2.1.1`。
  - 从 main 跑 `run-maven-release.py`，gradle 参数用 `:publish`，只发根项目：traffic-canary（0.1.1）与 fleet-observability（0.1.4）本计划没改、版本不变，不重发，同版重发会得到 409。
  - 用 `~/.config/kellen-github/tools/maven-readback.py` 读回 `com:utils:2.1.1`（含 test-fixtures 分类器），并确认它的 POM 依赖的 traffic-canary 0.1.1、fleet-observability 0.1.4 在远程私库里都在。
  - Expected：制品齐全，jar 与本地构建一致。
  - 删除本地短分支。

### Task 8: 提供方声明幂等并升 utils（user、ai-memory、rag）

**Files（各仓库）:**
- user：Modify `src/main/java/com/kellen/auth/rpc/SecurityContextRpcServiceImpl.java`（`@DubboService(methods = @Method(name = "resolve", parameters = {"idempotent", "true"}))`）、`UserRpcServiceImpl.java`（`getUserById`）
- ai-memory：Modify `src/main/java/com/kellen/memory/rpc/MemoryRetrievalRpcServiceImpl.java`（`retrieve`）
- rag：Modify `src/main/java/com/kellen/rag/rpc/RagRetrievalRpcServiceImpl.java`（`retrieve`）
- 三个仓库都改：`build.gradle`（服务版本与 utils 2.1.1）、`.github/workflows/ci.yml`（utils 固定提交）
- Test：各仓库 `src/test/java/<rpc 实现所在包>/DubboIdempotentDeclarationTest.java`

- [ ] **Step 1: 读实现，核对声明的依据。**对每个要声明的方法，确认它没有改变业务状态的副作用，或者这类副作用本身是幂等的。
  - `resolve`、`getUserById`、rag 的 `retrieve` 应只读。
  - ai-memory 的 `retrieve` 只写审计事件与检索评估两种执行记录。
  - 和设计稿对不上就停下，报告维护者。
- [ ] **Step 2: 每个仓库建分支，写失败测试 `DubboIdempotentDeclarationTest`。**
  - 要声明的类：`@DubboService.methods()` 里恰有一个 `@Method`，其 `name` 是该方法、`parameters` 是 `{"idempotent", "true"}`；并且这个名字是所实现接口里的方法（防拼错）。
  - ai-memory 另加 `otherServicesDeclareNothing`：`MemoryShortMemoryRpcServiceImpl`、`MemoryGovernanceRpcServiceImpl`、`MemoryCandidateRpcServiceImpl` 的 `methods()` 都是空的。
- [ ] **Step 3: 跑测试，看它失败。**Expected：断言失败，还没有声明。
- [ ] **Step 4: 加声明，升版本。**
  - 类的 Javadoc 写一句为什么是幂等的。ai-memory 写：每次执行会写审计事件与检索评估记录，它们是执行记录，按 RFC 9110 §9.2.1 不影响幂等；维护者 2026-11-24 09:09 定。
  - 升服务版本与 utils 版本，改 CI 固定的 utils 提交。
- [ ] **Step 5: 验证。**跑 `./gradlew clean build`（解析 utils 2.1.1）与 `bash scripts/check-secrets.sh`。Expected：都通过。
- [ ] **Step 6: 提交、合入、推送、读回 CI、删分支。**提交信息 `feat(rpc): 声明幂等的 Dubbo 方法，升 utils 2.1.1（<新版本>）`。

### Task 9: 其余调用方升 utils（message、ai、ai-agent、mcp、report）

- [ ] **Step 1:** 每个仓库建分支，改三处：服务版本、utils 版本、CI 固定的 utils 提交，改法照 ai-memory 的 `cae2416`。
- [ ] **Step 2:** 跑 `./gradlew clean build` 与 `bash scripts/check-secrets.sh`。Expected：都通过。
- [ ] **Step 3:** 提交，提交信息 `chore(deps): 升 utils 2.1.1，Dubbo 调用端统一重试（<新版本>）`；然后合入、推送、读回 CI、删分支。

### Task 10: 本机运行验证与收尾复查

- [ ] **Step 1:** 请用户在 IDEA 里用 Debug 重启 9 个服务，等健康检查全部为 UP。
- [ ] **Step 2:** 跑两遍 `python3 ~/.config/kellen-github/tools/run_chain_verify.py`（含经网关的浏览器链路）。Expected：两遍全部通过。
- [ ] **Step 3: 读调用端的“幂等声明”日志。**用 IDEA MCP 读运行输出：
  - message 的 `UserRpcService#getUserById`：全链路里 message→user 那一步会调到；
  - 任一资源服务器的 `SecurityContextRpcService#resolve`；
  - ai-agent 的 `MemoryRetrievalRpcService#retrieve`；
  - ai 的 `RagRetrievalRpcService#retrieve`。
  - Expected：值都是 true，没有“放弃重试”的 WARN。这两遍没调到的方法，如实记为未覆盖，不另造调用。
- [ ] **Step 4: 收尾复查。**按任务 7 Step 1 的做法，把任务 8、9 在 8 个仓库的改动交给全新的审阅代理复查；有 Critical 或 Important 就修。
- [ ] **Step 5: 交付说明。**按 utils `docs/ai-coding/AI_ENGINEERING_GUARDRAILS.md` 写，包括：
  - 风险分级（公共调用链路，高）、完成标准、测试证据、安全检查、风险与回滚；
  - 各仓库的提交与 CI、制品、运行验证结果、未覆盖项；
  - 台账里全部的 `Ruling:`，以及推迟处理的小问题。
