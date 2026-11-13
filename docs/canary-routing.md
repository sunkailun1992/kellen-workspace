# Gateway 与 Dubbo 灰度配置与验收

默认关闭。各环境独立密钥，启动参数和 Nacos 只引用密钥管理器注入的 `TRAFFIC_CANARY_SIGNING_KEY`。稳定实例继续用原服务名与无标签 RPC。灰度 HTTP 实例注册到同一 namespace，服务名使用 `<稳定服务名>-canary-<标签>`。灰度 RPC 实例保留同一业务接口，按发布组使用静态标签。

## 入口和下游

先在受信任的入口完成身份认证、灰度名单判断和外部同名头清洗。仅由该入口调用 `CanaryGrant.issue(service, tag, expiresAt, signingKey)` 签发不超过 60 秒的路由凭据，再设置 `X-Traffic-Canary-Grant`。凭据的 `service` 是 Gateway 原路由的服务名。它在有效期内可重用，因此入口与下游之间应使用受保护的传输，且灰度端口限内部调用。业务登录、权限及租户校验按原契约继续执行。

Gateway 例子放在个人环境归属的治理配置中，沿用原 `lb://message` 和 RewritePath。

```yaml
traffic:
  governance:
    canary:
      enabled: true
      signing-key: ${TRAFFIC_CANARY_SIGNING_KEY:}
      routes:
        message:
          blue: message-canary-blue
```

Message 灰度进程配置 `spring.cloud.nacos.discovery.service=message-canary-blue`。其 `traffic.governance.canary` 下设置 `enabled: true`、`service: message`、`http-tag: blue`、`require-grant: true`、`signing-key: ${TRAFFIC_CANARY_SIGNING_KEY:}`。这些值只给灰度部署实例，稳定实例使用默认配置。敏感接口应限制灰度端口网络可达范围。

调用方通过 `@DubboReference(parameters = {"traffic.canary.enabled", "true"})` 显式启用参与灰度的引用。保持 registry-backed，禁止配置 Dubbo mock、injvm 或业务回落。User 灰度进程配置 `dubbo.provider.tag=blue`，启用同一密钥并要求凭据。为指定 RPC 接口配置允许签发时的入口服务名。

```yaml
traffic:
  governance:
    canary:
      enabled: true
      require-grant: true
      signing-key: ${TRAFFIC_CANARY_SIGNING_KEY:}
      rpc-sources:
        "[com.kellen.rpc.user.UserRpcService]":
          - message
```

个人 Java fleet 以 Message 的既有受权限控制的 User RPC 测试入口为接入点。对应配置换成 `message` 和 `[com.kellen.rpc.user.UserRpcService]`。个人 Report 仍可走 HTTP 灰度服务发现，原项目没有 Report → User RPC 调用。

## 验收与回滚

- 普通请求命中原服务，裸路由头不会改变目标。伪造、重复、过期或范围错误的凭据应返回 403。原业务认证头保留。
- 合法凭据命中映射的 Nacos 服务名，匹配 Dubbo 静态标签。HTTP 无实例返回 503。Dubbo 无匹配 provider 或发生动态规则冲突时不会向稳定端发送 RPC。
- 先停止签发灰度凭据并等待存量结束，再摘除灰度服务注册。确认普通调用走原目标之后，关闭灰度映射。启动灰度前检查全部 HTTP 路由、Dubbo 订阅、后台任务、数据库迁移、消息消费和数据兼容性。
- 个人链路关联在 Jaeger 原生搜索用 `traffic.route.mode=canary` 和 `traffic.route.decision=unavailable`。Grafana Explore 选个人 Elasticsearch 数据源，用 `event:"traffic.route.decision" AND traffic_route_mode:"canary"`。精确对照失败路径用 `trace_id:"<TRACE_ID>"`。日志字段为有限枚举，凭据、身份、URL 和地址不入事件。
