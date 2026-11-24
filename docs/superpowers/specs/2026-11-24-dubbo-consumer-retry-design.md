# Dubbo 调用端统一重试：未发出的请求透明重试，幂等方法按声明重试（设计）

- 日期：2026-11-24（新加坡时间）
- 状态：维护者 2026-11-24 09:09 批准
- 已定：维护者 00:55 选“两层都做”，01:00 选“方案 1：两个过滤器”，09:03 定记忆检索照常声明为幂等
- 前情：C0110（`2026-11-23-c0110-context-lookup-retry-design.md`）。utils 2.1.0 只修了业务上下文查询这一处。

## 背景

Dubbo 3.3.7（Triple 协议，当前最新版）在连接重连窗口里的行为，已从源码与复现测试确认：

- `TripleInvoker#doInvoke` 发现连接没连上时直接返回 UNAVAILABLE，不等重连。
- `NettyConnectionHandler#reconnect` 在连接断开 1 秒后才重连。
- 选路时 `AbstractClusterInvoker` 发现提供方不可用，`AbstractDirectory#addInvalidateInvoker` 把它移出可用列表，每秒复查一次；复查前的请求得到“没有可用提供方”（错误码 6）。实测第一次复查常比重连早几毫秒，要到约 2 秒才恢复。
- 同步调用时，`AbstractInvoker#waitForResultIfSync` 把 Triple 的 UNAVAILABLE（网络错误，错误码 1）重新包成未知错误（错误码 0），原状态只留在异常链里。
- 失败转移 `FailoverClusterInvoker` 对所有非业务失败（含超时）立即重试、没有退避，默认再试 2 次。最后重新包一个异常，只保留错误码与异常链。
- 调用中途连接断开时，在途请求可能挂到超时才失败（C0110 测试代理“悄悄丢弃”模式下观察到）。

服务群里在用的 Dubbo 调用全是读（记忆检索另写执行记录，见“幂等声明”），共 4 条：

| 调用方 → 提供方 | 方法 | 引用设定 | 失败时 |
|---|---|---|---|
| 各资源服务器 → user | `SecurityContextRpcService.resolve` | `retries = 0`，3 秒；2.1.0 起由 `SecurityContextLookupRetry` 退避重试 | 503（C0110） |
| message → user | `UserRpcService.getUserById`（`UserRpcTestController`） | 默认 `retries = 2`，3 秒 | 报错 |
| ai-agent → ai-memory | `MemoryRetrievalRpcService.retrieve` | 默认 `retries = 2`，3 秒 | 静默返回空，对话无记忆 |
| ai → rag | `RagRetrievalRpcService.retrieve` | `retries = 0`，5 秒 | 捕获后处理 |

ai-memory 另外导出的 `MemoryShortMemoryRpcService`、`MemoryGovernanceRpcService`、`MemoryCandidateRpcService` 目前没有调用方，其中含写操作。

## 目标、非目标与反目标

目标：
- 所有同步 Dubbo 调用，请求还没发出就失败时（连接未就绪，或提供方正暂时被移出可用列表、等待复查），自动退避重试，直到成功或这次调用自己的超时用完，不论方法是否幂等。
- 提供方声明为幂等的方法，请求发出后因连接断开或服务端暂不可用而失败时，按 gRFC A6 的重试策略重试，有次数上限、退避与重试预算。
- 全服务群只有这一层重试：去掉 utils 2.1.0 的 `SecurityContextLookupRetry`，并在每次调用里关掉 Dubbo 失败转移的立即重试。
- 重试与放弃都留下日志，事后能看出发生过什么。

非目标：
- 不重试超时，包括“调用中途连接断开、请求挂到超时”的情形。
- 不处理异步调用（InvokeMode 为 ASYNC、FUTURE 时原样放行）。
- 不改 Dubbo 源码，不改网络路径，不改 Nacos 里的取值。

反目标（技术上能跑但算失败）：
- 非幂等方法在“可能已发出”时被重试（重复写）。
- 重试放大故障：服务端过载或大面积失败时还在加量。
- 改变业务语义：提供方抛出的业务异常被重试或被吞掉。
- 两层重试叠加：本机制之外还有别的重试一起生效。

## 方案比较

1. 两个 Dubbo 过滤器（选定）：连接级 `Filter` 在发请求前判断连接是否可用，没连上就不发、标记“未发出”；集群级 `ClusterFilter` 负责整次调用的分类、退避、预算与截止时间。
   - 优点：Dubbo 官方扩展点，对所有调用端自动生效，不用逐个改引用；utils 的上下文透传（`DubboContextPropagationFilter`）与灰度路由（`DubboCanaryClusterFilter`）用的是同一种写法。
   - 缺点：要写两个过滤器与一整套测试。
2. 自定义集群策略：替换 `FailoverClusterInvoker`，在它的循环里加分类、退避与预算。
   - 优点：重试与换提供方在同一个循环里。
   - 缺点：要改全局或每个引用的 `cluster` 配置，要覆写 Dubbo 内部类，升级 Dubbo 时更容易坏；灰度路由的顺序要重新验证。
3. 每个调用点包重试（2.1.0 的做法）：不是全局的，新增调用容易漏，不符合目标。

范围上，维护者选了“两层都做”；只做第一层（不登记幂等）的方案没有采用。

## 设计

### 组成

都在 utils，包 `com.kellen.config.dubbo`，类名为暂定，由实施者定稿。

1. `UnprocessedRequestFilter`：调用端 `Filter`，`@Activate(group = CONSUMER)`。顺序放在最内层，紧挨协议调用端，具体顺序值由实施者按 Dubbo 内置过滤器的位置确定，并用测试锁住。
   - 同步调用时，先问 `invoker.isAvailable()`。不可用就不调用下层：在调用对象的本地属性里记“未发出”（`invocation.put(...)`，只在本进程，不随请求发出），然后抛 `RpcException(NETWORK_EXCEPTION, …)`。这个异常不挂原因，消息写明提供方地址与“请求未发出”。
   - 每次尝试都把本次选中提供方的幂等声明记到本地属性：`invoker.getUrl().getMethodParameter(方法名, "idempotent", false)`。提供方的声明经注册中心元数据传来，调用端的同名参数优先。
   - 用本地属性而不用异常子类的原因：失败转移会重新包装异常，只留错误码与异常链。
2. `ConsumerRetryClusterFilter`：调用端 `ClusterFilter`，`@Activate(group = CONSUMER)`，顺序在灰度路由（`order = -10000`）之内。这样灰度上下文只设一次，每次重试都重新走目录、路由与负载均衡。具体顺序由实施者定，并用测试锁住。
   - 只处理同步调用，异步调用直接下传。
   - 截止时间：本次调用开始时，按 `RpcUtils.getTimeout` 的取值规则算出该方法的超时，得到截止时间。每次尝试前，用 Dubbo 自带的超时倒计时（`TimeoutCountDown`，放在调用上下文附件 `TIME_COUNTDOWN_KEY`）把剩余时间交给下层；退避等待不超过剩余时间。
   - 单层重试：每次尝试前在调用上下文附件里设 `retries = 0`。`FailoverClusterInvoker#calculateInvokeTimes` 读到后即删，所以 Dubbo 本身只试一次。全局默认值与 Nacos 都不改。
   - 每次尝试前清掉上一次留下的本地属性，尝试后按下面的规则分类。

### 失败分类

先看是不是业务结果：`Result.hasException()` 而没有抛 `RpcException` 的，是提供方的业务异常，原样返回，不重试。

抛出的 `RpcException` 沿异常链判断：

| 类别 | 判定 | 处理 |
|---|---|---|
| 未发出 | 本地属性记了“未发出”；或错误码 6（没有可用提供方），且目录里有提供方正暂时被移出可用列表、等待复查（`getAllInvokers()` 比 `getValidInvokers()` 多） | 第一层：任何方法都重试，直到截止时间；不计次数、不扣预算（A6：透明重试不算失败） |
| 无可等待的提供方 | 错误码 6，且目录里一个提供方都没有，或全部可用但路由后为空（如灰度强制标签没有匹配实例，见 `DubboCanaryRoutingTest.missingMatchingProviderNeverFallsBackToStable`） | 不重试，立即失败（同 gRPC：连接已失败时快速失败，只在连接中等待） |
| 发出后暂时失败 | 异常链上的 Triple 状态为 UNAVAILABLE 或 CANCELLED（对端复位），或 Dubbo `RemotingException`（发送中连接关闭）；且最外层错误码不是超时 | 第二层：只有本次尝试的提供方声明了幂等才重试，受次数上限与预算约束 |
| 超时 | 错误码 2 或 8，或异常链上的 Triple 状态为 DEADLINE_EXCEEDED | 不重试 |
| 其它 | 序列化、鉴权、限流、方法不存在、校验等带明确错误码的，以及其它一切 | 不重试 |

“发出后暂时失败”具体对应哪些 Triple 状态，由实施者用测试代理的“请求转发后复位”模式实测锁定，原则不变：只收录能确定是连接或传输层的失败。

### 退避、次数与预算

- 退避：指数退避加抖动，同 A6，首次 100 毫秒、每次翻倍、最长 1 秒、±20% 抖动；两层共用，等待不超过截止时间。
- 第二层次数：最多 3 次尝试（1 次加 2 次重试），在 A6 的 5 次上限内。第一层不计次数，只受截止时间约束。
- 预算：照 A6 的重试限流，按（调用方应用，提供方服务）计一个令牌桶：初值与上限 10，每次第二层失败扣 1，每次成功加 0.1；令牌不高于 5 时不再做第二层重试。第一层不扣也不查预算（请求没到服务端，不会放大负载）。

### 幂等声明

- 由提供方在 `@DubboService` 上用方法参数声明：`methods = @Method(name = "…", parameters = {"idempotent", "true"})`。这是 Dubbo 官方建议在提供方配置调用端属性的做法；gRPC（protobuf 的 `idempotency_level`）、Linkerd（`isRetryable`）也是由服务的拥有者声明。
- 调用端可在 `@DubboReference(methods = @Method(...))` 用同名参数覆盖。直连 URL 的引用拿不到提供方元数据时，按调用端参数，没有就视为不幂等。
- 本次声明：user 的 `SecurityContextRpcService.resolve` 与 `UserRpcService.getUserById`；ai-memory 的 `MemoryRetrievalRpcService.retrieve`；rag 的 `RagRetrievalRpcService.retrieve`。
- 每个方法声明前，实施者要读实现确认它没有改变业务状态的副作用，或这类副作用本身幂等。确认不了的不声明，并写进交付说明。
- 设计时的核对：
  - `resolve`、`getUserById`、rag 的 `retrieve` 只读，没有写操作（设计者核对，审稿代理复核）。
  - ai-memory 的 `retrieve` 在事务里每执行一次，写两条记录，两张表都没有唯一约束：
    - 一条审计事件（检索结果为命中或未命中、条数，挂在调用方、会话、患者等字段下）；
    - 一条 `ai_memory_retrieval_eval` 检索评估记录（查询、命中数、耗时）。
  - 维护者 09:03 定：照常声明，不为这两条记录加去重。依据：两条都是执行记录，不是业务状态；RFC 9110 §9.2.1 明确说，服务端记日志这类副作用不是客户端要求的，不影响方法的安全与幂等。重试时多出的记录，如实反映又执行了一次。
  - 两次执行的记录带同一个 traceId：它是请求对象的字段（ai-agent 从自己收到的对话请求里转填），重试时原样重发。调用方没传时为空，这时可按会话与时间对上。
- 没有声明的方法（含以后新增的写接口）默认不做第二层重试。

### 合并现有重试

- 去掉 utils 2.1.0 的 `SecurityContextLookupRetry` 及 `FleetJwtAuthenticationConverter` 里的 `RetryTemplate`。转换器恢复为直接调用，失败照旧回 503；`FleetSecurityContextDubboAutoConfiguration` 的注释同步。
- 2.1.0 的复现测试 `FleetSecurityContextDroppedConnectionTest` 改为验证新机制：直连 URL、未声明幂等，只走第一层，仍应通过。

### 可观测

- 重试后成功：INFO，写方法、类别、尝试次数、耗时与第一次失败的原因（异常类与 Triple 状态）。
- 放弃：WARN，写同样内容。每次重试的细节记 DEBUG。
- 不记参数、返回值或附件内容。
- 指标（可选）：若能经 Dubbo 的 Spring 注入取到 `MeterRegistry`，按类别计数重试与预算拒绝；取不到就只记日志。由实施者按 fleet-observability 现有写法决定。

### 发布与回滚

- utils 2.1.0 升为 2.1.1：行为变化，接口兼容。合入 main 后发布远程私库，CI 检出提交同步。
- 提供方 user、ai-memory、rag 加幂等声明并各升补丁版本；8 个调用方升 utils。
- 本机重启服务后，跑全链路两遍与真实网关浏览器测试一组。并从调用端读出 4 个方法的 `idempotent` 参数，确认提供方声明经注册中心传到了调用端；读法由实施者定，例如启动时记一行日志。
- 回滚：调用方退回 utils 2.1.0，即回到“只有业务上下文查询重试、Dubbo 默认失败转移”。提供方的声明对旧版调用方无影响。

## 测试

按 utils `docs/ai-coding/TESTING_SPEC.md`：测试先失败再通过；网络测试本机连跑三轮；等待用有总时限的条件等待。

- 集成测试：本进程真实 Triple，加测试 TCP 代理 `InterruptingTcpProxy`；代理新增“转发请求后复位、不回响应”的模式。
  - 非幂等方法，连接复位后马上调用：第一层重试后成功。
  - 提供方被移出可用列表时调用：第一层重试后成功。
  - 目录里没有任何提供方时调用：立即失败，不等到超时。
  - 灰度强制标签没有匹配实例时调用：立即失败，不等到超时。
  - 幂等方法，请求转发后复位：第二层重试后成功，提供方收到 2 次。
  - 非幂等方法，同上：不重试、失败，提供方收到 1 次。
  - 超时：不重试。
  - 业务异常：不重试，原样返回。
  - 预算耗尽后：第二层不再重试。
  - 异步调用：原样放行。
  - 截止时间：总耗时不超过超时加一个小余量。
  - 单层重试：Dubbo 失败转移确实只试一次。
- 单元测试：分类表逐行，以及退避上下限、令牌桶加减与阈值。
- 灰度路由：与 `DubboCanaryRoutingTest` 一起验证，每次重试仍带灰度授权、仍按灰度选路。

## 已知边界

- 调用中途连接断开、在途请求挂到超时的情形，超时按设计不重试，仍会失败。
- 发请求前判断可用之后、真正写出之前连接恰好断开：Triple 会直接回 UNAVAILABLE，这次按“发出后”处理，只有幂等方法会重试。
- 异步调用不在本次范围。
- 多个提供方时，重试会重新选路，可能换到另一个实例，这是期望的行为。
- 目录里暂时被移出的那个提供方，恰好不是路由要的那个（例如另一个稳定实例在重连、灰度实例却根本没起来）：这时仍会等到截止时间才失败。这种情况少见，等待有截止时间封顶。
- 记忆检索走了第二层重试时，若前一次在 ai-memory 已执行完、只是响应没回来，审计事件与检索评估记录会各多一条；每重试一次最多多一组，第二层最多重试 2 次。按维护者决定不去重。

## 留给实施者

- 过滤器的顺序值、类名与日志格式。
- 测试代理新模式的实现。
- “发出后暂时失败”在 Dubbo 3.3.7 下对应的确切状态（以实测为准，原则见分类表）。
- 读出 `idempotent` 参数的运行验证方式。
- 指标是否接入。

## 主流依据

- gRFC A6 Client Retries：透明重试（没离开客户端的请求不论配置都重试、不计次数）；按方法配置的重试策略（UNAVAILABLE 可重试、指数退避、±20% 抖动、最多 5 次）；重试限流令牌桶（`maxTokens`、`tokenRatio`）。https://github.com/grpc/proposal/blob/master/A6-client-retries.md
- gRPC Wait for Ready Semantics：通道连接中时不应让请求失败。https://github.com/grpc/grpc/blob/master/doc/wait-for-ready.md
- protobuf `MethodOptions.idempotency_level`（`NO_SIDE_EFFECTS`、`IDEMPOTENT`）：由服务定义声明方法的幂等性。https://github.com/protocolbuffers/protobuf/blob/main/src/google/protobuf/descriptor.proto
- Finagle：确定没到服务端的失败自动重排（requeue），并与重试共用重试预算（默认约为请求量的 20% 加每秒 10 次）。https://finagle.github.io/blog/2016/02/08/retry-budgets ；https://twitter.github.io/finagle/guide/Clients.html#retries
- Envoy：`connect-failure`、`refused-stream` 属请求未被处理，可安全重试；`reset` 可能已被处理，只适合幂等请求；重试预算。https://www.envoyproxy.io/docs/envoy/v1.11.2/faq/transient_failures
- Linkerd：只重试标为可重试（幂等）的路由，并用重试预算限制重试比例。https://linkerd.io/2.13/tasks/configuring-retries/ ；https://linkerd.io/2019/02/22/how-we-designed-retries-in-linkerd-2-2/
- Armeria：`UnprocessedRequestException` 表示服务端肯定没处理，可安全重试。https://armeria.dev/docs/client-retry
- Apache Dubbo 集群容错：失败转移适用于读操作，非幂等写用快速失败；调用端属性（超时、重试等）建议在提供方配置。https://dubbo.apache.org/en/docs3-v2/java-sdk/advanced-features-and-usage/service/fault-tolerent-strategy/
- RFC 9110 §9.2.2：幂等请求可以在通信失败后自动重试，非幂等的不应自动重试。https://www.rfc-editor.org/rfc/rfc9110#section-9.2.2
- RFC 9110 §9.2.1：服务端对安全请求记日志等副作用，不是客户端要求的，不改变方法的安全性。https://www.rfc-editor.org/rfc/rfc9110#section-9.2.1
- Google SRE 书第 22 章：重试要随机化指数退避、限制次数，只在一层重试，避免放大成级联故障。https://sre.google/sre-book/addressing-cascading-failures/
- AWS Builders' Library「Timeouts, retries, and backoff with jitter」：退避设上限、加抖动，只重试暂时性失败，注意幂等。https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
