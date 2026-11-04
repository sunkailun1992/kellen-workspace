# kellen-workspace设计

## 已确认目标

用户选择总入口仓库、初始化/更新脚本和Gradle composite方案，授权建立`kellen-workspace`、完成后提交推送main。12个项目保持独立Git，一键取得和更新，在IDEA统一导入。公司项目不改动。本方案不再引入新的产品选择或人工审批步骤。

## 方案比较

- 采用：workspace meta repo + clone/reuse脚本 + Gradle composite。日常只提交真正修改的子仓，不维护母仓的gitlink指针。
- 未采用：submodule + composite。可以标准递归clone及固定SHA，但增加detached HEAD和两层指针提交操作。
- 未采用：monorepo。与保留独立Git的目标不符。

## 结构与接口

- `repos.json`为唯一12仓清单：`schemaVersion:1`，每项`name/url/branch/kind`；GitHub公开URL，branch均main，11个Java（2库、9服务）、1个Web前端admin-web；不接受移动端kind或历史小程序名称。
- Python 3.10+标准库实现`scripts/workspace.py`；Bash薄入口`bootstrap.sh/update.sh/status.sh/idea.sh`。无新增第三方运行时依赖。
- 默认下载到工作区根目录下的同名子目录。所有12个目录由工作区Git忽略，子项目拥有独立`.git`。
- `bootstrap --reuse-existing PATH`只给缺失的工作区子目录建立本机symlink，指向PATH下对应项目。已有目标只核对，不覆盖、不移动、不切分支，允许保留已有dirty。当前本机使用个人GitHub父目录，不复制源码或配置。
- `bootstrap`不带复用路径时clone清单中的main；支持`--only NAME...`选择、`--dry-run`预览、`--json`机器输出。origin核对同时接受同仓HTTPS和SSH常见写法，不接受嵌入凭据或其他仓库。
- `update`只更新已存在且身份正确的仓库；工作区不干净、非main、detached、ahead、diverged全部跳过且保留。fetch后仅使用`merge --ff-only origin/main`，不stash、不reset、不commit/push、不自动切分支。重复更新幂等。
- `status`只读本地Git状态，不隐式fetch；明确ahead/behind基于当前tracking refs。输出每仓branch、短SHA及dirty/ahead/behind等状态，不输出文件内容或凭据。
- 退出码：0全部成功/正常状态；1输入或操作错误；2更新因保护条件跳过至少一仓。逐仓失败有汇总，不将一次失败误报为整套成功。
- `idea`生成本机忽略的`.idea/vcs.xml`，只添加工作区和12个子Git映射，移除历史小程序的精确Git映射（工作区路径和symlink真实目标），保留其他用户映射。有效JSON输出必须只有一个JSON对象，日志不得混入stdout。
- `.run/*.run.xml`保存9个共享Gradle `bootRun`入口，引用工作区根构建的`:服务名:bootRun`；服务工作目录必须是各自项目目录。Web前端保持自己的工具链，不纳入Java构建；移动端不在本工作区范围内。
- Gradle wrapper固定8.14.3，与现有11个Java项目一致。根`settings.gradle`从清单includeBuild所有Java项目；缺失时明确报初始化提示，不静默漏导入。
- composite只在总工作区生效，显式替换`com:utils`和`com:rpc-api`为本地源码；公司`com.kellen:utils/rpc-api`不替换。单仓CI、版本、远端私库发布方式均不修改。
- 根Gradle应用内建idea插件，将历史ai-wechat-mini-programs目录及其真实目标排除出根模块内容；保留原目录/symlink和Git ignore，不删除或修改独立小程序仓库。
- 根Gradle提供`verifyWorkspace`及`compileAllJava`；不默认运行全部业务测试、启动服务或发布远端制品。

## 验证与边界

- 真实临时bare Git集成测试证明：初次clone、复用dirty、FF更新、dirty/untracked/staged保护、非main/detached/ahead/diverged保护、origin错误、已有非Git目录、缺失项目与重复调用。测试不依赖公网。
- `.idea` XML合并、共享运行配置及清单一致性纳入验证。路径含空格/中文、symlink复用必须有覆盖。
- 本机复用现有12仓并验证全部11 included builds、SDK替换与Java编译；检查每个bootRun工作目录，不启动服务。再用全新临时目录验证一键clone所有12公开仓。
- GitHub CI验证Python集成测试、全新清单下载与composite构建入口；提供push/PR/workflow_dispatch。
- 尽可能实际打开IDEA并确认统一Gradle导入；若无法完成则记录准确缺口，不用命令行成功冒充GUI验收。
- 不提交子项目源码、真实配置、凭据、个人绝对路径、构建产物和IDE本机状态；不操作公司路径、不访问私有历史备份作为数据源。
- 总仓公开、main管理者可直推、其他人PR审查；保留main/test/preprod并在完成后清理本任务短分支。
