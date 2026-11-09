# kellen-workspace

12个个人开源项目的统一开发入口。各项目保留独立Git仓库，在IDEA中统一导入11个Java项目；Web前端admin-web使用自己的工具链。

本仓库只保存清单、辅助脚本、IDE配置工具和文档。项目清单以[`repos.json`](repos.json)为准，不包含子项目源码、Git历史或公司项目。

| 类型 | 项目 |
| --- | --- |
| Java库（2） | `utils`、`rpc-api` |
| Java服务（9） | `gateway`、`user`、`ai-agent`、`ai`、`ai-memory`、`rag`、`mcp`、`message`、`report` |
| Web前端（1） | `admin-web` |

## IDEA原生Spring Boot与服务器Jar观测启动

IDE原生开发与服务器Jar入口共用 `scripts/observability_runtime.py` 的完整 preflight：固定官方 OTel Java agent `2.33.1` 的 SHA-256/版本，以及本地构建隐私扩展 `0.1.1` 的 SHA-256/兼容版本；任一缺失、摘要或版本不符均在服务 JVM 启动前拒绝。仅给选定服务挂 agent，不使用全局 `JAVA_TOOL_OPTIONS`，不影响编译器和测试 JVM。

先按 `utils/ops/github-dev/observability/agent-extension/README.md` 准备两个 Git 外制品。私有 runtime env 提供 `OTEL_JAVAAGENT_PATH`、`OTEL_JAVAAGENT_SHA256`、`FLEET_PRIVACY_EXTENSION_PATH`、`FLEET_PRIVACY_EXTENSION_SHA256`、`OTEL_EXPORTER_OTLP_ENDPOINT` 和需要的 `OTEL_EXPORTER_OTLP_HEADERS`；私有 dev env 提供既有 `GITHUB_*` 配置及 `GITHUB_ACTUATOR_TOKEN`。两文件必须由当前用户持有、0600、非 symlink，内容按简单 env 格式解析，不执行 shell。

gateway、ai-agent 的独立管理端口为 `17100`、`17310`。私有 runtime env 必须显式提供 `FLEET_MANAGEMENT_BIND_ADDRESS`，与 NAS Prometheus 目标一致，仅接受明确私网地址；不从 OTLP 出站路由猜入站网卡，不默认绑定所有接口。其它服务沿用受保护 Actuator 入口。

三个 Dubbo 提供方共用准备入口分配固定 Tri 端口：`user=27500`、`rag=27200`、`ai-memory=27301`；其余六个服务不写 RPC 端口。端口写入受管 `application.properties`，由现有 `spring.config.additional-location` 加载，原生 IDEA 与服务器 Jar 使用相同设置。实际 Boot 4.0.4 / Nacos 2025.1.0.0 loader 的离线配置验证确认此文件覆盖导入的 `dubbo.protocol.port=-1`；不依赖各 JVM 同时探测空闲端口。启动前仍须核对端口无人占用，发现未知监听进程应停止切换，不自动终止它。具体原理、覆盖保护与验收边界见 [Dubbo 固定端口说明](docs/dubbo-provider-ports.md)。

### 一次配置本机私有引用

```bash
python3 scripts/prepare-observed.py configure \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env
```

该命令只在被忽略的 `build/observability-runtime/launch.json` 保存原私有文件的路径引用。每次启动再读原文件、重验制品，生成 0700 目录中的 0600 `agent.properties` 与 `application.properties`。OTLP header、运维令牌和配置地址等实际值只在这些受限文件，不进入源码、IDE XML、命令行或回执。自定义 `--state-dir` 必须位于 Git 工作树外或被明确 ignore；可跟踪路径会在写入前拒绝。新 clone 未配置私有绑定仍可编译/测试；迁移后的原生运行入口缺少绑定时会在 JVM 启动前明确拒绝。

### IDEA原生Spring Boot/Application入口

已有 `GatewayApplication`、`AiAgentApplication` 等原生 Spring Boot/Application 配置可受限迁移：

```bash
python3 scripts/configure-idea-observed.py \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env \
  --idea-config-dir "/path/to/IDE-config" --dry-run
python3 scripts/configure-idea-observed.py \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env \
  --idea-config-dir "/path/to/IDE-config" \
  --expected-sha256 <本次dry-run的source_sha256> \
  --expected-tools-sha256 <已存在工具文件的tools_source_sha256>
```

工具仅匹配清单中的9个主类、对应模块和已核个人 GitHub origin；其它配置保留，冲突 VM/业务参数或环境变量会拒绝迁移并保留原文件。全部候选通过后，按原 XML SHA 做 CAS、留受限备份，再更新本机忽略的 `.idea/workspace.xml`。IDE XML 只引用 agent 与两个配置文件的路径，并添加 IDE 内置 `ToolBeforeRunTask`：用户照常点主类绿叶 Run/Debug，原生 JVM 启动前由 External Tool 直接执行 Python preflight，不装额外插件。成功检查保持静默，失败只把固定 reason/hint 写 stderr 并以非零退出，阻止启动。普通 `-Xmx` 和 JDWP 调试参数保留。

工具在指定 IDE 配置目录的 `tools/` 下维护独立个人工具组；只接管持久 owner/root 标记一致的九个工具，不覆盖用户其它工具。新建文件时省略 `--expected-tools-sha256`；已有文件则使用本次 dry-run 的原 SHA。工作区 XML 和工具文件候选都通过 CAS 后才应用，并保存受限备份。

IDEA 已打开时，需要刷新并核对原生运行配置的 VM options、Before launch 中的 External Tool；若 IDE 内存仍保留旧配置，先保存并关闭对应工作区，再重新 dry-run/CAS 迁移后打开。脚本写入成功不表示 IDEA 内存刷新或真实 Run 已验收。后来新建的原生 Application 配置需要重新执行迁移，不能从“主类相同”推定已挂 agent。

原来的九个共享服务启动文件及旧运行前任务已移除；迁移时保留 Make 和其它用户检查。日常直接选择 Spring Boot 主类绿叶 Run/Debug。新 clone 由 IDEA 识别各主类，创建原生运行配置后进行上述受限迁移，不生成第二套共享启动器。

IDEA 保存 XML 时可能把绝对路径改写为 `$PROJECT_DIR$`、`$USER_HOME$` 或带 UTF-8 百分号和 `..` 的本地 `file:` URI。校验会规范化这些受支持的等价拼写，再逐项比较同一受控文件；迁移器保留等价宏形式，不反复改写。未知宏、非本地 URI、query/fragment、不同目标与重复 agent 引用仍会拒绝。失败回执使用固定 `reason_code` 区分 `IDE_REFERENCE_*`、`IDE_*_OVERRIDE` 和 `LAUNCH_BINDING_*`，不回显参数、路径内容或秘密。

IDEA 可能继承交互 shell 的工具环境。检查仅把私有项目配置实际提供的 `GITHUB_*` 键及明确的 Nacos/Actuator 身份键视为覆盖；无关工具 metadata 不因前缀相同而被拒绝。项目键名仅作为安全元数据随 preflight 回执供校验使用，不包含值。实际受控键即使为空也会拒绝；OTel、管理端口、日志与关键 Spring 参数的保护保持。

### 服务器Jar入口

服务器部署 `scripts/run-observed-jar.py`、`scripts/observability_runtime.py` 与业务 Boot Jar。配置仍由服务器自己的受限私有 env 提供：

```bash
python3 scripts/run-observed-jar.py rag /deploy/rag.jar \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env \
  --state-dir /secure/observability-runtime --dry-run
# 去掉 --dry-run 后以 exec 启动真正的 java -jar。
```

Jar 的 `Start-Class` 必须匹配声明服务，`Implementation-Version` 必须存在；日志与 trace 使用同一版本。dry-run 做完整输入检查、不写文件、不启动 JVM。业务参数可放在 `--` 后，但覆盖受控日志、管理入口、版本、Spring 配置来源或 OTel 的参数会在 JVM 前拒绝。服务器路径和凭据不从本机复制，按目标环境提供；当前入口限定个人 `github-dev`，不隐式切换公司或其它环境。

### 运维检查与日志采集

下述工具供已授权运维验收与进程接管使用；日常开发仍直接点 IDEA 原生 Run/Debug。`scripts/manage-observed.py`用于现有进程的受控替换，不要求先发布远端 Git/Maven。它会先配置私有绑定、完成全部 prepare 和 dry-run，再核对个人 checkout classpath、服务主类、业务/管理端口归属，只对明确选中的旧 PID 发 TERM；超时不强杀。任一 preflight 失败保留旧 JVM。新进程必须通过 health、正确令牌 metrics、无/错令牌拒绝，才记为 verified。

```bash
python3 scripts/manage-observed.py collector-start --runtime-env /secure/observability.env
python3 scripts/manage-observed.py start user --replace-pid <已核个人JVM的PID> \
  --runtime-env /secure/observability.env --dev-env /secure/github-dev.env
```

Java 文件日志写入所选 state 目录下的 `logs/`，Boot ECS 保留安全HTTP投影及AI元数据白名单；单服务轮转10MB、保留3期、总上限40MB。不要同步写 NAS 远程文件。Java 在 Mac 运行时，NAS Collector 无法直接读取 Mac 日志，应用主机也需运行文件 Collector；服务器同样复用当地采集器并让 `FLEET_LOG_DIR` 指向所选 state 的 `logs/`。本机入口支持固定官方 `otelcol-contrib 0.164.0`，私有 env 提供 `FLEET_COLLECTOR_PATH`、`FLEET_COLLECTOR_SHA256`、`FLEET_OTLP_TOKEN`；本机数值遥测仅绑定 `127.0.0.1:18888`，不占用现有8888进程。不会自动安装工具、数据库或中间件。

共享 `logging.yml` 固定环境 `github-dev`；prepare 同时设置应用版本和 OTel resource 版本，禁用原始异常 events、重复 metrics/log exporter，并加载隐私扩展。独立管理端口会迁走 health/metrics，业务端口另外核监听 JVM 归属。启动进程不是操作系统自启/守护程序；文件存在、编译成功、IDE配置写入、服务启动、NAS目标UP、ES入库和Jaeger链路需分别验收。

本机访问自身私网地址可能被本机路由/网络策略阻断，即使 NAS 已成功抓取。托管验证超时会保留新 JVM 和诊断回执，不自动回滚或杀进程；先从 NAS 核 health、令牌边界和目标状态，再决定是否重试。

## 个人 PostgreSQL 数据迁移

[`scripts/database/migrate.py`](scripts/database/migrate.py) 是个人七库的一次性 JDBC 迁移入口，默认只读计划；显式执行要求已停止业务写入、目标空表、完整 schema 审核及本次 `plan_sha256`。PostgreSQL 只部署在 NAS，本机运行 Java 17/JDBC 客户端，不安装或运行本地数据库服务。来源、目标均限固定个人七库，不包括公司、中间件或独立调度平台数据库。

工具保留原 ID，以每库事务复制和内容指纹校验，并通过受限 receipts 记录提交与序列校准；不创建表、删除、覆盖或自动切换 Nacos。全部 VERIFIED、独立新连接对账、服务启动、管理端点、日志和链路仍须分别验收。应用恢复写入后不得用旧迁移计划重跑或恢复。完整配置形状、超时单位、空目标准备及恢复限制见 [七库迁移说明](scripts/database/README.md)。

## 环境要求

- Git、Bash、Python 3.10+（只使用标准库，无需安装Python依赖）。
- JDK 17；Gradle由根目录的wrapper固定为8.14.3，无需全局安装。
- 首次下载需要访问GitHub和项目使用的远端依赖仓库。
- Web前端的Node.js/包管理器，按admin-web文档配置。

## 初始化

全新下载公开仓库：

```bash
git clone https://github.com/sunkailun1992/kellen-workspace.git
cd kellen-workspace
./scripts/bootstrap.sh
```

脚本按照清单将12个项目的`main`分支下载到工作区根目录下的同名子目录。每个目录有独立`.git`，全部被本仓库忽略。

已有项目位于工作区的相邻目录时，可以复用现有checkout：

```bash
./scripts/bootstrap.sh --reuse-existing ..
```

`--reuse-existing PATH`只为工作区内尚不存在的项目目录建立本机symlink，目标是`PATH/<项目名>`。脚本先核对独立Git根目录与origin；不会移动目录、覆盖已有文件、切换已有分支或清理dirty worktree。已有项目和本机symlink都不加入总仓Git。

两种初始化都可以先预览，或只选择部分项目：

```bash
./scripts/bootstrap.sh --dry-run
./scripts/bootstrap.sh --only utils rpc-api
./scripts/bootstrap.sh --json
```

部分下载适合单独查看项目；根Gradle统一导入要求清单中的11个Java项目全部存在。`--json`输出一个JSON对象，适用于自动化调用。

## 状态与安全更新

```bash
./scripts/status.sh
./scripts/update.sh --dry-run
./scripts/update.sh
```

`status.sh`只读取本地Git状态，不自动fetch。显示的ahead/behind以当前本地tracking refs为依据，不能当作刚刚核对过的远端状态。

`update.sh`只更新身份匹配且已存在的项目。已有dirty或非`main`的仓库先跳过，不fetch；其余仓库先fetch核对远端，再复查本地状态，仅对干净、处于清单指定`main`分支且没有本地领先或分歧的仓库执行`merge --ff-only origin/main`。fetch可以更新本地tracking refs，不会改动工作树。以下情况会保留工作树原状并跳过：

- 有未提交、暂存或未跟踪文件。
- 当前不是`main`，或处于detached HEAD。
- 有本地领先提交，或本地与远端已分歧。

脚本不会stash、reset、自动提交、推送或切换已有仓库分支。origin不匹配、非Git目录或其他操作错误会明确报告；逐仓处理后会汇总结果。

FF合并同时使用`--no-overwrite-ignore`：若远端开始跟踪与本机ignored文件同名的路径，更新会中止并返回操作错误，保留本机文件和当前HEAD。正常的ignored构建产物本身不计为dirty。

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部成功，或状态检查正常完成 |
| `1` | 输入或操作错误；查看逐仓错误信息 |
| `2` | 更新时至少一个仓库因保护条件被跳过 |

## 构建模型与IDE导入

```bash
./scripts/idea.sh
./gradlew verifyWorkspace
./gradlew compileAllJava
```

用IDEA打开本工作区根目录，设置Gradle JVM为JDK 17，并从根`settings.gradle`进行一次Gradle导入。根构建将清单中的11个Java项目作为included builds导入。缺少项目时会提示先初始化，不会静默忽略。

`idea.sh`在本机`.idea/gradle.xml`中只为当前工作区链接设置`delegatedBuild=false`，让原生Run/Debug使用IDEA编译；保留`testRunner`、其它项目链接及未知设置。新克隆可生成该根链接；模糊/重复链接或文件在规划后发生变化时拒绝覆盖。该本机设置不进入Git，不改变全局IDE选项或服务器打包方式。

复用symlink时，根构建将included build路径解析为目标的真实路径，避免IDEA模型把链接路径与真实路径视为不同项目；构建名保持一致。

`idea.sh`生成本机忽略的`.idea/vcs.xml`，为总仓和12个子仓按真实目录保留唯一Git映射。复用symlink时与Gradle导入路径一致，清理同一仓库的链接路径、真实路径和重复条目，避免分支菜单出现两组仓库；不相关的自定义映射和非Git配置保留。运行脚本后可在IDEA版本控制设置中确认这些独立仓库。

IDEA日常使用各服务主类的原生Spring Boot/Application Run/Debug，按上文受限迁移以启用直接Python检查。本节Gradle composite仅用于构建整合与验证，不替代原生运行；标准wrapper不改，保留的显式观测init/CLI仅兼容既有运维自动化。本仓库不提供真实凭据，也不自动启动数据库、中间件或业务服务。

`idea.sh`同时为清单中的Web前端生成本机忽略的`.idea/workspace-web/admin-web.iml`，并将它合并进`.idea/modules.xml`。前端作为独立`WEB_MODULE`出现在Project树，新建内容根使用复用目录的真实绝对`file://`路径，排除`node_modules`、`dist`和`coverage`；真实路径只写入被忽略的本机配置。旧版受管`.iml`中的`$PROJECT_DIR$`内容根及其下URL会迁移为绝对路径，避免IDEA未展开宏导致`Watch roots should be absolute`；IDE自身改写的等价`$MODULE_DIR$`和绝对路径保留。已有Java/自定义模块、用户设置及已登记的同目录Web模块保留；重复执行幂等，`--dry-run`不写文件，损坏XML或输出symlink会停止并保留原文件。

IDE元数据使用标准库的受限Expat解析入口：拒绝DTD、自定义/外部实体和参数实体，UTF-16等编码也使用同一规则；单文件读取与格式化输出上限2MiB，单次运行按真实路径缓存解析结果，最多读取512个文件、原始输入累计8MiB，重复模块引用不重复解析和扫描。每文件深度128、元素及注释合计50000个。命名空间展开前先限制每标签256个属性、名称1024字符、命名空间URI512字符及总声明128个，再限制展开后的名称、值、文本和注释合计8MiB字符。合法注释、命名空间与XML预定义转义保留；所有输入和候选输出通过后才写文件，拒绝错误不包含XML正文或外部标识。无需安装第三方Python包。

已打开IDEA时，运行脚本后执行File → Reload All from Disk，等待模块模型刷新，再在Project树展开`admin-web`并核对`src`和`package.json`。生成文件通过不等于窗口已显示；若仍未加载，先检查IDE日志与内容根路径，再通过File → New → Module from Existing Sources导入生成的`.iml`，或关闭对应工作区、重跑脚本后重新打开。前端模块不依赖根Gradle导入，也不带Gradle外部系统标记。

`admin-web`继续使用自己的前端工具链，不加入Java构建。在其目录使用既有前端命令；Project树可见不代表依赖安装、打包或运行已经验收。

移动端和微信小程序不在总工作区范围内。历史`ai-wechat-mini-programs`目录或symlink保留并继续被Git忽略，脚本不下载、更新或检查该仓库。`idea.sh`会移除此前生成的该仓精确Git映射（工作区路径及symlink真实目标），保留其他用户映射；根IDEA模块也排除该目录。已有IDEA会话请执行脚本后重新加载根Gradle模型。

## 本地依赖与独立发布

根composite只将个人项目的`com:utils`和`com:rpc-api`依赖替换为本地源码，方便跨项目修改和编译。`com.kellen:utils`及`com.kellen:rpc-api`不在替换范围内。

子仓的固定远端Maven依赖版本、制品坐标、单仓CI和发布流程保持原样。离开总工作区单独构建时，仍按子仓现有方式解析依赖；总仓不负责发布本地库到远端仓库。

修改业务代码后，在实际修改的子仓中分别审查、提交和推送。例如：

```bash
git -C utils status
git -C utils add <需要提交的文件>
git -C utils commit -m "修复工具方法"
git -C utils push
```

只有清单、脚本、IDE配置工具和文档等工作区改动才提交到总仓。12个子仓的提交与推送互相独立，没有跨仓原子提交；跨仓变更应记录各仓提交并按各自流程验证和发布。

## 验证与CI边界

本地脚本测试与语法检查：

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/bootstrap.sh scripts/update.sh scripts/status.sh scripts/idea.sh
```

测试使用临时本地Git仓库，不向真实子项目注入测试提交。默认测试还会检查原生运行配置生成与共享Gradle启动入口退役；需要执行Gradle临时fixture验证时，在已准备JDK 17的环境中运行：

```bash
WORKSPACE_GRADLE_TESTS=1 python3 -m unittest discover -s tests -p test_composite.py -v
```

GitHub Actions在`main`、`test`、`preprod`推送及面向`main`的PR上运行，也支持手动触发。CI分别运行脚本测试，以及Gradle临时fixture、真实公开清单下载、JDK 17下的`verifyWorkspace`和`compileAllJava`。

根`compileAllJava`默认只编译Java源码，不运行子仓业务测试、不启动服务、不发布远端制品。编译通过不能代表子仓业务测试通过。IDEA实际导入、业务测试、部署和运行态验收需要分别确认，并以对应仓库本轮实际检查结果为准。

## 本机文件与隐私

12个项目目录、symlink、`.idea/`、Gradle缓存、构建产物、环境文件和本机配置均由`.gitignore`排除。提交前检查总仓暂存树，避免通过强制添加绕过忽略规则。

本机验证生成的`.class`、`.log`及各层级私有环境文件也会被忽略；不含凭据的`.env.example`、`.env.sample`、`.env.template`等示例模板可正常跟踪。

不要把凭据、真实运行配置、个人绝对路径、公司项目、私有历史备份或归档加入总仓。源码检查工具应在本仓库已跟踪文件范围内运行；检查子项目时在对应独立仓库中单独执行，避免递归扫描被忽略的checkout或symlink。


### 开发环境全量Trace与安全输入输出

github-dev使用`always_on`，包括收到`traceparent`中sampled=0的请求，保留原Trace/parent并向下游传播采样标记；旧`FLEET_TRACE_RATIO`不再控制此环境。Java隐私扩展0.1.1在实际exporter前保留有限GenAI模型/Prompt版本/供应商报告用量与Dubbo协议元数据，继续清除原始异常、SQL、URL、模型正文和未知属性。完整Trace以验收请求清单逐项读回为准，100%采样不能替代存储持久化保证。

普通HTTP请求通过`com:fleet-observability:0.1.2`统一输出`event=http.server.exchange`：方法、框架路由模板、状态、耗时、真实读写字节、完整性、结果及同Trace/Span标识。每体最多8192字节，仅对路由/类型白名单作投影；未知或无效JSON整段省略，不截出原始前缀。SSE、二进制和AI HTTP正文不缓存。`request_body_omitted/response_body_omitted`明确省略原因；完整性为false或未知字节不代表空体。

AI的模型调用单独建`ai.model.call`子span，记忆/RAG编排与模型计时分开。日志摘要由代码按操作、模型、长度、finish reason生成；token仅在供应商实际报告时出现，公开reasoning摘要的存在性不等于模型隐藏的内部推理。当前自由Prompt/回答正文采集保持关闭，诊断投影只接受经过类型白名单的元数据，不能通过设置日志级别打开原始正文。

在Grafana“探索”选择Elasticsearch，查询`Resource.service.name:"rag" AND event:"http.server.exchange"`；生成的`summary`显示可读信息，展开条目查看安全`request_body/response_body`、省略原因及`trace_id`链接。Jaeger v3原生UI可按服务/时间检索整条链。实际验收工具是`scripts/acceptance_run.py`，默认只计划，显式`--execute`才发清单；`--readback-ledger`仅重查已发Trace，避免重复调用模型。
