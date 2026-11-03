# kellen-workspace

13个个人开源项目的统一开发入口。各项目保留独立Git仓库，在IDEA中通过根Gradle composite统一导入11个Java项目；前端和微信小程序使用各自工具链。

本仓库只保存清单、辅助脚本、共享IDE运行入口和文档。项目清单以[`repos.json`](repos.json)为准，不包含子项目源码、Git历史或公司项目。

| 类型 | 项目 |
| --- | --- |
| Java库（2） | `utils`、`rpc-api` |
| Java服务（9） | `gateway`、`user`、`ai-agent`、`ai`、`ai-memory`、`rag`、`mcp`、`message`、`report` |
| 前端（2） | `admin-web`、`ai-wechat-mini-programs` |

## 环境要求

- Git、Bash、Python 3.10+（只使用标准库，无需安装Python依赖）。
- JDK 17；Gradle由根目录的wrapper固定为8.14.3，无需全局安装。
- 首次下载需要访问GitHub和项目使用的远端依赖仓库。
- 前端的Node.js/包管理器及微信开发者工具，按各子项目文档配置。

## 初始化

全新下载公开仓库：

```bash
git clone https://github.com/sunkailun1992/kellen-workspace.git
cd kellen-workspace
./scripts/bootstrap.sh
```

脚本按照清单将13个项目的`main`分支下载到工作区根目录下的同名子目录。每个目录有独立`.git`，全部被本仓库忽略。

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

`idea.sh`生成本机忽略的`.idea/vcs.xml`，添加总仓和13个子仓的Git映射，并保留既有用户映射。运行脚本后可在IDEA版本控制设置中确认这些独立仓库。

`.run/`提供9个服务的共享Gradle `bootRun`入口；入口通过根构建调用`:服务名:bootRun`，每个服务的工作目录为自己的项目目录。启动前，按子项目说明准备本机配置和所需外部服务。本仓库不提供真实凭据，也不自动启动数据库、中间件或业务服务。

`admin-web`和`ai-wechat-mini-programs`不加入Java构建。分别在自己的目录中使用既有前端命令和微信开发者工具；IDEA中的Git映射不代表其依赖安装、打包或运行已经完成。

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

只有清单、脚本、共享入口和文档等工作区改动才提交到总仓。13个子仓的提交与推送互相独立，没有跨仓原子提交；跨仓变更应记录各仓提交并按各自流程验证和发布。

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

13个项目目录、symlink、`.idea/`、Gradle缓存、构建产物、环境文件和本机配置均由`.gitignore`排除。提交前检查总仓暂存树，避免通过强制添加绕过忽略规则。

不要把凭据、真实运行配置、个人绝对路径、公司项目、私有历史备份或归档加入总仓。源码检查工具应在本仓库已跟踪文件范围内运行；检查子项目时在对应独立仓库中单独执行，避免递归扫描被忽略的checkout或symlink。
