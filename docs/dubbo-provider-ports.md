# 个人 Dubbo 提供方固定端口

个人九服务中，`user` 有三个 `@DubboService` 实现，`rag` 有一个，`ai-memory` 有四个；其余六服务不提供 RPC。三个提供方在同一主机运行时，共用准备入口采用以下固定 Tri 端口：

| 服务 | RPC 端口 |
| --- | --- |
| user | 27500 |
| rag | 27200 |
| ai-memory | 27301 |

Dubbo 3.3.6 对 `port<=0` 的自动选取先探测 socket，关闭后再启动实际协议监听，选中端口仅在当前 JVM 内缓存。不同 JVM 并行启动时不能靠该缓存互相预留端口。本次故障已有 Dubbo 启动阶段的 `BindException`，但未取得其具体冲突端口，不把某个默认或临时端口当成已证实的故障现场。

`observability_runtime.prepare()` 只给上述三服务写入 `dubbo.protocol.port`，并在安全回执提供整数 `rpc_port`；其它服务该字段为 `null`。`write=False` 和服务器 `--dry-run` 不写配置或启动 JVM。受管文件沿现有 `spring.config.additional-location` 引用加载，不增加 Gradle Before Run，也不改变原生 Spring Boot/Application Run/Debug。

配置优先级已用实际运行 Jar 内的 Spring Boot 4.0.4、Alibaba Nacos 2025.1.0.0 验证：保留原 `application.yml`、`application-dev.yml` 及十四个 Nacos import，以合成 `ConfigService` 替代传输，实际执行 `ConfigDataEnvironmentPostProcessor` 与 `NacosConfigDataLoader`。没有额外文件时，合成 Nacos 的端口为 `-1`；加入受管文件后端口为固定值，Nacos 来源仍实际参与加载。此验证不连接 Nacos、不创建应用上下文、不执行任何业务。

覆盖保护限三个提供方的具体端口键：`dubbo.protocol.port`、`dubbo.protocols.tri.port`、`DUBBO_PORT_TO_BIND`、`DUBBO_PORT_TO_REGISTRY`、`TRI_DUBBO_PORT_TO_BIND`、`TRI_DUBBO_PORT_TO_REGISTRY`，包含大小写、下划线与连字符的规范化等价形式。后两项由实际 Dubbo 3.3.6 `ServiceConfig.getValueFromConfig()` 的协议名前缀规则确认。IDE 的 VM、程序参数或环境覆盖会在原生 JVM 启动前拒绝；服务器入口拒绝相应程序参数并清理继承环境中的这些键。其它 Dubbo 选项与无关工具 metadata 保留，不按整个 `DUBBO_` 前缀拦截。

此改动不更新业务 Jar、Maven 制品、Compose 或 Nacos 正文。实际切换需先核对三个固定端口未占用，保留未知进程；只替换已核归属的提供方 JVM。源码测试与配置优先级验证通过后，仍须读回新 PID 对应的实际协议端口、HTTP readiness 和 Nacos 注册身份，再验收跨服务调用。多个副本若部署到同一主机，需要独立实例端口规划，不能直接复用本单实例映射。
