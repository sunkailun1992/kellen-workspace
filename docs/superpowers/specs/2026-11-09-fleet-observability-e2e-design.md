# 个人服务全链路与输入输出日志设计

## 授权与目标

用户要求完成真实跨服务链验收、验证每次请求均可查到完整链路，并按主流项目规范完善请求输入与响应输出日志。AI 默认记录摘要、编排步骤和供应商报告用量；正文按需启用脱敏限长诊断。继续使用已选 OpenTelemetry、Collector、Jaeger、Prometheus、Grafana、Elasticsearch，不增加 AI 观测中间件。现有执行授权涵盖实施、验证与此前要求的提交推送；本文记录实现判断，不新增重复审批。

## 依据与方案

- 参考 Zalando Logbook 的条件、过滤、格式、写入分层及敏感字段、流式体处理；不直接套用会完整缓存响应体的默认实现。
- 参考 Spring AI 的内容采集默认关闭与 OTel GenAI 属性合同。
- 不复制三套脱敏源码，也不给 gateway/ai-agent 引入完整 utils。在 utils 仓库增加轻量 Gradle 子模块 `fleet-observability`，坐标 `com:fleet-observability:0.1.0`，供 utils、gateway、ai-agent 共同使用。
- 此库提供统一安全事件、有限 JSON 投影、Servlet 与 WebFlux 适配，自动配置按实际 Web 类型隔离。运行依赖只含轻量 JSON、SLF4J、OTel API；Spring/Servlet/Reactor 由消费应用提供，不引入数据库、Redis或Seata。

## HTTP 日志合同

每请求一个 `event=http.server.exchange` 终态事件。`summary` 由固定模板、方法、路由模板、状态和耗时生成，作为 Grafana 的可读行；不启用任意业务 logger 的原始 message。

字段：`summary,event,http_method,http_route,http_status,duration_ms,outcome,request_bytes,response_bytes,request_body,response_body,request_body_omitted,response_body_omitted,error_type,error_code,trace_id,span_id`。

类型合同：summary/event/method/route/error与省略原因均为有界string；status为100–599整数，未能确定时缺失；duration_ms为非负毫秒数；字节计数为实际被应用读取/成功写出的非负long，不采用Content-Length冒充实读量。增加`request_body_complete/response_body_complete`布尔表示是否完整；未读完、取消或写出失败不得标complete。body为安全投影后的JSON字符串，绝非任意对象；缺失字段不输出null或假0。outcome只取`success/failure/cancelled/timeout/rejected`，省略原因只取`policy/stream/binary/multipart/too_large/invalid_json/invalid_encoding/incomplete/not_consumed/empty`。

首批实际路由策略（均使用框架匹配模板，不能使用客户端提供的策略名）：

| 路由 | 输入投影 | 输出投影 |
|---|---|---|
| POST `/api/rag/retrievals` | topK整数1–100、similarityThreshold数值0–1、maxContextCharacters整数1–65536、collectionName仅有限ASCII标识；query/conversationId正文排除 | envelope code/success、data.enabled/data.matched布尔、data.sourceCount非负整数；context/chunks/message排除 |
| GET `/api/ai/model-selection` | 无正文，明确empty | provider/model有限ASCII模型标识、supportedProviders有限枚举列表；更新时间与其他键排除 |
| GET `/api/agents/model` | 无正文，不记录签名或query身份 | 同上provider/model的安全标识字段；未知键排除 |
| 其他普通JSON路由 | 默认仅安全envelope/分页控制字段，未知业务键省略；新增具体业务字段必须声明类型/边界并加测试 | 仅code/success与分页计数，未知业务data和message省略 |
| AI chat/patient/questionnaire路由 | HTTP层不抓prompt/患者/media正文，只类型、字节数与省略原因 | SSE不抓正文，AI摘要由模型边界事件生成 |

code仅允许有限格式的业务码或整数，不允许把任意错误文字作为code导出。AI摘要只从操作、模型、Prompt key/version、消息/文档/字符数和finish reason生成，不能直接引用模型文本前缀。每类验收必须同时证明一个合成允许值保留及一个合成秘密/未知字段被删除。

- 真实 trace/span 从 OTel 上下文捕获；业务 DTO 的 traceId、conversationId、用户 MDC 不能冒充观测身份。
- HTTP 方法受控；路由使用框架匹配模板，未匹配记 `UNMATCHED`，不存原始 URL、query、IP或身份。
- body 捕获上限各 8192 字节，在业务实际读取/写入时有界复制。超限、未知编码、无效/过深/重复键 JSON 整体省略，不记录原始前缀。
- JSON 使用路由与字段类型白名单投影，未知键删除，密码、令牌、签名、Cookie、个人信息和自由正文默认排除。普通参数和响应状态可安全展示；AI路由默认摘要，不能由普通 HTTP 日志旁路记录 prompt。
- SSE、multipart、文件与二进制只记大小、类型与终态，始终透传，不聚合完整响应，不改变背压、flush和取消行为。
- Servlet async complete/error/timeout/redispatch 与 WebFlux complete/error/cancel 各保证一次终态。日志异常不能改变业务结果。
- 异常记录类型和受控错误码，不输出 Throwable message、堆栈、绑定原值或服务商错误原文。
- 旧 RequestRequiredAspect 的原文审计和慢请求外发旁路使用同一安全策略收敛，不删除历史索引。

## AI 合同

模型 span 保留 `gen_ai.operation.name,gen_ai.provider.name,gen_ai.request.model,gen_ai.response.model,gen_ai.response.finish_reasons,gen_ai.usage.input_tokens,gen_ai.usage.output_tokens,gen_ai.usage.reasoning.output_tokens`，以及有限 `fleet.ai.*` Prompt来源/版本、用量可用性、公开推理输出/摘要存在性。

对应日志字段：`ai_operation,ai_provider,ai_model,ai_prompt_key,ai_prompt_version,ai_input_tokens,ai_output_tokens,ai_reasoning_tokens,ai_usage_available,ai_reasoning_kind,ai_reasoning_present,ai_input_summary,ai_output_summary,ai_input,ai_output`。AI事件 `event=ai.model.call`，`summary` 为安全摘要。

AI日志继承相同真实trace_id/span_id；token是非负long，可用性/存在性为boolean；provider/operation/reasoning_kind为有限枚举，模型/Prompt标识为有界ASCII字符串。摘要为代码构造的有界string。ai_input/ai_output只在受信开关启用且安全投影成功时输出有界string，默认缺失。

- 保留业务响应，通过 ChatResponse 提取元数据；流式累计 usage 只取最终供应商报告值，不逐片相加。未报告不伪造0。
- reasoning token 是输出 token 子集，不重复累加；记录供应商公开字段的来源与类型。不透明 thought signatures 不记录，不声称能取得未公开的内部思维。
- 模型调用、RAG和memory步骤使用真实父子 span。当前固定编排不伪装成自主 tool calling；只有真实工具执行才记 execute_tool。
- AI正文默认关闭。受信服务器配置启用时仍按独立策略脱敏、限长；不受客户端请求头打开，不写聊天、Wiki或验收报告。
- Prompt正文与已解析版本应来自同一快照；版本不可得或调用方覆盖时明确标识，不能从正文猜测。

## 采样、传输与展示

- 个人 github-dev 的 Java 与 Engine 使用全量记录策略，包括入站 sampled=0 的测试请求；保持 W3C tracecontext 传播。独立异步根 trace 同样记录。
- 100%采样不等于崩溃、断网或存储丢失时的永久保证。验收以发出的请求清单、查询回来的 Trace和日志逐项对账，不能以 exporter 200代替持久化读回。
- 精确更新 Boot ECS、本机Collector、中央Collector、ES additive mapping、Grafana行显示与关联字段。保留未知属性与原文的清理，不放开任意 MDC/原始message。
- Compose继续完整内联交付；如需更新只改必要观测配置及相应Engine版本/采样，不重传文档、不重建图谱。

## 验收

先按真实源码拆分存在的链：gateway→后端、message→user Dubbo、ai→rag→Engine、ai→ai-agent→ai-memory；不硬造不存在的调用。

使用专用合成输入与既有知识库，认证来自既有授权私有配置。AI会话/记忆如需合成测试数据，记录精确范围与可恢复清理；不调用真实患者来源、不导出数据库、不记录正文。每条链必须同时核业务结果、跨服务同Trace与父子连接、终态日志、服务/版本/环境及脱敏边界。

正常、拒绝、异常、取消、并发、上游不采样、导出恢复分别按有界请求数对账。HTTP与AI内容、秘密与未知字段使用合成哨兵测试。工具/模型步骤按实际支持范围报告，不以单服务探针代表整链。

## 参考

- https://github.com/zalando/logbook
- https://docs.spring.io/spring-ai/reference/observability/index.html
- https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
