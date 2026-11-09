# 个人服务全链路与输入输出日志实施计划

**目标：** 完成真实服务链、全量开发环境请求跟踪及安全I/O日志，并通过ES/Jaeger实际持久化读回验收。

**设计：** `../specs/2026-11-09-fleet-observability-e2e-design.md`。

**执行：** 当前会话持续实施，按独立代码归属并行，完成后独立复核。所有仓库先fetch并保留其他dirty，改动在短分支，最终按已有授权提交推送main并核CI。

## 全局约束

- 仅个人GitHub与个人NAS；不改公司/Yudao。
- 默认HTTP采集只接受路由/类型白名单、每体8192字节，AI正文默认关闭。
- 采样需要真实验证上游sampled=0；不能只把parentbased比例改1。
- 终态日志不改变HTTP/SSE字节、flush、背压和异常语义；每请求一次。
- 新字段必须贯穿真实Boot ECS→两级Collector→ES→Grafana，原始message/异常/未知MDC仍禁止。
- IDEA继续原生Spring Boot入口，Gradle仅用于编译、测试、打包；不增加Gradle启动或Before Run。
- 凭据、真实请求/模型正文、身份及数据库导出不入聊天、Git、Wiki或回执。
- 不重建图谱、不重传文档；发布、配置与现状分别验证。

## 审查重点

1. 小体/大体/无效JSON/重复键/未知字段/嵌套秘密均安全投影。
2. 异步complete/error/timeout/cancel只记录一次且不串trace。
3. SSE首片延迟和单次订阅不因日志改变。
4. usage缺失与0分开，累计片段不重复统计，reasoning为输出子集。
5. 日志或Trace导出失败不能伪装持久化成功，旧隐私旁路不保留。

## 任务与进度

- [x] **T1 共享日志运行库与MVC接入（utils owner）**：新增`utils/fleet-observability`轻量子模块、共享事件/投影/Servlet/WebFlux自动配置及真实HTTP测试；utils依赖它并收敛旧Aspect/响应Trace头。根utils与子模块构建/发布保持独立坐标。
- [x] **T2 AI语义观测（AI owner）**：修改ai/ai-agent的编排span、模型ChatResponse元数据、Prompt实际版本快照与安全摘要；Engine按必要阶段补元数据；合成SDK与流式取消/并发测试先红后绿，不改变业务文本/模型选择。
- [x] **T3 全量采样与出站合同（root）**：workspace私有配置准备、隐私扩展、Boot字段白名单、两级Collector、ES additive schema、Grafana summary与trace关联同步；原生OTLP fixture证明字段保留与秘密清除。
- [x] **T4 轻量消费者接入（root）**：gateway/ai-agent依赖轻量artifact，7个utils消费者更新精确版本，各自补丁版本按远端顺延；统一实际IDE原生运行/服务器Jar路径。
- [ ] **T5 可重复真实验收（acceptance owner）**：新增合成输入/私有认证引用/请求清单、Trace父子与ES join对账工具；先只读发现权限及合法业务入口，准备具体可复核请求，再实际执行。
- [ ] **T6 运行应用与独立复核（root）**：发布必要Maven/Engine制品，受控更新私有运行配置与NAS内联Compose，实际应用；每链正常/拒绝/异常/取消/并发有界验证；不以模拟代真实。
- [ ] **T7 完成交付（root）**：独立代码与证据复核、fresh fetch、测试、main提交推送与CI、短分支和自建临时诊断清理；交付完整Compose（如有变更）、查询示例和有边界的验收回执。

## 已知证据与决定

- 2026-11-09 16:46已核12/12采集目标UP；现有9/9单服务Trace不代替跨服务E2E。
- 用户确认AI默认摘要、工具/编排步骤和用量，按需启用脱敏限长正文。
- 当前AI是固定RAG/记忆编排；暂无自主ToolCallback，不新增无业务用途的工具。
- 采用统一轻量子模块避免三份脱敏策略和gateway引入完整utils；这是实施判断，不新增服务/数据库。
- 认证缺口先查既有私有引用，不能将未创建的bootstrap候选账号当真实登录凭据。


## 本轮实现验证进展

- T1共享库以真实Servlet/Reactive HTTP与生命周期最小复现校验错误、取消、并发、reset及有界I/O；独立Review发现的三处终态边界、标识符URL投影均先红后绿，精确Maven Local实物已供消费者构建。
- T2 Java的Prompt快照、整轮编排/模型child、真实供应商usage及异步审计上下文回归已过。AI正文关闭；不会把metadata诊断API描述为自由正文脱敏已支持。当前固定编排没有自主tool calling。
- T3固定真实agent验证incoming sampled=0、原OTLP metadata正向保留与秘密/events清除；固定Collector四段原生回放通过。独立恶意typed fixture发现并修复base5字段绕过，10项非法字段清除、14项合法字段保留。filelog持久化offset恢复在补验，不增加新服务。
- T4七个消费者最新轻库再次构建测试300项通过，逐包核对版本、恰一份轻库及gateway无完整utils。workspace11 included builds保留，轻库明确source substitution；隔离真实Gradle先红后绿。
- T5合法测试会话已通过正常API登录取得，权限只读核对成功；既有KB摘要为1个ACTIVE文档/866来源，未上传或重建。模型选择空model定位为本环境发现/注册两个enabled关闭，已权威备份/CAS开启并读回；当前旧JVM尚未应用，不能把HTTP200 fallback算成功。
- T6尚需必要制品发布、运行配置应用和新JVM的N/N请求对账；此前存活/单服务Trace或本机测试均不能代替。
