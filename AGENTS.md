# kellen-workspace

这是12个个人开源项目的开发入口仓库，不是业务代码仓库。

- 对用户回复使用繁体中文；代码、注释、文档和提交信息沿用简体中文或既有英文。
- 先读 `README.md`、`repos.json` 和 `docs/superpowers/specs/2026-11-03-workspace-design.md`。
- 12个子项目保持独立Git，不使用submodule，不将子项目源码、Git历史或本机链接加入本仓库索引。
- 整合范围仅11个Java后端和admin-web；移动端不纳入。历史小程序目录或symlink保留并继续ignore，不能删除或修改。
- 只能操作清单中显式声明的GitHub项目；禁止扫描、复制或修改公司项目及私有历史备份。
- 下载、复用前核对独立Git根目录和origin。已有目录、symlink目标、dirty worktree必须保留。
- 更新仅对干净、处于清单指定main分支、没有本地领先或分歧的仓库执行fast-forward。禁止reset、force push、stash、自动commit/push或切换已有仓库分支。
- IDE及Gradle整合只修改本仓库；不改子项目业务代码、版本、制品坐标、运行配置取值或密钥。
- 测试必须使用临时本地Git仓库，不得向真实子项目注入测试提交。真实验证只在个人项目上进行。
- 不写入真实凭据、个人绝对路径、IDE私有状态或运行时配置。所有子目录和本机生成文件必须被ignore。
- 提交前、推送前fetch；远端前进时在验证后正常整合，不强推。新功能分支使用`codex/`前缀，完整交付main后清理本次短分支。
- 编译、脚本测试、IDE导入、CI、部署与运行态验收分别记录，不能互相代替。
