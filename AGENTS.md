# kellen-workspace

这是12个个人开源项目的开发入口仓库，不是业务代码仓库。

- 对用户回复默认使用简体中文；代码、注释、文档和提交信息沿用简体中文或既有英文。
- 先读 `README.md`、`repos.json` 和 `docs/superpowers/specs/2026-11-03-workspace-design.md`。
- 12个子项目保持独立Git，不使用submodule，不将子项目源码、Git历史或本机链接加入本仓库索引。
- 整合范围仅11个Java后端和admin-web；移动端不纳入。微信小程序项目`ai-wechat-mini-programs`已废弃，本机不再保留目录；脚本继续排除并忽略该名称，避免误纳入。
- 只能操作清单中显式声明的GitHub项目；禁止扫描、复制或修改公司项目及私有历史备份。
- 下载、复用前核对独立Git根目录和origin。已有目录、symlink目标、dirty worktree必须保留。
- 更新仅对干净、处于清单指定main分支、没有本地领先或分歧的仓库执行fast-forward。禁止reset、force push、stash、自动commit/push或切换已有仓库分支。
- IDE及Gradle整合只修改本仓库；不改子项目业务代码、版本、制品坐标、运行配置取值或密钥。
- 测试必须使用临时本地Git仓库，不得向真实子项目注入测试提交。真实验证只在个人项目上进行。
- 测试结果只能由输入决定，不依赖真实时间、执行顺序或本机遗留状态；偶发失败按缺陷处理，不用重试、跳过或删除测试掩盖。本机通过不代表CI通过，只在一个平台验证过的结论写明平台。
- 不将真实凭据、个人绝对路径、IDE私有状态或运行时配置加入Git。授权的本机配置生成仅写被忽略目录或IDE个人设置中的独立受管组，按dry-run、SHA比对和受限备份应用；未知用户配置保留。
- IDEA日常开发使用九个原生Spring Boot/Application入口，用户点击主类绿叶Run/Debug；不生成Java服务的共享Gradle启动配置，不添加Gradle Before Run。原生运行前通过IDE内置External Tool执行Python校验，不启动Gradle、不装额外插件。
- `idea.sh`只为当前工作区根链接维护`delegatedBuild=false`，使用IDEA编译运行；`testRunner`、其它链接及未知用户设置保留，文件并发变化须CAS拒绝，不修改全局委托选项。
- Gradle composite继续用于构建模型、SDK源码替换和验证；标准wrapper不改。命令行/服务器Jar入口保持独立，不替代IDE原生启动体验。
- 提交前、推送前fetch；远端前进时在验证后正常整合，不强推。新功能分支使用`codex/`前缀，完整交付main后清理本次短分支。
- 维护者本人，以及维护者本机用维护者账号运行的Claude/Codex会话，可以直接合并到`main`：合并前本机验证命令全过，合并后看CI，红了马上修。推送后读回精确提交的CI结论。
- 云端会话（`CLAUDE_CODE_REMOTE=true`，或会话里没有本机配置）只推会话分支，由维护者或本机会话合并。其他人的代码走PR：CI全绿、维护者审核批准、评审对话解决后再合并。代理不修改ruleset与仓库权限，除非用户当场明确要求。
- 编译、脚本测试、IDE导入、CI、部署与运行态验收分别记录，不能互相代替。
- 本机自动验收以 `dev-scenarios.json` 为场景清单。Codex、Claude 和 IssueRelay 先运行 `python3 scripts/dev-verify.py plan <场景>`，再按任务范围执行 `run` 并附上 JSON 回执。新增场景应复用仓内可重复的检查，记录 Wiki 参考与未覆盖范围；个人场景不引用公司仓库、配置或数据。

- 服务 OS 环境输入按 `docs/service-environment.md` 的私有 JSON/每服务 allowlist 准备；AI KEK 只能走本服务 OS env，不进 properties、JVM-D、argv、Git 或 IDE XML 值。原生 envFilePaths 只保存路径，未知用户 env 文件保留并拒绝接管；不把源码支持等同实际 IDE 已应用。
## 会话材料与本机仓库引用

- IssueRelay 任务上下文、用户会话、交接原文、日志、截图、回执、本机绝对路径和仓库映射只放在本机忽略目录或 Git 之外，不加入暂存区。长期 Wiki 仅按其规则保存经授权的脱敏证据。
- 任务跨出固定清单时，可在本机提供 `--repo-map` 和私有场景。它们只扩展当前任务的本地验证范围，既有 bootstrap、Git 远端规则和各仓授权仍适用。
