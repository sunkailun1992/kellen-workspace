# kellen-workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供13仓独立Git的一键下载/更新和IDEA统一导入入口，验证后推送main。

**Architecture:** 清单驱动的meta repo；Python标准库封装安全Git操作；Gradle composite及共享运行配置统一Java开发。本机通过ignored symlink复用现有checkout，新使用者直接clone子仓。

**Tech Stack:** Git、Python 3.10+、Bash、Gradle 8.14.3、Java 17、IntelliJ IDEA。

**Spec:** `docs/superpowers/specs/2026-11-03-workspace-design.md`

## Global Constraints

- 13仓独立Git，不使用submodule；公司项目与私有历史备份不操作。
- 子仓dirty、非main、detached、ahead/diverged不得被自动切换、覆盖或提交。
- 仅替换本地`com:utils`/`com:rpc-api`；无业务源代码与远端制品发布变更。
- 不提交任何实际凭据、个人绝对路径、子仓源码/历史、产物或IDE本机状态。

## Review Focus

- 路径含空格/中文和本机symlink：仍能识别独立Git根、IDE映射和included build。
- 错误origin、已有普通目录或非Git目录：拒绝更新或覆盖。
- dirty/untracked/staged、detached、ahead/diverged、非main：保护并汇总跳过，不产生隐式stash/reset/commit。
- 公网失败和半途clone：保留原数据，下一次可重试；不可误报全成功。
- IDEA统一导入与bootRun工作目录：本地配置仍由子服务读取，不启动服务来冒充验证。

## Task 1: 安全工作区CLI

**Files:** `scripts/workspace.py`、四个shell入口、`tests/test_workspace.py`。

**Interfaces:** 清单字段由spec固定；命令`bootstrap/update/status/idea`；公共可选项`--only NAME.../--json/--dry-run`，bootstrap专有`--reuse-existing PATH`。

- [ ] 先写真实临时Git集成测试，观察缺失实现失败。
- [ ] 实现下载、复用、本地状态和仅FF更新，按spec退出码汇总。
- [ ] 实现保留用户映射的IDEA VCS XML生成。
- [ ] 跑完整Python测试，记录保护条件与错误输入证据。

## Task 2: Gradle与IDEA

**Files:** `settings.gradle`、`build.gradle`、`gradle.properties`、wrapper、`.run/`、针对composite的验证脚本/测试。

**Interfaces:** 读取`repos.json`；工作区子目录名固定；`verifyWorkspace/compileAllJava`，9个`:name:bootRun`。

- [ ] 定义/验证缺失仓、清单11 Java、SDK仅com坐标替换和项目工作目录的验收断言。
- [ ] 实现included builds、聚合验证/编译与共享运行入口。
- [ ] 使用本机现有11仓做实际Gradle验证和源码依赖替换检查。
- [ ] 实际IDEA统一导入并观察模型，或准确记录GUI验证缺口。

## Task 3: 文档、CI与整体交付

**Files:** `README.md`、`.gitignore`、`.github/workflows/ci.yml`、发布验证记录。

- [ ] 文档说明两种初始化、更新保护、独立提交/推送、前端工具链、本机配置与现有Gateway测试边界。
- [ ] CI运行Python测试并验证真实清单和Gradle入口；提供手动触发。
- [ ] 独立审查脚本、子仓索引隔离与13仓/公司边界；执行全新13仓下载。
- [ ] 检查暂存树和凭据、提交、合入main、推送并回读refs/CI；设置公开仓权限并清理本次短分支。
