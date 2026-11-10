# 服务启动凭据边界

本入口仅为清单中的九个个人 Java 服务准备启动输入，不读取公司配置。源码接线、私有配置生成、IDE 内存刷新、真实 Run/Debug、目标 JVM 生效分别验收；本文不表示任何本机绑定、IDE 或运行进程已修改。

## 现行归属

- 存储连接与业务凭据由 AI DB 控制面管理。`GITHUB_MINIO_*`、OSS/COS/S3/storage 家族及旧 Aliyun 存储 access key 不再写入任何服务的受管 application.properties。
- 公开模板中的 Google/DeepSeek/Qwen 变量只分发给 ai-agent；RAG Qwen embedding/Engine 变量只分发给 rag。没有声明消费者的 `GITHUB_*_API_KEY` 默认不广播。数据库、JWT/HMAC 及其它现行基础配置本批保持原行为，不宣称已完成所有凭据的独立治理。
- `AI_STORAGE_KEK_B64`、`AI_STORAGE_KEK_ID`、`AI_STORAGE_ALLOWED_ORIGINS` 和 `AI_MODELCONTROLPLANE_APIBASEURL`、`AI_MODELCONTROLPLANE_APIKEY`、`AI_MODELCONTROLPLANE_CONSOLEBASEURL` 只允许 AI 的私有 OS 环境输入。KEK 不进入 application.properties、JVM `-D`、业务参数、IDE XML 值、Git 或启动回执。
- 环境文件可以提供当前消费方的真实 provider key 或 LiteLLM 应用 key；入口不执行模型调用、不自动更改 Nacos、不推定业务已切换到 LiteLLM。

代码策略在 `scripts/service_runtime_environment.py`。名称区别于本机临时运维 helper，避免动态导入时与其它同名 `service_environment` 模块冲突。

## 每服务私有 JSON

在 Git 外创建独立文件，由当前启动用户持有，普通文件、0600、单硬链接、绝对路径，整条路径不得经过 symlink。大小不超过 64 KiB。只接受 UTF-8 JSON 对象；重复键、未知键、空对象、非字符串、控制字符、无效编码或并发漂移均拒绝，不执行 shell。

AI 文件示意（仅键与占位符；真实值不能粘进对话或 Git）：

```json
{
  "AI_STORAGE_KEK_B64": "<32-byte-random-master-key-in-base64>",
  "AI_STORAGE_KEK_ID": "v1",
  "AI_STORAGE_ALLOWED_ORIGINS": "<approved-storage-origin>",
  "AI_MODELCONTROLPLANE_APIBASEURL": "<catalog-api-base-url>",
  "AI_MODELCONTROLPLANE_APIKEY": "<catalog-read-key>",
  "AI_MODELCONTROLPLANE_CONSOLEBASEURL": "<native-console-base-url>"
}
```

字段按实际部署需要提供；空 DB/缺 KEK 的 AI 行为仍由服务的 typed unconfigured 合同决定。不要把样例占位符当成有效配置。

ai-agent 可选键为公开模板的 `GITHUB_GOOGLE_API_KEY` / `GITHUB_GOOGLE_BASE_URL` / `GITHUB_GOOGLE_CHAT_MODEL`、DeepSeek 的 API_KEY/BASE_URL/MODEL、Qwen 的 API_KEY/BASE_URL/CHAT_MODEL。rag 可选 `GITHUB_RAG_QWEN_API_KEY` / `GITHUB_RAG_QWEN_BASE_URL` / `GITHUB_RAG_ENGINE_API_KEY` / `GITHUB_RAG_ENGINE_URL`。其余六个服务本批没有额外 OS 键，不接受 AI 的文件。

文件同时用于 CLI 与原生 IDE，值须能被 JetBrains 原生 parser 无损表示：拒绝开头空白、美元符号、双引号和控制字符，不裁剪或改写秘密。支持的 `#`、单引号、反斜线和 Unicode 以受管双引号格式写入 `.env`；不使用 `shlex.quote`，不执行变量展开。若实际凭据超出该格式，停止并调整受支持的输入方案，不能静默转义成另一个值。

## 持久绑定与 CLI

既有 launch.json 的 runtime_env/dev_env/state_dir 三键继续有效；新可选 service_envs 只保存每服务文件路径，不保存值：

```bash
python3 scripts/prepare-observed.py configure \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env \
  --service-env ai=/secure/ai.environment.private.json \
  --service-env ai-agent=/secure/agent.environment.private.json \
  --service-env rag=/secure/rag.environment.private.json
```

显式提供的 service map 是完整替换；普通三键 configure 刷新保留已有 map。`--clear-service-envs` 仅在 configure 中显式清除。未配置额外环境的旧三键绑定不需要升级。

`run-observed.py ai` 和原生 IDE 运行前任务每次通过绑定重新读取、校验输入。Jar 不指定 runtime/dev 时也读取绑定；显式提供目标服务器 runtime/dev 时，应同时显式提供其私有文件，避免隐式复制本机绑定：

```bash
python3 scripts/run-observed-jar.py ai /deploy/ai.jar \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env \
  --state-dir /secure/observability-runtime \
  --service-env-file /secure/ai.environment.private.json --dry-run
```

实际执行前再去掉 dry-run。部署脚本须同时带上 `observability_runtime.py`、`service_runtime_environment.py` 与 Jar 入口。dry-run 不写产物、不启动 JVM；回执只含路径、键名和摘要。真正 exec 前按预检 SHA 再读该 JSON，变化即拒绝。父环境中的旧受控键被移除，只有已校验的本服务键加入目标 OS 环境；同名受信 OS 模型 key 会从 application.properties 剔除，不让旧 shared raw key 仍作为低优先级属性留在该 JVM。

兼容 bootRun 的入口使用 `--no-daemon`。Gradle 的父 OS 环境没有这些 key；只有目标 JavaExec 在 doFirst 根据预检摘要读取本服务文件并设置目标 OS 环境，不传给编译、测试或其它服务任务。与 Python/IDE 等启动器一样，该 Gradle 启动进程会短暂在自身内存处理目标值，不能宣称只有服务进程接触过值；不会把它们写成 `-D` 或打印。

## 原生 IDEA 的 plan/CAS

IntelliJ IDEA 官方支持 Application/Spring Boot 的原生 env 文件，无需安装 EnvFile 插件。候选只为九个已核个人仓库的现有原生配置添加路径：

```xml
<option name="envFilePaths">
  <option value="/secure/observability-runtime/prepared/ai/service.env" />
</option>
```

这是 XCollection v1 形状，不是 list wrapper。每服务生成独立 0600 `.env`，未配置额外 OS 值的服务生成空文件。XML 中不出现环境值；不会写入 `.sh`/`.bat` 等可执行 env 脚本。

沿用 `configure-idea-observed.py --dry-run` 生成完整候选，再用当前 workspace/tools SHA 做 CAS。可通过重复 `--service-env service=/absolute/file.json` 提供本次 map；实际路径、IDE XML 和绑定不在本次源码任务中应用。既有 envFilePaths 若指向未知用户文件、脚本或多文件组合，会保留原文件并拒绝迁移，不读取或接管这些文件。等价受管路径的 IDEA 宏形式保留。

原有 PASS_PARENT_ENVS 设置保持，不静默改变用户其它继承变量。运行前任务发现父环境或显式 env 中有受控配置/密钥，会失败关闭；清除遗留全局变量后再规划，不能靠把空值或同值写进 XML 绕过。此方案不扫描任意命名的秘密，约束范围为已声明的业务和观测命名空间。

内置 Before launch External Tool 使用独立命令行并继承 IDE 父环境，不自动读取目标 Run Configuration 的 envFilePaths；已从本机公开安装包核对这一调用链。第三方 ToolsCustomizer 可能更改命令行环境，本轮未读取实际扩展状态，不把此结论扩大到所有插件。

每次 Before launch 准备后校验 envFilePaths、受管内容摘要、owner/mode/link 身份，拒绝显式 env/VM/业务参数覆盖。脚本成功写入不代表 IDE 已重载，更不代表真实 Run/Debug 已验证。IDE 打开时应保存/关闭工作区，再按最新 SHA 规划与应用，重开后独立核对。

## 公开依据与验证边界

- [JetBrains 官方环境变量说明](https://www.jetbrains.com/help/idea/program-arguments-and-environment-variables.html)：原生 env 文件入口。
- [固定公开源码的 JVM options](https://github.com/JetBrains/intellij-community/blob/668585bc697eb098ac6472f436d924665dabe206/java/execution/impl/src/com/intellij/execution/application/JvmMainMethodRunConfigurationOptions.kt)、[XCollection 序列化](https://github.com/JetBrains/intellij-community/blob/668585bc697eb098ac6472f436d924665dabe206/platform/util/src/com/intellij/util/xmlb/annotations/XCollection.java)：精确 XML 形状。
- [EnvFileParser](https://github.com/JetBrains/intellij-community/blob/668585bc697eb098ac6472f436d924665dabe206/platform/execution-impl/src/com/intellij/execution/envFile/EnvFileParser.kt)、[合并顺序](https://github.com/JetBrains/intellij-community/blob/668585bc697eb098ac6472f436d924665dabe206/platform/execution-impl/src/com/intellij/execution/util/ProgramParametersConfigurator.java)：文件后被显式 env 覆盖，之后仍有美元及 IDE 宏展开，不能当成 shell dotenv。

公开源码 ref 不是本机 build 的对应 tag；已另只读核对本机公开安装包 IU 2026.3/build 263.7307.200 的同一关键 API/bytecode。没有读取实际 IDE 私有状态或应用配置。离线 Python/Java/Gradle 子进程与 XML 夹具不替代真实 IDE、服务重启和密钥边界验收；已运行的旧 JVM、旧生成文件和远端 Nacos 不会因为源码已更新就自动清除。
