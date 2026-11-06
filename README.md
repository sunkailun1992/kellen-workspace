# kellen-workspace

12个个人开源项目的统一开发入口。各项目保留独立Git仓库，在IDEA中通过根Gradle composite统一导入11个Java项目；Web前端admin-web使用自己的工具链。

本仓库只保存清单、辅助脚本、共享IDE运行入口和文档。项目清单以[`repos.json`](repos.json)为准，不包含子项目源码、Git历史或公司项目。

| 类型 | 项目 |
| --- | --- |
| Java库（2） | `utils`、`rpc-api` |
| Java服务（9） | `gateway`、`user`、`ai-agent`、`ai`、`ai-memory`、`rag`、`mcp`、`message`、`report` |
| Web前端（1） | `admin-web` |

## 可选观测启动

全体系观测使用 OTel Java agent，启动前从官方 `opentelemetry-java-instrumentation` 的固定 release `v2.33.1` 获取 agent 和官方 SHA-256 校验值；将文件保存在 Git 外。还必须按 `utils/ops/github-dev/observability/agent-extension/README.md` 构建 0.1.0 隐私扩展，提供 `FLEET_PRIVACY_EXTENSION_PATH` 与 `FLEET_PRIVACY_EXTENSION_SHA256`；两者缺失时拒绝启用。`OTEL_JAVAAGENT_PATH`、`OTEL_JAVAAGENT_SHA256`、`OTEL_EXPORTER_OTLP_ENDPOINT` 由私有环境提供，若 Collector 要求认证，再提供 `OTEL_EXPORTER_OTLP_HEADERS`；不要把凭据写在命令行或提交到仓库。

```bash
python3 scripts/run-observed.py rag --dry-run
python3 scripts/run-observed.py rag
```

启动器校验清单、固定官方 agent 2.33.1 的 SHA-256/版本与扩展 0.1.0 的本地构建 SHA-256/兼容版本，仅向选中服务的 `bootRun` JVM 注入 agent、`OTEL_JAVAAGENT_EXTENSIONS` 与 events=0，不设置全局 `JAVA_TOOL_OPTIONS`。gateway、ai-agent 默认管理端口分别为17100、17310且仅绑定loopback；从NAS抓取前必须显式设置私网绑定并验证 `actuatorReq` 令牌保护。其它服务沿用已有受保护Actuator入口。

服务 JVM 从所选项目的 `project.version` 同时设置 `SPRING_APPLICATION_VERSION` 与 OTel `service.version`，使 Boot ECS 日志和链路使用同一制品版本；共享模板固定环境为 `github-dev`。Jar/IDE 入口需按观测部署说明先校验两个 Jar 并显式注入相同版本和隐私扩展，缺值日志显示 `unknown`。

公共配置和极空间独立编排位于 `utils/ops/github-dev/observability/`。Java运行在Mac时，NAS不能直接读取Mac日志；应在应用主机运行文件采集器，或复用已有安全采集通道。控制台成功不代表ES已采集、Jaeger可查询或目标实例已启用。

### 本机受控切换与日志采集

`scripts/manage-observed.py`复用上述观测启动器和workspace composite，不要求先发布远端Git或Maven。启动前须在个人`github-dev`配置中心准备匹配的`actuator.yaml`与metadata-only `logging.yml`，并保留各服务根目录的私有`config/application-dev.properties`。现有服务需逐个切换，不能关闭IDE本身或按进程名批量终止Java。

私有runtime env必须是当前用户受限普通文件（0600，非symlink），包含固定agent/隐私扩展的路径及摘要、`OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS`、`FLEET_OTLP_TOKEN`。可再配置`FLEET_COLLECTOR_PATH`和`FLEET_COLLECTOR_SHA256`，指向项目任务缓存中经官方release摘要验证的`otelcol-contrib 0.164.0`本机二进制。启动器不执行env内容，不把凭据加入命令行，也不设置全局`JAVA_TOOL_OPTIONS`；业务dev配置仍从既有私有文件和服务根目录读取。

```bash
python3 scripts/manage-observed.py collector-start --runtime-env /secure/observability.env
python3 scripts/manage-observed.py start user --replace-pid <已核个人JVM的PID> --runtime-env /secure/observability.env
```

替换前同时校验PID的个人checkout classpath、服务主类和监听端口，只向该JVM发送TERM；超时不强杀。新进程必须通过应用health、带令牌metrics以及无/错令牌拒绝检查，才记为verified。gateway/ai-agent的health与metrics迁到独立管理端口；必须在私有runtime env显式提供与NAS Prometheus目标一致的`FLEET_MANAGEMENT_BIND_ADDRESS`，不从OTLP出站路由猜入站网卡。业务端口另外验证监听JVM归属。默认`build/observability-runtime/`保存受限PID回执、尝试历史和诊断日志，`logs/`只写白名单ECS JSON；单服务轮转上限10MB、保留3期、总上限40MB。Collector只采集该目录，使用仓库原生`collector-agent.yaml`、有限异步队列和固定官方二进制，不安装全局工具；它自身的数值遥测只绑定`127.0.0.1:18888`，不占用既有8888进程。退出工具后服务继续运行；此入口不是操作系统开机自启或自动重启守护程序。

该入口的成功回执只证明本机进程和管理边界。NAS Prometheus目标UP、ES真实安全日志、Jaeger链路和告警通知仍须分别验收；Collector启动成功不能替代日志送达。

本机访问自身私网地址可能被本机路由/网络策略阻断，即使NAS已经成功抓取。验证超时会保留新JVM和诊断回执，不自动回滚或杀进程；先从NAS分别核health、正确/错误令牌和Prometheus目标，再决定是否重试。外部验收证据与本机超时分开保存，不能把本机探测失败直接解释为服务未启动。

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

## IDEA统一导入

```bash
./scripts/idea.sh
./gradlew verifyWorkspace
./gradlew compileAllJava
```

用IDEA打开本工作区根目录，设置Gradle JVM为JDK 17，并从根`settings.gradle`进行一次Gradle导入。根构建将清单中的11个Java项目作为included builds导入。缺少项目时会提示先初始化，不会静默忽略。

复用symlink时，根构建将included build路径解析为目标的真实路径，避免IDEA模型把链接路径与真实路径视为不同项目；构建名仍沿用清单，所以`:服务名:bootRun`入口保持一致。

`idea.sh`生成本机忽略的`.idea/vcs.xml`，为总仓和12个子仓按真实目录保留唯一Git映射。复用symlink时与Gradle导入路径一致，清理同一仓库的链接路径、真实路径和重复条目，避免分支菜单出现两组仓库；不相关的自定义映射和非Git配置保留。运行脚本后可在IDEA版本控制设置中确认这些独立仓库。

`.run/`提供9个服务的共享Gradle `bootRun`入口；入口通过根构建调用`:服务名:bootRun`，每个服务的工作目录为自己的项目目录。启动前，按子项目说明准备本机配置和所需外部服务。本仓库不提供真实凭据，也不自动启动数据库、中间件或业务服务。

`admin-web`不加入Java构建，在自己的目录中使用既有前端命令；IDEA中的Git映射不代表其依赖安装、打包或运行已经完成。

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

只有清单、脚本、共享入口和文档等工作区改动才提交到总仓。12个子仓的提交与推送互相独立，没有跨仓原子提交；跨仓变更应记录各仓提交并按各自流程验证和发布。

## 验证与CI边界

本地脚本测试与语法检查：

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/bootstrap.sh scripts/update.sh scripts/status.sh scripts/idea.sh
```

测试使用临时本地Git仓库，不向真实子项目注入测试提交。默认测试还会检查共享运行配置；需要执行Gradle临时fixture验证时，在已准备JDK 17的环境中运行：

```bash
WORKSPACE_GRADLE_TESTS=1 python3 -m unittest discover -s tests -p test_composite.py -v
```

GitHub Actions在`main`、`test`、`preprod`推送及面向`main`的PR上运行，也支持手动触发。CI分别运行脚本测试，以及Gradle临时fixture、真实公开清单下载、JDK 17下的`verifyWorkspace`和`compileAllJava`。

根`compileAllJava`默认只编译Java源码，不运行子仓业务测试、不启动服务、不发布远端制品。已知Gateway子仓有3个配置契约测试失败；本工作区未修复该问题，编译通过不能代表这些测试通过。IDEA实际导入、业务测试、部署和运行态验收也需要分别确认。

## 本机文件与隐私

12个项目目录、symlink、`.idea/`、Gradle缓存、构建产物、环境文件和本机配置均由`.gitignore`排除。提交前检查总仓暂存树，避免通过强制添加绕过忽略规则。

本机验证生成的`.class`、`.log`及各层级私有环境文件也会被忽略；不含凭据的`.env.example`、`.env.sample`、`.env.template`等示例模板可正常跟踪。

不要把凭据、真实运行配置、个人绝对路径、公司项目、私有历史备份或归档加入总仓。源码检查工具应在本仓库已跟踪文件范围内运行；检查子项目时在对应独立仓库中单独执行，避免递归扫描被忽略的checkout或symlink。
