# 服务私有 OS 环境持久接线计划

目标：保留现有观测/固定端口/三键绑定，修复全量 GITHUB 凭据广播；让 AI 控制面输入经每服务私有 OS 文件持久进入标准 Jar/IDE 入口。仅本仓源码、临时合成测试和文档，不读取或改写真实 env、bindings、IDE、Nacos、运行进程。

1. 审计公开模板与实际启动代码，固定 Google/DeepSeek/Qwen→Agent、RAG embedding/Engine→RAG、storage→无 Java 消费者；不扩大 DB/HMAC 治理。
2. RED→GREEN：0600 owner/regular/no symlink/no hardlink/重复键/UTF8/大小/控制符/键范围；独立真实 child environment 证明值不进 argv/stdout。
3. RED→GREEN：prepare 范围过滤、可信 OS 同名键移除旧 properties、三键兼容与普通刷新保留 map、SHA 漂移拒绝、IDE 单一受管路径与未知文件保护。
4. Jar exec 重新读取已验 SHA；bootRun 只给目标 JavaExec 载入受信文件，并使用非驻留 Gradle，编译/测试/父 OS 环境无 key。
5. 依据官方文档、固定公开源码和公开安装包元数据核 native envFilePaths/解析；沿用 workspace/tools plan/CAS，不安装插件或触碰实际 IDE。
6. 独立 review、全量 unittest、显式 Gradle integration、语法/diff/秘密检查；给主会话 sourcefreeze，协调 main 发布后跟精确 CI。

实施裁定：

- 新模块名 service_runtime_environment 避免与本机运维 helper 的同名模块污染；代码内无私有绝对路径。
- 受管 native 格式拒绝 dollar/doublequote/control/leading whitespace；不裁剪或猜测转义秘密。
- 未声明消费者的旧 GITHUB_*_API_KEY 默认丢弃，DB/HMAC/普通业务键仍保持原逻辑。
- 未知用户 env 文件保留并拒绝迁移；父环境继承开关保持用户原状，有受控父变量则 preflight fail closed。
- 无制品 version 字段，不改其它仓版本。Git、离线验证、IDE 应用、真实 JVM 和 Nacos 边界分别报告。
