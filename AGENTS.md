# kellen-workspace

这是12个个人开源项目的开发入口仓库，不是业务代码仓库。

- 对用户回复默认使用简体中文；代码、注释、文档和提交信息沿用简体中文或既有英文。
- 先读 `README.md`、`repos.json` 和 `docs/superpowers/specs/2026-11-03-workspace-design.md`。
- 12个子项目保持独立Git，不使用submodule，不将子项目源码、Git历史或本机链接加入本仓库索引。
- 整合范围仅11个Java后端和admin-web；移动端不纳入。历史小程序目录或symlink保留并继续ignore，不能删除或修改。
- 只能操作清单中显式声明的GitHub项目；禁止扫描、复制或修改公司项目及私有历史备份。
- 下载、复用前核对独立Git根目录和origin。已有目录、symlink目标、dirty worktree必须保留。
- 更新仅对干净、处于清单指定main分支、没有本地领先或分歧的仓库执行fast-forward。禁止reset、force push、stash、自动commit/push或切换已有仓库分支。
- IDE及Gradle整合只修改本仓库；不改子项目业务代码、版本、制品坐标、运行配置取值或密钥。
- 测试必须使用临时本地Git仓库，不得向真实子项目注入测试提交。真实验证只在个人项目上进行。
- 不将真实凭据、个人绝对路径、IDE私有状态或运行时配置加入Git。授权的本机配置生成仅写被忽略目录或IDE个人设置中的独立受管组，按dry-run、SHA比对和受限备份应用；未知用户配置保留。
- IDEA日常开发使用九个原生Spring Boot/Application入口，用户点击主类绿叶Run/Debug；不生成Java服务的共享Gradle启动配置，不添加Gradle Before Run。原生运行前通过IDE内置External Tool执行Python校验，不启动Gradle、不装额外插件。
- `idea.sh`只为当前工作区根链接维护`delegatedBuild=false`，使用IDEA编译运行；`testRunner`、其它链接及未知用户设置保留，文件并发变化须CAS拒绝，不修改全局委托选项。
- Gradle composite继续用于构建模型、SDK源码替换和验证；标准wrapper不改。命令行/服务器Jar入口保持独立，不替代IDE原生启动体验。
- 提交前、推送前fetch；远端前进时在验证后正常整合，不强推。新功能分支使用`codex/`前缀，完整交付main后清理本次短分支。
- 编译、脚本测试、IDE导入、CI、部署与运行态验收分别记录，不能互相代替。

- 服务 OS 环境输入按 `docs/service-environment.md` 的私有 JSON/每服务 allowlist 准备；AI KEK 只能走本服务 OS env，不进 properties、JVM-D、argv、Git 或 IDE XML 值。原生 envFilePaths 只保存路径，未知用户 env 文件保留并拒绝接管；不把源码支持等同实际 IDE 已应用。
