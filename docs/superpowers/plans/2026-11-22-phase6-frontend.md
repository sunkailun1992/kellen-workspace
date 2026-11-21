# 第6阶段前端升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin-web 升到 React 19、antd 6、ProComponents 3、Umi Max 4.7.36，工具链换成主流多数做法（Node 26、pnpm 12、TypeScript 6.0、ESLint 10、Prettier 3、Vitest、Playwright、husky）；`sorting` 改名 `sort`，前后端同一批上线。

**Architecture:** 分三部分按顺序交付。第一部分在现有 React 18 与 antd 5 代码上立起格式、代码、类型检查、Vitest 单元测试与 Playwright 浏览器测试（固定数据的一组进 CI，经真实网关的一组在本机跑），得到升级前基线；第二部分升依赖、改布局右侧、迁移弃用写法，以控制台警告清零为准；第三部分 utils 改基类字段并发版，四个有表的服务用 Flyway 改列名，其余服务跟升，admin-web 改字段，全部验证后一起合入，停服、备份、启动迁移，再用两遍全链路与浏览器测试验收。

**Tech Stack:** Node 26、pnpm 12.21.0、Umi Max 4.7.36（utoopack）、React 19.3、antd 6.7、ProComponents 3.1 beta、TypeScript 6.0.3、ESLint 10 + typescript-eslint 8 + eslint-plugin-react-hooks 7、Prettier 3、Vitest 5、Playwright Test 1.65、husky 9 + lint-staged 17；Gradle 9.9.1、JDK 21、Spring Boot 4.0.9、MyBatis-Plus、Flyway、PostgreSQL。

**Spec:** `docs/superpowers/specs/2026-11-22-phase6-frontend-design.md`

## Global Constraints

- 范围：admin-web、utils、user、message、ai、ai-memory、ai-agent、rag、mcp、report、kellen-workspace（只有设计与计划文档）、`~/.config/kellen-github/tools/`（运行脚本）。rpc-api、gateway 不动；美联项目不动；代理、VPN、组网相关配置一律不碰。
- 主流规则（用户2026-11-20）：每个实现选择以 GitHub 多个主流项目的多数做法与官方文档、标准、论文为依据；偏离设计或主流做法要在台账写 `Ruling:` 并附依据；不为缩小改动范围简化。
- 版本（推送前 fetch，按规范核对远端 main、在途分支、tag、私库占用，被占就按编号顺序顺延）：admin-web 1.2.1→1.2.2（第一部分）→1.2.3（第二部分）→1.2.4（第三部分）；utils 2.0.8→2.0.9；user 1.5.6→1.5.7；message 1.4.5→1.4.6；ai 1.9.5→1.9.6；ai-memory 1.3.4→1.3.5；ai-agent 0.4.4→0.4.5；rag 1.4.4→1.4.5；mcp 1.4.4→1.4.5；report 1.3.4→1.3.5。
- 前端依赖（写计划时的 npm latest；用 `^` 加锁文件，ProComponents 精确锁定）：react、react-dom `^19.3.1`；@types/react、@types/react-dom `^19.3.0`；antd `^6.7.1`；@ant-design/icons `^6.3.4`；@ant-design/pro-components `3.1.15-9`（实施当天 3.x 最新 beta，选定写 `Ruling:`）；@umijs/max `^4.7.36`；typescript `^6.0.3`；@types/node `^26.6.6`；eslint `^10.15.0`；@eslint/js `^10.0.1`；typescript-eslint `^8.73.0`；eslint-plugin-react-hooks `^7.1.1`；eslint-config-prettier `^10.1.8`；globals `^17.14.0`；prettier `^3.10.1`；prettier-plugin-organize-imports `^4.3.0`；prettier-plugin-packagejson `^3.1.0`；vitest `^5.1.1`；@playwright/test `^1.65.1`；husky `^9.1.7`；lint-staged `^17.6.1`。不加 overrides。
- 运行环境：`package.json` 的 `engines.node` 为 `">=26"`、`packageManager` 为 `pnpm@12.21.0`（本机 pnpm 10.28.2 按它自动切换，不改全局安装）；CI `node-version: 26`。
- CI action 固定完整提交 SHA，行尾注释写版本：actions/checkout v7.0.1、actions/setup-node v7.1.0、pnpm/action-setup v6.1.1、actions/upload-artifact v7.0.2；SHA 用 `gh api repos/<action>/commits/<tag> --jq .sha` 取。
- 测试稳定性：不重试（Playwright `retries: 0`；官方模板 CI 重试2次，这里按工作区 AGENTS「偶发失败按缺陷处理」）；等待一律用有总时限的条件等待；结果不依赖真实时间与执行顺序。
- 浏览器测试：页面清单从 `config/routes.ts` 推出，当前12页：`/dashboard`、`/system/tenant`、`/system/dept`、`/system/user`、`/system/role`、`/system/resource`、`/message/send`、`/ai/model`、`/configuration/models`、`/configuration/storage`、`/rag-knowledge/documents`、`/rag-knowledge/graph`；登录失败页 `/login?error` 单独一条。固定数据一组：`max preview --port 4173`，接口用 `page.route` 拦截；真实网关一组：`pnpm dev`（`http://localhost:8000`，已在跑就复用）。本机浏览器 `channel: 'chrome'` 加 `--no-proxy-server`（与全链路脚本一致），CI 用 Playwright Chromium。控制台允许清单 `e2e/support/console-allowlist.ts` 默认空。
- 真实网关一组的账号：全链路用的 `admin`；口令由 `run_chain_verify.py` 的 `probe_password()` 取，以环境变量 `ADMIN_WEB_E2E_PASSWORD` 传给子进程（`ADMIN_WEB_E2E_USERNAME=admin`）；登录状态只留在内存里的浏览器上下文，不写 storageState 文件；合成部门名 `e2e-<YYYYMMDDHHmmss>`，测试结束（含失败）按名删除。
- 改名：Flyway 文件 user `V7__rename_sorting_to_sort.sql`、message `V7__rename_sorting_to_sort.sql`、ai `V8__rename_sorting_to_sort.sql`、ai-memory `V6__rename_sorting_to_sort.sql`，写法照本仓库 `V3__rename_audit_columns.sql`；Java 字段 `private Integer sort;`；查询白名单 `Map.entry("sort", "sort")`、`DEFAULT_SORT_FIELD = "sort"`；前端字段 `sort`。历史迁移文件不改。
- 已定裁定（写计划时定，执行时台账各记一条 `Ruling:`）：
  - 路由表移到 `config/routes.ts`、代理前缀移到 `config/apiPrefixes.ts`（官方模板同样把路由放 `config/routes.ts`）：浏览器测试从同一份数据推出页面清单与要拦截的接口前缀，避免与菜单、代理脱节。
  - Playwright 不重试（见上）。一份 `playwright.config.ts`，环境变量 `ADMIN_WEB_E2E=live` 切到真实网关一组（官方模板也是单份配置）。
  - admin-web 每部分一个短分支（`codex/phase6-toolchain`、`codex/phase6-upgrade`、`codex/phase6-sort`），部分内各任务在该分支提交，部分结束时一次合入，所以每部分只升一次版本。
  - 第三部分各仓库的 `codex/phase6-sort` 验证完先不合入，到任务19一起合入：避免有人中途重启服务、某个库提前改名。utils 例外，要先发版（任务13）。
  - PG 测试库的运行脚本从会话草稿区收进 `~/.config/kellen-github/tools/`（任务1），之后都用它。
- 命令：
  - admin-web：`pnpm format:check`、`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build:dev`、`pnpm e2e`；真实网关一组只经 `python3 -I ~/.config/kellen-github/tools/run_chain_verify.py --admin-web-e2e`；`bash scripts/check-secrets.sh`。下文“前端全套门禁”指依次跑 `pnpm install --frozen-lockfile`、`format:check`、`lint`、`typecheck`、`test`、`build:dev`、`e2e`、`check-secrets`。
  - Java：`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`；不带 PG：`./gradlew clean test bootJar --no-daemon`；带 PG 测试库：在 GitHub 根目录 `~/.config/kellen-github/tools/run_pg_kc.sh <仓库> fleet_pg_test_phase5_schema clean test bootJar --no-daemon`；`bash scripts/check-secrets.sh`。
- Git：开工、提交前、推送前 `git fetch`；短分支从最新 `origin/main` 建（`--no-track`）；本机验证全过后快进合入 `main`、推送、删短分支；不强推；推送后读回精确提交的 CI（user、message、ai 推送不触发 CI，手动 `gh workflow run CI --repo sunkailun1992/<仓库> --ref main` 并核 headSha）。
- NAS：只用 `ssh -o BatchMode=yes qw-nas` 与 `sudo -n docker …`；数据库只读查询与 pg_dump 经 `docker exec`；备份放 `~/.config/kellen-github/phase6/backup/`（目录0700、文件0600）；输出遮掉 IPv4 与9位以上数字串；不打印任何口令。
- 凭据：私有 Maven 只经 `run-maven-release.py`；测试登录口令只在子进程环境里使用；任何密钥不进 Git、Wiki 与对话。
- 运行态：9个服务由用户在 IDEA 启停；不用 IDEA MCP 的 `execute_run_configuration` 运行这九个配置；运行配置不挂 JVM 参数。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-22-phase6-frontend/progress.md`，逐任务记完成行与 `Ruling:` 行。

## Review Focus

1. 新增或改名的菜单路由没进浏览器测试：页面清单必须随 `config/routes.ts` 变——任务7 `menuPagesFromRoutes lists the 12 menu pages in route order`、`menuPagesFromRoutes skips redirects and the login page`。
2. 固定数据一组里有接口没写固定数据、请求悄悄落空：要报出方法与路径并判失败——任务7 `resolveFixture returns undefined for an unknown endpoint` 与每页断言“未拦截列表为空”。
3. 换成 Vitest 后少跑了用例：用例名集合必须与迁移前一致——任务4第3步与基线名单比对。
4. 真实库里有推算之外的表仍带 `sorting` 列：迁移后不允许任何表留着它——任务14、15、16 `noTableKeepsSortingColumn`，任务19第7步读回真实库。
5. antd 6 改了 DOM 或类名，`Rag/Graph/index.less` 覆盖的7个 antd 类名不再命中、页面错位，测试与警告都发现不了——任务7 `graphStyleOverridesStillMatch`（建基线），任务12复跑并做前后截图对比。

---

### Task 1: 提交计划与工具准备

- [ ] **Step 1:** 设计（含写计划时的措辞修订）与本计划在 `codex/phase6-design` 提交，快进合入 kellen-workspace `main` 并推送，删短分支。Expected：Workspace CI success。
- [ ] **Step 2:** 把会话草稿区的 `run_pg_kc.sh`、`run_pg_tests.py` 收进 `~/.config/kellen-github/tools/`（脚本里引用的路径改为相对本目录）；`tools/README.md` 记用法；自动记忆 `github-java-verify-tools-location.md` 补一行。Expected：在 GitHub 根目录 `~/.config/kellen-github/tools/run_pg_kc.sh utils fleet_pg_test_phase5_schema help --no-daemon` 退出0，输出不含 IPv4 与口令。

### Task 2: 运行环境、包管理与类型检查（第一部分起点）

**Files:**
- Modify: `admin-web/package.json`（`engines`、`packageManager`、typescript、@types/node，新增 `typecheck: "tsc --noEmit"`）、`pnpm-lock.yaml`、`tsconfig.json`
- Create（按 pnpm 12 官方升级说明需要时）：`pnpm-workspace.yaml`

- [ ] **Step 1:** 从最新 `origin/main` 建 `codex/phase6-toolchain`。记基线用例名：`node --test --test-reporter=spec tests/unit/*.test.mjs > <台账目录>/baseline-test-names.txt`。Expected：178条、全部通过。
- [ ] **Step 2:** 改运行环境与依赖，`pnpm install`。Expected：仓库内 `pnpm --version` 为12.21.0；锁文件由 pnpm 12 重写；`src/.umi` 由 `max setup` 生成。
- [ ] **Step 3:** `pnpm typecheck`。TS 6.0 改了默认值的选项（`types` 默认 `[]`、`rootDir` 默认 tsconfig 所在目录等）按发布说明在 `tsconfig.json` 显式写出，不关严格模式。Expected：退出0、0错误。
- [ ] **Step 4:** `pnpm test:services`、`pnpm build:dev`。Expected：178条全过；构建成功。
- [ ] **Step 5:** 提交 `build: Node 26、pnpm 12、TypeScript 6.0 与类型检查`。

### Task 3: Vitest——认证与会话相关的5个测试文件

**Files:**
- Create: `admin-web/vitest.config.ts`（`environment: 'node'`；别名 `@`→`src`、`@@`→`src/.umi`；`include: ['tests/unit/**/*.test.{ts,tsx}']`）
- Modify: `package.json`（vitest；`test: "vitest run"`；`test:services` 暂时只跑剩下的 `.mjs`）
- Move: `tests/unit/request-auth.test.mjs`→`.test.ts`、`session-bootstrap`→`.test.ts`、`session-login`→`.test.ts`、`login-page`→`.test.tsx`、`layout-account-menu`→`.test.tsx`

**Interfaces:**
- Produces（任务4沿用的写法）：被测模块直接 `import`；各文件原来的依赖表改为 `vi.mock('<模块>', factory)`，要截取组件属性时用 `vi.mock('antd', async (importOriginal) => ({ ...(await importOriginal()), Dropdown: 截取组件 }))`；组件仍用 `react-dom/server` 的 `renderToStaticMarkup` 渲染；断言用 `expect`：基本值 `toBe`、对象与数组 `toStrictEqual`、正则 `toMatch` / `not.toMatch`。

- [ ] **Step 1:** 按上面写法迁移5个文件，用例名不改。
- [ ] **Step 2:** `pnpm vitest run tests/unit/request-auth.test.ts tests/unit/session-bootstrap.test.ts tests/unit/session-login.test.ts tests/unit/login-page.test.tsx tests/unit/layout-account-menu.test.tsx`。Expected：全过，条数与基线里这5个文件的条数相同。
- [ ] **Step 3:** 变异：`src/utils/requestAuth.ts` 去掉 `X-XSRF-TOKEN`，`src/utils/sessionLogin.ts` 让登录失败页也检查会话。Expected：对应用例失败；还原后全过。
- [ ] **Step 4:** 提交 `test: 认证与会话相关测试改用 Vitest`。

### Task 4: Vitest——其余22个测试文件

**Files:**
- Move: `tests/unit/` 其余22个 `.test.mjs`→`.test.ts` 或 `.test.tsx`
- Modify: `package.json`（删 `test:services`、`test:request-auth`）、`README.md`、`docs/ai-coding/TESTING_SPEC.md`、`docs/ai-coding/RAG_NEO4J_GRAPH.md`、`docs/ai-coding/RAG_SERVICE_PATH_FIX.md`（命令改为 `pnpm test`）

**Interfaces:**
- Consumes：任务3的写法。

- [ ] **Step 1:** 迁移22个文件，用例名不改。
- [ ] **Step 2:** `pnpm test`。Expected：178条（或更多）全过。
- [ ] **Step 3:** `pnpm vitest list` 的用例名集合与 `baseline-test-names.txt` 比对。Expected：完全一致（顺序可不同）。
- [ ] **Step 4:** `rg -n "transpileModule|node:test|runInContext" tests`。Expected：无结果。
- [ ] **Step 5:** 提交 `test: 全部单元测试改用 Vitest`。

### Task 5: Prettier 3 与全仓格式化

**Files:**
- Modify: `package.json`（三个包；`format: "prettier --write ."`、`format:check: "prettier --check ."`）、`.prettierignore`（加 `dist`、`pnpm-lock.yaml`、`e2e-out`）
- Create: `.git-blame-ignore-revs`

- [ ] **Step 1:** 升级后 `pnpm format:check`。Expected：列出待格式化文件，数量记台账。
- [ ] **Step 2:** `pnpm format`，单独提交 `style: Prettier 3 全仓格式化`（这个提交只有格式变化）。Expected：`pnpm test`、`pnpm typecheck` 全过。
- [ ] **Step 3:** 该提交号写进 `.git-blame-ignore-revs`，提交 `chore: 追溯视图跳过格式化提交`。Expected：`pnpm format:check` 退出0。

### Task 6: ESLint 10 与提交钩子

**Files:**
- Create: `admin-web/eslint.config.mjs`（忽略 `src/.umi*`、`dist`、`e2e-out`；依次用 `@eslint/js` 的 recommended、typescript-eslint 的 recommended、eslint-plugin-react-hooks 文档给出的扁平 recommended 预设、`globals` 的 browser 与 node，最后 eslint-config-prettier；不自定义规则）、`.husky/pre-commit`（`pnpm exec lint-staged`）
- Modify: `package.json`（`lint: "eslint ."`、`prepare: "husky"`）、`.lintstagedrc`（ts、tsx、js、mjs 跑 `eslint --fix` 与 `prettier --write`；md、json、less、css 只跑 `prettier --write`）
- Delete: `.eslintrc.js`、`.stylelintrc.js`

- [ ] **Step 1:** 配好后 `pnpm lint`。Expected：按规则统计存量违规，记台账。
- [ ] **Step 2:** 按规则文档逐条改写；确属上游误报的，附上游 issue 链接后在配置里按文件关闭该条并记 `Ruling:`。Expected：`pnpm lint` 退出0；`rg -n "eslint-disable" src tests` 无结果；`pnpm test`、`pnpm typecheck`、`pnpm build:dev` 全过。
- [ ] **Step 3:** 钩子：在临时文件里写一处违规并暂存，`git commit`。Expected：提交被拒；撤掉临时文件后正常。
- [ ] **Step 4:** 提交 `build: ESLint 10 扁平配置与 husky 提交钩子`、`fix: 修掉代码检查的存量问题`。

### Task 7: 浏览器测试（固定数据）与基线警告

**Files:**
- Create: `admin-web/config/routes.ts`（从 `.umirc.ts` 原样移出，`export default routes`）、`config/apiPrefixes.ts`（`BUSINESS_API_PREFIXES = ['/auth', '/user', '/message', '/report', '/api']`、`GATEWAY_OWNED_PREFIXES = ['/oauth2', '/login/oauth2', '/logout']`）
- Modify: `.umirc.ts`（引入上面两份；代理由前缀数组生成，行为不变）、`package.json`（@playwright/test；`e2e: "playwright test"`、`e2e:live: "ADMIN_WEB_E2E=live playwright test"`）、`.gitignore`（`e2e-out/`）
- Create: `playwright.config.ts`（`retries: 0`；固定数据一组 `testDir: 'e2e/mock'`、`baseURL: 'http://localhost:4173'`、`webServer` 为 `pnpm exec max preview --port 4173`，要先 `build:dev`；本机 `channel: 'chrome'` 加 `--no-proxy-server`，`CI` 时用 Playwright 自带 Chromium）、`e2e/support/pages.ts`、`e2e/support/api-fixtures.ts`、`e2e/support/console.ts`、`e2e/support/console-allowlist.ts`、`e2e/fixtures/*.json`（全部是合成数据）、`e2e/mock/pages.spec.ts`、`e2e/mock/login-error.spec.ts`、`e2e/mock/graph-style.spec.ts`
- Test: `tests/unit/e2e-support.test.ts`

**Interfaces:**
- Produces：`type AppRoute = { path?: string; name?: string; component?: string; redirect?: string; layout?: boolean; access?: string; routes?: AppRoute[] }`；`type MenuPage = { path: string; name: string }`；`menuPagesFromRoutes(routes: AppRoute[]): MenuPage[]`（有 `name` 与 `component`、不是 `layout: false` 的路由，嵌套展开，按路由表顺序）。
- Produces：`type ApiFixture = { status: number; body: unknown }`；`resolveFixture(method: string, pathname: string): ApiFixture | undefined`；`isApiRequest(pathname: string): boolean`（命中两组前缀之一）。
- Produces：`type ConsoleProblem = { kind: 'error' | 'warning' | 'pageerror'; text: string }`；`watchConsole(page: Page): ConsoleProblem[]`；`type AllowlistEntry = { pattern: RegExp; source: string; until: 'part2' | string }`（`until` 为 `'part2'` 或上游 issue 的 https 链接）；`assertAllowlistValid(entries: AllowlistEntry[]): void`；`unexpectedProblems(problems: ConsoleProblem[], allowlist: AllowlistEntry[]): ConsoleProblem[]`。

- [ ] **Step 1: 写失败测试**（`tests/unit/e2e-support.test.ts`）
  - `menuPagesFromRoutes lists the 12 menu pages in route order`：路径依次等于 Global Constraints 里的12页。
  - `menuPagesFromRoutes skips redirects and the login page`：结果不含 `/`、`/login`、`/system`、`/message`、`/ai`、`/configuration`、`/rag-knowledge`。
  - `resolveFixture returns undefined for an unknown endpoint`；`resolveFixture returns the signed-in session for GET /auth/current/resources`（固定数据里的用户带 `user:auth:manage` 与六个 `menu:*` 资源码，`access.ts` 的每个 `canSee*`、`canManage*` 都为真）。
  - `allowlist entries need a source and an end condition`：`source` 为空或 `until` 既不是 `'part2'` 也不是 https 链接时 `assertAllowlistValid` 抛错。
- [ ] **Step 2:** `pnpm test`。Expected：新用例失败（模块不存在）。
- [ ] **Step 3:** 实现支持模块，移出路由表与前缀。Expected：`pnpm test` 全过；`pnpm build:dev` 成功；开发代理行为不变（`.umirc.ts` 代理键与原来相同）。
- [ ] **Step 4:** 写浏览器测试：
  - `pages.spec.ts`：对 `menuPagesFromRoutes(routes)` 每页：`isApiRequest` 的请求用 `resolveFixture` 回应，没有固定数据的回501并记入未拦截列表；打开页面，等内容区出现且加载指示消失（15秒内）；断言未拦截列表为空（失败信息列出方法与路径）、没有页面错误、`unexpectedProblems` 为空。
  - `login-error.spec.ts`：打开 `/login?error`，地址不变，「重新登录」可见，没有请求 `/auth/current/resources`。
  - `graph-style.spec.ts` 的 `graphStyleOverridesStillMatch`：在 `/rag-knowledge/graph`，`index.less` 覆盖的 `.ant-form`、`.ant-form-item`、`.ant-collapse-header`、`.ant-collapse-content-box`、`.ant-tabs-tab`、`.ant-spin-nested-loading`、`.ant-spin-container` 在页面根节点内各至少命中1个元素。
- [ ] **Step 5:** `pnpm build:dev && pnpm e2e`，按失败信息补固定数据；来自本项目代码的 antd 5 与 React 18.3 弃用警告就地改掉（antd 迁移指南第1步）；只来自旧版第三方库的警告列入允许清单，`until: 'part2'` 并写来源。Expected：全部通过；允许清单每条都有来源；条数记台账。
- [ ] **Step 6:** 变异：删掉一份固定数据；在某页加一行 `console.warn`；把 `config/routes.ts` 里一个菜单路由改名。Expected：前两项各让对应页面失败且信息指明接口或警告，第三项让清单单元测试失败；还原后全过。
- [ ] **Step 7:** 提交 `test(e2e): Playwright 浏览器测试（固定数据）`、`fix: 去掉本项目代码里的 antd 5 与 React 18.3 弃用警告`。

### Task 8: 浏览器测试（真实网关）与全链路入口

**Files:**
- Create: `admin-web/e2e/live/session.spec.ts`（`test.describe.serial`，`beforeAll` 登录一次、共用一个浏览器上下文）
- Modify: `playwright.config.ts`（`ADMIN_WEB_E2E=live` 时：`testDir: 'e2e/live'`、`baseURL: 'http://localhost:8000'`、`webServer` 为 `pnpm dev` 且复用已在跑的、`workers: 1`）
- Modify: `~/.config/kellen-github/tools/run_chain_verify.py`（改前备份为 `run_chain_verify.py.before-admin-web-e2e-20261122`；加 `--admin-web-e2e`：在 admin-web 目录跑 `pnpm e2e:live`，环境变量见 Global Constraints，退出码原样返回，不打印环境变量）、`tools/README.md`

- [ ] **Step 1:** `python3 -I ~/.config/kellen-github/tools/run_chain_verify.py --admin-web-e2e`。Expected：失败（参数还不存在）。
- [ ] **Step 2:** 写 `session.spec.ts`：
  - 登录：打开 `/oauth2/authorization/keycloak`，填 `#username`、`#password`，点 `#kc-login`，回到 `http://localhost:8000`。
  - `every menu page opens without errors`：页面清单与断言同任务7，另断言没有响应状态 ≥500。
  - `dept create and delete`：在部门页新建 `e2e-<时间戳>`，树里出现；删除，树里消失。`afterAll` 里若仍存在，用同一上下文带 `X-XSRF-TOKEN` 调接口删除。
  - `login error page stays put`：同任务7。
  - `logout ends the session`：点头像菜单「退出登录」，之后同一上下文请求 `/auth/current/resources` 得401。
- [ ] **Step 3:** 实现 `--admin-web-e2e`，跑一遍（9个服务在运行）。Expected：全部通过；部门里没有 `e2e-` 开头的残留；输出不含口令。
- [ ] **Step 4:** 提交 `test(e2e): 经真实网关的浏览器测试`；工具改动记台账（不在 Git 里）。

### Task 9: CI、文档与第一部分交付（1.2.2）

**Files:**
- Modify: `admin-web/.github/workflows/ci.yml`（Node 26；action 按 Global Constraints 固定 SHA；顺序：安装 → `format:check` → `lint` → `typecheck` → `test` → `build:dev` → `playwright install --with-deps chromium` → `e2e` → 失败时上传 `e2e-out` 报告 → `check-secrets`）
- Modify: `README.md`、`AGENTS.md`、`docs/ai-coding/AI_CODING_GUIDE.md`、`docs/ai-coding/TESTING_SPEC.md`（两组浏览器测试、允许清单规则、不重试）、`package.json`（version 1.2.2）

- [ ] **Step 1:** 改 CI 与文档。前端全套门禁。Expected：全过。
- [ ] **Step 2:** 截基线图：固定数据下12页与登录失败页各一张，存 `e2e-out/screenshots/before/`（本机保留，不进 Git）。
- [ ] **Step 3:** fetch；远端前进就 rebase、重跑全套门禁，并按新的格式化提交号更新 `.git-blame-ignore-revs`；快进合入 `main`、推送、删短分支。Expected：精确提交 CI success。
- [ ] **Step 4:** 在 `main` 上跑 `run_chain_verify.py --admin-web-e2e`。Expected：通过，作为升级前基线记台账。

### Task 10: 依赖升级（第二部分起点）

**Files:**
- Modify: `admin-web/package.json`、`pnpm-lock.yaml`，以及类型检查报错的源码（`useRef()` 补初始值等）

- [ ] **Step 1:** 从最新 `origin/main` 建 `codex/phase6-upgrade`。`npm view @ant-design/pro-components versions --json` 取 3.x 最新 beta，记 `Ruling:`。
- [ ] **Step 2:** 按 Global Constraints 升 react、react-dom、两个类型包、antd 与 icons、ProComponents、@umijs/max，`pnpm install`。Expected：安装成功；`pnpm why @ant-design/pro-components` 里项目直接依赖为 3.x。
- [ ] **Step 3:** `pnpm typecheck`，按 React 19 升级指南改类型错误，不用 `any`、不加 `@ts-ignore`。Expected：0错误。
- [ ] **Step 4:** `pnpm lint`、`pnpm test`、`pnpm build:dev`。单元测试若因 antd 6 渲染结构变化而失败，只在确属预期变化时改断言，逐条记台账，不放宽。Expected：全过。
- [ ] **Step 5:** 核对产物只含 ProComponents v3：用构建统计或 source map 列出打进产物的模块路径。Expected：没有含 `pro-components@2` 的路径。
- [ ] **Step 6:** 提交 `feat!: React 19、antd 6、ProComponents 3、Umi Max 4.7.36`。

### Task 11: 布局右侧改用 avatarProps 与 actionsRender

**Files:**
- Create: `admin-web/src/components/AvatarDropdown/index.tsx`（账号菜单与账户设置弹窗，从 `LayoutRightContent` 移来）
- Modify: `src/app.ts`（`layout` 去掉 `rightRender`、`logout`；有当前用户时给 `avatarProps`；`actionsRender` 返回 `MessageCenter`）
- Delete: `src/components/LayoutRightContent/`
- Test: `tests/unit/layout-account-menu.test.tsx`（改测 `AvatarDropdown`）、新增 `tests/unit/layout-runtime.test.ts`

**Interfaces:**
- Produces：`AvatarDropdown(props: { initialState?: AccountState; children: React.ReactNode })`；`AccountState` 即原 `LayoutRightContentProps['initialState']`。
- Produces：`layout(...)` 返回的 `avatarProps = { src, title: 显示名, render: (_props, avatarDom) => <AvatarDropdown initialState={…}>{avatarDom}</AvatarDropdown> }`，`actionsRender = () => [<MessageCenter key="message" … />]`。

- [ ] **Step 1: 写失败测试**
  - `layout uses avatarProps and actionsRender instead of rightRender`：有当前用户时 `avatarProps.title` 为显示名、`render` 是函数、`actionsRender()` 返回1个元素；配置里没有 `rightRender` 与 `logout` 键。
  - `layout has no avatar without an authenticated identity`：没有当前用户时 `avatarProps` 为 undefined。
  - `avatar dropdown lists settings then logout`：菜单项 key 依次为 `settings`、`logout`。
  - `account logout clears the selected tenant and leaves through the gateway after a successful logout` / `after a failed logout`（沿用原用例的期望）。
  - `avatar dropdown renders no umi-plugin-layout classes`：渲染结果不含 `umi-plugin-layout`。
- [ ] **Step 2:** `pnpm test`。Expected：新用例失败。
- [ ] **Step 3:** 照 Ant Design Pro 6 的 `app.tsx` 与头像下拉写法实现。Expected：`pnpm test`、`lint`、`typecheck` 全过。
- [ ] **Step 4:** 固定数据一组加 `account menu opens from the header`：页头有显示名，点开后有「账户设置」「退出登录」。Expected：`pnpm build:dev && pnpm e2e` 通过。
- [ ] **Step 5:** 变异：`avatarProps.render` 不包 `AvatarDropdown`；退出不清选中的租户。Expected：对应用例失败；还原后全过。
- [ ] **Step 6:** 提交 `feat: 布局右侧改用 ProLayout v3 的 avatarProps 与 actionsRender`。

### Task 12: 弃用写法迁移与第二部分交付（1.2.3）

**Files:**
- Modify: 设计“现状”列出的写法所在页面与组件、`src/pages/Rag/Graph/index.less`（选择器不再命中时）、`e2e/support/console-allowlist.ts`
- Test: `tests/unit/e2e-support.test.ts` 加 `no part-one allowlist entries remain`

- [ ] **Step 1:** 写失败测试 `no part-one allowlist entries remain`：允许清单里没有 `until: 'part2'` 的条目。Expected：若第一部分留了条目则失败。
- [ ] **Step 2:** `pnpm build:dev && pnpm e2e`，按控制台警告逐条迁移：Alert `message`→`title`、Space `direction`→`orientation`、Drawer `width`→`size`、`bordered`→`variant`、输入框前后缀→`Space.Compact`、`destroyOnClose`→`destroyOnHidden`、尺寸→`medium`、ProTable `hideInSearch` 等 v3 删除项，以及警告里出现的其他项；第三方的警告随升级消失后删掉允许清单条目。`graphStyleOverridesStillMatch` 失败时，按 antd 6 的新类名或语义化 `classNames` 改 `index.less`。Expected：`pnpm e2e` 全过、允许清单为空（或只剩附上游 issue 的条目）；`pnpm test` 全过。
- [ ] **Step 3:** 截同样的图到 `e2e-out/screenshots/after/`，生成本地左右对比页交用户过目。Expected：用户确认没有错位。
- [ ] **Step 4:** 文档里的技术栈与版本；version 1.2.3；前端全套门禁；`run_chain_verify.py --admin-web-e2e`。Expected：全过。
- [ ] **Step 5:** fetch、必要时 rebase 重验，快进合入 `main`、推送、删短分支。Expected：精确提交 CI success。

### Task 13: utils 2.0.9（第三部分起点）

**Files:**
- Modify: `utils/src/main/java/com/kellen/entity/EntityBase.java`（`private Integer sort;`，说明“排序值”不变）、`build.gradle`（version 2.0.9）、`AGENTS.md`、`README.md`（迁移步骤与消费方影响）、`docs/ai-coding/PROJECT_CODING_SPEC.md`、`docs/ai-coding/examples/ExampleEntity.java`、`ExampleServiceQuery.java`
- Test: `utils/src/test/java/com/kellen/entity/EntityBaseTest.java`

- [ ] **Step 1:** 建 `codex/phase6-sort`。字段清单断言改为 `"id", "code", "description", "createTime", "createBy", "updateTime", "updateBy", "deleted", "label", "sort", "version"`。Expected：测试失败。
- [ ] **Step 2:** 改字段与文档。Expected：`./gradlew clean test javadoc` 全过；`check-secrets` 通过。
- [ ] **Step 3:** 提交 `feat!: 实体基类排序字段改名 sort（2.0.9）`，合入 `main`、推送、读回 CI；发布：`python3 -I ~/.config/kellen-github/observability-20261106/run-maven-release.py <日志> clean :publish --no-build-cache`，读回：`~/.config/kellen-github/tools/maven-readback.py`；上传超时就先 `maven-probe.py` 查已上传的部分，再 `maven-finish-publish.py` 补齐后读回。Expected：CI success；读回 2.0.9 的 jar 与 pom 一致，`javap` 看到 `getSort()`、没有 `getSorting()`。

### Task 14: user 1.5.7

**Files:**
- Create: `user/src/main/resources/db/migration/V7__rename_sorting_to_sort.sql`
- Modify: `AuthUserServiceQuery`、`AuthRoleServiceQuery`、`AuthTenantServiceQuery`、`AuthResourceServiceQuery`、`AuthDeptServiceQuery`（默认排序字段与白名单）、`AuthDeptQuery`（`@Schema` 示例）、`AuthTenantVO`、`AuthRoleVO`、`AuthResourceVO`、`AuthResourceBO`、`build.gradle`（utils 2.0.9、version 1.5.7）、`docs/ai-coding/PROJECT_CODING_SPEC.md`、`examples` 两个文件
- Test: `PostgresqlSchemaLiveTest`、`EntityBaseFieldsTest`、`SecurityContextRpcServiceImplTest`、`AuthCurrentUserServiceImplTest`、新增 `user/src/test/java/com/kellen/auth/service/query/AuthSortFieldTest.java`

- [ ] **Step 1:** 只读核对真实库：带 `sorting` 列的表与名字里带 `sorting` 的索引。Expected：表为 `auth_dept`、`auth_resource`、`auth_role`、`auth_role_data_scope`、`auth_role_resource`、`auth_tenant`、`auth_user`、`auth_user_role`、`auth_user_tenant`；不一致就以真实库为准并记 `Ruling:`。
- [ ] **Step 2: 写失败测试**
  - `PostgresqlSchemaLiveTest`：基础列清单用 `sort`；新增 `noTableKeepsSortingColumn`（本 schema 内 `column_name = 'sorting'` 的列数为0）；原插入用例改用 `sort`。
  - `AuthSortFieldTest`：`defaultOrderIsSort`（5个查询类不传排序字段时 `getSqlSegment()` 含 `ORDER BY sort`）；`legacySortingFieldFallsBackToSort`（传 `sorting` 时同样按 `sort`）；`sortIsSelectable`（`fields` 含 `sort` 时进入 select）。
  - 其余测试里的 `sorting` 改为 `sort`。
- [ ] **Step 3:** 运行（不带与带 PG）。Expected：新用例失败。
- [ ] **Step 4:** 写迁移（每表 `ALTER TABLE … RENAME COLUMN sorting TO sort`；名字带 `sorting` 的索引一并改名）与代码。Expected：不带与带 PG 全过；`check-secrets` 通过；bootJar 里是 `utils-2.0.9.jar`。
- [ ] **Step 5:** 变异：迁移里漏掉 `auth_user_tenant`；`AuthDeptServiceQuery` 的默认排序字段改回 `sorting`。Expected：`noTableKeepsSortingColumn`、`defaultOrderIsSort` 分别失败；还原后全过。
- [ ] **Step 6:** 在 `codex/phase6-sort` 提交 `feat!: 排序字段 sorting 改名 sort（V7，1.5.7）`，先不合入。

### Task 15: message 1.4.6 与 ai-memory 1.3.5

**Files:**
- Create: `message/src/main/resources/db/migration/V7__rename_sorting_to_sort.sql`、`ai-memory/src/main/resources/db/migration/V6__rename_sorting_to_sort.sql`
- Modify: 两仓库 `build.gradle`（utils 2.0.9、版本）、`docs/ai-coding/PROJECT_CODING_SPEC.md`、`examples` 两个文件
- Test: 两仓库的 `PostgresqlSchemaLiveTest`、`EntityBaseFieldsTest`

- [ ] **Step 1:** 只读核对真实库。Expected：message 为 `message_user_message`；ai-memory 为 `ai_memory_candidate`、`ai_memory_event`、`ai_memory_long_fact`、`ai_memory_retrieval_eval`、`ai_memory_short_message`、`ai_memory_short_summary`、`ai_memory_vector_task`；不一致就以真实库为准并记 `Ruling:`。
- [ ] **Step 2:** 两仓库 `PostgresqlSchemaLiveTest` 基础列清单用 `sort`，新增 `noTableKeepsSortingColumn`。Expected：带 PG 运行时失败。
- [ ] **Step 3:** 写迁移，改代码与文档。Expected：不带与带 PG 全过；`check-secrets` 通过；bootJar 里是 utils 2.0.9。
- [ ] **Step 4:** 各自在 `codex/phase6-sort` 提交，先不合入。

### Task 16: ai 1.9.6

**Files:**
- Create: `ai/src/main/resources/db/migration/V8__rename_sorting_to_sort.sql`
- Modify: `ai/src/test/resources/schema.sql`、`storage-schema.sql`（H2 测试建表）、`build.gradle`（utils 2.0.9、version 1.9.6）、`AGENTS.md`、`docs/ai-coding/PROJECT_CODING_SPEC.md`、`AI_CODING_GUIDE.md`、`examples` 两个文件
- Test: `PostgresqlSchemaLiveTest`

- [ ] **Step 1:** 只读核对真实库。Expected：`ai_chat_conversation`、`ai_chat_conversation_message`、`ai_chat_memory`、`ai_chat_memory_summary`、`ai_chat_message`、`ai_file_object`、`ai_integration_outbox`、`ai_model_selection`、`ai_patient_memory`、`ai_questionnaire_answer`、`ai_questionnaire_session`、`ai_questionnaire_template`、`ai_role_prompt`、`ai_wechat_session`、`ai_wechat_user`；不一致就以真实库为准并记 `Ruling:`。
- [ ] **Step 2:** `PostgresqlSchemaLiveTest` 基础列清单用 `sort`，新增 `noTableKeepsSortingColumn`。Expected：带 PG 运行时失败。
- [ ] **Step 3:** 写迁移；H2 建表脚本里的列改为 `sort`；改文档。Expected：不带与带 PG 全过；`check-secrets` 通过；bootJar 里是 utils 2.0.9。
- [ ] **Step 4:** 在 `codex/phase6-sort` 提交，先不合入。

### Task 17: ai-agent、rag、mcp、report 跟升

**Files:**
- Modify: 四个仓库 `build.gradle`（utils 2.0.9、版本）；rag、mcp、report 的 `src/test/java/com/kellen/database/FlywayMigrationLiveTest.java`（`BASE_COLUMNS` 里 `sorting` 改 `sort`）；各仓库 `docs/ai-coding/PROJECT_CODING_SPEC.md`（ai-agent 没有这份）与 `examples` 两个文件

- [ ] **Step 1:** 改 `BASE_COLUMNS` 与文档、升 utils。Expected：四个仓库不带与带 PG 全过（rag、mcp、report 的 `everyTableHasAllBaseColumns` 以 `sort` 检查）；`check-secrets` 通过；bootJar 里是 utils 2.0.9。
- [ ] **Step 2:** 各自在 `codex/phase6-sort` 提交，先不合入。

### Task 18: admin-web 改字段（1.2.4）

**Files:**
- Modify: `admin-web/src/services/auth/types.d.ts`（7处 `sort?: number`）、`src/utils/deptTree.ts`、`src/utils/resourceTree.ts`（按 `sort` 排）、`src/pages/System/Dept/index.tsx`、`Resource/index.tsx`、`Role/index.tsx`（列 `dataIndex` 与表单 `name`、默认值）、`e2e/fixtures/*.json`、`package.json`（version 1.2.4）
- Test: 新增 `tests/unit/auth-tree-sort.test.ts`

- [ ] **Step 1:** 从最新 `origin/main` 建 `codex/phase6-sort`。写失败测试：`buildDeptTree orders siblings by sort ascending`、`buildResourceTree orders siblings by sort ascending`（同级 `sort` 为3、1、2 时顺序为1、2、3，缺省按0）。Expected：失败。
- [ ] **Step 2:** 改字段。Expected：前端全套门禁全过；`rg -n -w sorting src e2e` 无结果。
- [ ] **Step 3:** 提交 `feat!: 排序字段改名 sort（1.2.4）`，先不合入。

### Task 19: 合入与上线

- [ ] **Step 1:** 各仓库 fetch，`codex/phase6-sort` 若需 rebase 就重跑本仓库验证。向用户确认上线：要停全部服务、备份并改四个库。Expected：用户同意。
- [ ] **Step 2:** user、message、ai、ai-memory、ai-agent、rag、mcp、report、admin-web 快进合入 `main`、推送、删短分支。Expected：各精确提交 CI success（user、message、ai 手动触发并核 headSha）。
- [ ] **Step 3:** 请用户在 IDEA 停下9个服务。Expected：进程都已退出。
- [ ] **Step 4:** 只读查四个库的 `undo_log` 行数。Expected：都为0；不为0就停下报告用户。
- [ ] **Step 5:** pg_dump 自定义格式备份四个库到 `~/.config/kellen-github/phase6/backup/<库>-before-sort-20261122.dump`。Expected：文件0600；`pg_restore --list` 对每个备份都成功。
- [ ] **Step 6:** 请用户 Reload All Gradle Projects 后启动9个服务。Expected：health 都是 UP；四个库的 `flyway_schema_history` 各多一条成功的迁移（user V7、message V7、ai V8、ai-memory V6）。
- [ ] **Step 7:** 读回 `information_schema`。Expected：四个库都没有 `sorting` 列；`sort` 列所在的表等于任务14至16核对过的清单。
- [ ] **Step 8:** 两遍全链路（带 / 不带灰度授权）。Expected：最后一行分别为 `phase 5 chain verification OK（带灰度授权）`、`（不带灰度授权）`。
- [ ] **Step 9:** `run_chain_verify.py --admin-web-e2e`。Expected：通过。

### Task 20: 收尾

- [ ] **Step 1:** 检索关口：`rg -n -w sorting utils user message ai ai-memory ai-agent rag mcp report admin-web --glob '!**/build/**' --glob '!**/node_modules/**' --glob '!**/docs/superpowers/**' --glob '!**/db/migration/**'`。Expected：只剩 `utils/ops/github-dev/acceptance.json`（首次迁移快照）、`admin-web/tests/unit/rag-document-model.test.ts`（英文用词）与 utils README 迁移步骤里写到的旧名。
- [ ] **Step 2:** 设计逐节对证据；按选定的执行方式做最终复查；汇总全部 `Ruling:` 与推迟的小问题；清理本计划的短分支。
- [ ] **Step 3:** 第5阶段与本阶段的备份、旧 OTLP 令牌文件按用户决定处理。
- [ ] **Step 4:** 用 `llm-wiki-archive` 归档（台账全文进证据页附录），同步6阶段路线页、相关仓库页与交接快照，以“已提交推送 <hash>”为准；台账目录在归档后由用户在终端删除。
