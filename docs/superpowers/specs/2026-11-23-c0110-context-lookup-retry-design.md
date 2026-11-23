# C0110：业务上下文查询的连接类失败重试（设计）

- 日期：2026-11-23（新加坡时间）
- 状态：维护者 22:30 选“按机制修”（先按已查清的机制修，不再等现场复现）
- 范围：utils 2.1.0；消费方 user、message、ai、ai-memory、ai-agent、rag、mcp、report 只升 utils 版本

## 背景与证据

- 现象：11-22 共 6 次，资源服务器（多见于消息服务）空闲后第一轮请求回 503 C0110「用户安全上下文暂不可用」，首轮各约 86 毫秒失败，约 1 秒后自恢复。
- 11-23 的排查：
  - 两个服务 Debug 启动、6 个不暂停的异常日志断点；空闲 2 至 30 分钟的 6 次探测（全链路 2 次、真实网关浏览器测试 4 次）都没复现，message 到 user 的 Triple 连接全程未断、每分钟一次 PING/ACK；本机设置为不睡眠。
  - NAS 上 Nacos 的连接日志：11-22 的 6 次都在 03:08 至 11:45 同一批进程里（11-21 22:58 九个服务一起启动，03:08 是 message 第一次调用 user、建立 Dubbo 引用），这段时间注册中心没有任何断开或重新注册；之后 4 次重启都没再出现。排除“提供方重启”“实例变动”两种解释。
  - 当时是什么断开了连接没能确认：属于本机两个服务之间的网络路径，按维护者规则不查。
- 机制（Dubbo 3.3.7 源码，2026-10-26 发布、当前最新版）：
  - `TripleInvoker#doInvoke` 在连接没连上时直接返回 UNAVAILABLE，不等重连。
  - `NettyConnectionHandler#reconnect` 在连接断开后固定 1 秒才重连。
  - `AbstractClusterInvoker` 选路发现提供方不可用时，`AbstractDirectory#addInvalidateInvoker` 把它移出可用列表并每秒复查一次，复查前的请求报“没有可用提供方”。实测第一次复查常比重连早几毫秒，要到第 2 秒才恢复。
  - 同步调用时 `AbstractInvoker#waitForResultIfSync` 把 Triple 的 UNAVAILABLE（网络错误码）重新包成未知错误码，原状态只在异常链里。
  - 与 11-22 的“首轮约 86 毫秒全部失败、约 1 秒后恢复”吻合。

## 目标与非目标

- 目标：业务上下文查询遇到请求还没到 user 的连接类失败时，不立即回 503，在原单次 3 秒的预算内等重连恢复；其余失败的行为不变。
- 非目标：不改 Dubbo；不改其它 Dubbo 调用（见“后续”）；不查网络路径；不改接口、配置与 Dubbo 引用参数。

## 方案比较

1. 在调用点按 gRFC A6 的按方法重试策略重试（选定）：只重试连接类失败，指数退避加抖动，用 Spring Framework 7 自带的 `RetryTemplate`，不加依赖。业务上下文查询是只读的，且重试的两类失败都没到提供方。
2. 打开 Dubbo 的失败转移 `retries`：立即重试、没有退避，只有一个提供方时 1 至 2 秒的重连窗口内每次都落空。不采用。
3. 写一个全局的 Dubbo 消费端过滤器，做透明重试或 wait-for-ready（gRPC 的默认语义）：能覆盖所有 Dubbo 调用，但要可靠区分“请求未发出”与“已发出”，还要处理非幂等方法。
   这次没做。与选定方案的差别和代价：选定方案只保护业务上下文查询（即 C0110 这条路径），其它 Dubbo 调用（如 message→user 的 `UserRpcService`）遇到同样的重连窗口仍会立即失败，以各自的错误码出现。全局方案要另写过滤器、改动所有消费方的调用行为，还要逐个核对方法是否幂等。是否做由维护者决定，见“后续”。
4. 缩短心跳：只能缩小窗口，不能消除，而且断开不是空闲本身造成的。不单独采用。

## 设计

- 位置：utils `FleetJwtAuthenticationConverter` 在缓存未命中、调用 `SecurityContextRpcService.resolve` 时套 `RetryTemplate`；判断与参数集中在新类 `SecurityContextLookupRetry`。
- 可重试：沿异常链找 Dubbo `RpcException`，遇到网络错误（Triple 的 UNAVAILABLE）或没有可用提供方即重试；遇到其它明确错误码（超时、业务、限流、序列化、鉴权等）即停，不重试。提供方应用自己抛出的异常也不重试。
- 参数：最多重试 4 次（共 5 次，A6 的次数上限）；首次等 250 毫秒、每次翻倍、最长 1 秒；抖动 50 毫秒且随倍数放大，即每次约 ±20%（同 A6）。累计等待 2.25 至 2.9 秒，盖住约 2 秒的恢复，又不超过原单次 3 秒超时。超时不重试，因为单次已用掉 3 秒，再试会放大延迟与负载（SRE 书第 22 章）。
- 可选依赖：Dubbo 判断放在只在类路径上有 Dubbo 时才加载的内部类里（`ClassUtils.isPresent` 守卫，Spring 处理可选依赖的常规写法）。
- 记录：重试后成功记一行 INFO（重试次数与第一次失败的原因），给以后留下“断连确实发生过”的证据；重试用尽时，503 的 WARN 行写明共尝试几次和最后一次的原因。
- Dubbo 引用仍是 `retries = 0`，注释写明原因。

## 已知边界

- 调用端恰好在发现断开的同一时刻发出的请求，可能卡在已关闭的连接上，等满 3 秒超时（测试代理“悄悄丢弃”模式下观察到）。超时按设计不重试，这种请求仍会回 503。这不是 11-22 那种约 86 毫秒的快速失败；该测试依赖调用端何时写流控帧，结果随时序变化，没有收进测试集。

## 测试

- `FleetSecurityContextDroppedConnectionTest`：本进程起真实的 Triple 提供方与调用端，中间放测试用 TCP 代理（`InterruptingTcpProxy`），两端立即复位以模拟连接被断开，覆盖两条路径：连接不可用（UNAVAILABLE）、提供方已被移出可用列表。改前两例都得到 C0110，改后通过；本机连续三轮通过。
- `FleetJwtAuthenticationConverterRetryTest`：等待改成 1 毫秒，结果只由失败序列决定。覆盖：两类连接类失败会重试；超时、业务、限流、未知错误、超时里裹着网络错误、提供方应用异常不重试；用尽后保留最后一次原因；默认策略的累计等待在 2.25 至 2.9 秒之间。临时改错三次（全不重试、全部重试、不在明确错误码处停），分别有 3、6、1 个用例失败。

## 发布

- utils 2.1.0 合入 main 后发布远程 Maven；8 个消费方升 utils 版本、CI 固定提交与各自补丁版本，编译与测试通过后合入并读回 CI。
- 本机重启服务后跑全链路两遍（带与不带灰度授权）与真实网关浏览器测试一组。
- 回滚：消费方退回 utils 2.0.9 即可，没有数据或配置变化。

## 主流依据

- gRFC A6 Client Retries：按方法配置重试策略，UNAVAILABLE 为典型可重试状态，指数退避、±20% 抖动、最多 5 次；没离开客户端的请求可透明重试。https://github.com/grpc/proposal/blob/master/A6-client-retries.md
- gRPC Wait for Ready Semantics：通道处于 CONNECTING、IDLE 时不应让请求失败。https://github.com/grpc/grpc/blob/master/doc/wait-for-ready.md
- RFC 9110 §9.2.2：幂等请求在通信失败时可以自动重试，非幂等的不应自动重试。https://www.rfc-editor.org/rfc/rfc9110#section-9.2.2
- AWS Builders' Library「Timeouts, retries, and backoff with jitter」：退避设上限、加抖动，只重试暂时性失败。https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- Google SRE 书第 22 章与附录 B：重试要随机化指数退避、限制次数，避免重试放大成级联故障。https://sre.google/sre-book/addressing-cascading-failures/
- Spring Framework 7.0 Resilience Features（`RetryTemplate`、`RetryPolicy`、抖动随倍数放大）。https://docs.spring.io/spring-framework/reference/7.0/core/resilience.html
- 主流客户端的同类做法：OkHttp 默认 `retryOnConnectionFailure`，从失效的池化连接上恢复；Apache HttpClient 5 的 `DefaultHttpRequestRetryStrategy` 对幂等请求的 I/O 失败重试。
- 上游相关问题：apache/dubbo#13591（Triple 连接共享的并发问题，未关闭）、#16558（Triple 服务端不清理半开连接，未关闭）。

## 后续

- 其它 Dubbo 调用（message→user 的 `UserRpcService` 等）同样暴露在连接重连窗口里，可另立任务，评估全局消费端透明重试（只重试未发出的请求）或按方法登记幂等调用。
