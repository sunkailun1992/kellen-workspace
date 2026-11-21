# 第6阶段 前端升级设计

## 已确认目标

用户2026-11-13定6阶段路线，第6阶段内容为：admin-web 升 React 19、antd 6、Umi 4.7、ProComponents 3；拆微前端时用 Umi 自带的 qiankun；与后端冲突的接口字段（第3阶段为兼容保留的）在这一阶段一起改。

第3阶段设计（2026-11-15，`2026-11-15-phase3-flyway-entity-design.md`）写明：admin-web 在用的 `sorting`、`code`、`version`、`tenantId` 当时不改，“`sorting`→`sort` 以及 admin-web 在用字段的改名：第6阶段与前端一起”。`code`、`version`、`tenantId` 本身就是主流写法，所以本阶段要改的接口字段只有 `sorting`→`sort`。

用户2026-11-22在会话里（新加坡时间）：

- 00:25 选「在这里完成此任务」：第6阶段在第5阶段同一会话里做。
- 00:44「开始第六阶段吧」：没有回答“要不要拆微前端”，由我按依据定（见方案比较），用户过目本设计时可以改。

沿用的规则：所有升级先查 GitHub 多个主流项目的做法（以多数为准，不以单个项目为准）与官方文档、标准和论文，不为缩小改动范围偷懒（用户2026-11-20定）；计划内事项不逐步询问，计划外、破坏性、对外发布的动作先确认（2026-11-15）。

第5阶段留下、本阶段必须保持的约定：浏览器经网关 BFF 会话登录，前端与网关同源；写请求带 `X-XSRF-TOKEN` 请求头（网关1.3.4起只认请求头）；登录回调失败跳 `/login?error`，由 admin-web 的登录失败页承接（不检查会话、不自动跳登录）。

## 修订记录

- 2026-11-22 初稿（b11e1a4）；用户01:30批准。
- 2026-11-22 写计划时核对：admin-web 的 `.umirc.ts` 已开 `utoopack: {}`（官方模板同样），“构建工具”两处措辞改为保持现有的 utoopack，做法不变。

## 目标与反目标

目标：

1. 运行时依赖与官方模板 Ant Design Pro 6 同代：React 19.3、antd 6.7、@ant-design/icons 6.3、@ant-design/pro-components 3.1（beta 线）、@umijs/max 4.7.36。
2. antd 6、ProComponents 3、React 19 弃用或删除的写法一次改完：每个菜单页打开时没有页面错误，控制台没有错误和弃用警告（这些 API 在 antd 7 删除）。
3. 工具链按主流多数做法换新并进 CI：Node 26（Active LTS）、pnpm 12、TypeScript 6.0 类型检查、ESLint 10 加 Prettier 3、Vitest 单元测试、Playwright 浏览器测试、提交钩子。
4. `sorting`→`sort` 前后端同一批改完：数据库列、utils 实体基类、各服务、admin-web、各仓库规范与示例。
5. 页面功能不变：登录、会话检查、租户切换、按权限出菜单、CSRF、登录失败页，以及系统管理、消息、AI 模型、统一配置、RAG、工作台各页。

反目标（出现即为失败，哪怕构建通过）：

- 为了通过检查去压警告或规则：关严格模式、加 eslint-disable、用 overrides 把 v3 强塞给需要 v2 的依赖、放宽测试断言。
- 页面结构、交互和接口语义被顺手改动；除 `sort` 改名外改后端接口。
- 改名漏掉任何一张表、一个接口字段或一处排序，导致排序静默失效。
- 拆微前端、换掉现有的构建工具 utoopack，或换掉 Umi。

## 判定口径

- 选型：每项做法对照下文10个主流后台项目，以多数为准。官方模板 Ant Design Pro 是其中之一，不单独以它为准；只有 Umi 专属的问题（布局插件、overrides）只有它可比，照它并写明。
- 版本：取 npm 正式版 latest；工具之间有兼容约束时，取满足约束的最新版并写明（TypeScript 取 6.0.3，见方案比较）。ProComponents 只有 3.x 支持 antd 6，而 3.x 还没有正式版，用 beta，锁定精确版本并提交锁文件。
- 写法：以 antd 6、ProComponents 3、React 19 的官方迁移指南为准；指南没写、官方模板有现成写法的，照模板（布局右侧区域）。两份官方材料冲突时，以运行时实际行为为准并写明（overrides）。
- 字段名沿用第3阶段定的 `sort`。
- 版本号按各仓库 `VERSIONING_SPEC`：MINOR、PATCH 各为0–9，逢9进位，不跳号；破坏性改动也按这个顺序，另写迁移、影响与回滚（admin-web 规范第6条、ai 规范第5条）。先例：utils 改审计字段名为 `feat!` 只从1.7.0升到1.7.1；user、ai 第3阶段改列名也各只顺延一号。

## 主流依据

### GitHub主流项目

2026-11-22读各仓库默认分支的根目录与 `package.json`（monorepo 只看根目录，“未见”指根目录没有）：

| 项目 | 星标 | 单元测试 | 浏览器测试 | 代码检查 | 格式化 | TypeScript | 包管理 | 提交钩子 |
|---|---|---|---|---|---|---|---|---|
| ant-design/ant-design-pro | 3.9万 | Vitest 5 | Playwright | Biome | Biome | 7.0 | npm | husky、lint-staged |
| refinedev/refine | 3.6万 | Vitest 2 | Cypress | Biome | Biome、Prettier 2 | 5.8 | pnpm 9 | husky、lint-staged |
| vbenjs/vue-vben-admin | 3.4万 | Vitest 4 | Playwright | ESLint 10、oxlint | oxfmt | 6.0 | pnpm 11 | lefthook |
| marmelab/react-admin | 2.7万 | Jest 29 | Cypress | ESLint 9 | Prettier 3 | 5.8 | yarn 4 | husky、lint-staged |
| pure-admin/vue-pure-admin | 2.1万 | 未见 | 未见 | ESLint 10 | Prettier 3 | 6.0 | pnpm 11 | husky、lint-staged |
| satnaing/shadcn-admin | 1.7万 | Vitest 4（浏览器模式，Playwright 驱动） | 未见 | ESLint 10 | Prettier 3 | 6.0 | 未声明 | 未见 |
| soybeanjs/soybean-admin | 1.5万 | 未见 | 未见 | ESLint 10 | 未见 Prettier | 未声明 | pnpm 12 | 未见 |
| ant-design/pro-components | 0.5万 | Vitest 4 | 未见 | oxlint | Prettier 3 | 6.0（另装 7.0） | pnpm 12 | 未见 |
| yudaocode/yudao-ui-admin-vue3 | 0.4万 | 未见 | 未见 | ESLint 10 | Prettier 3 | 6.0 | pnpm 10 | lint-staged |
| d3george/slash-admin | 0.3万 | 未见 | 未见 | Biome | Biome | 5.6 | pnpm 10 | lefthook |

多数结论：

- 单元测试：有测试的6个里5个用 Vitest，1个用 Jest。
- 端到端浏览器测试：有的4个里 Playwright、Cypress 各2个，样本打平；shadcn-admin 另用 Playwright 驱动 Vitest 的浏览器模式。按生态用量取 Playwright：npm 最近一周（2026-11-14至20）下载 @playwright/test 约1.39亿次，cypress 约763万次。
- 代码检查：ESLint 6个，Biome 3个，oxlint 2个（vben 两样都用）。
- 格式化：Prettier 6个，Biome 3个，oxfmt 1个。
- 同一周的 npm 下载也是同样的排序：vitest 约1.95亿、jest 约6295万；eslint 约2.17亿、oxlint 约5381万、@biomejs/biome 约3021万；prettier 约1.96亿。
- TypeScript：6.0 有5个，5.x 有3个；只用 7.0 的只有官方模板，pro-components 是 6.0 为主、另装 7.0。
- 包管理：pnpm 7个。
- 提交钩子：7个配了，husky 最多（4个），lefthook 2个；commitlint 恰好5个，不算多数。
- 芋道 ruoyi-vue-pro：排序字段为 `sort`（第3阶段按主流多数定名时的依据）。

Umi 专属的事实（只有官方模板可比）：Ant Design Pro master 为 6.0.3（v6.0.0 发布于2026-04-30），依赖 react ^19.3.1、antd ^6.6.7、@ant-design/pro-components ^3.1.14-2、@ant-design/icons ^6.3.4、@umijs/max ^4.7.27；运行时布局用 `actionsRender` 与 `avatarProps`；用 npm overrides 让 `@umijs/plugins` 改用项目自己的 ProComponents；CI 的 action 固定到完整提交 SHA。

### 标准与指南

- React 19 Upgrade Guide（react.dev，2024-04-25）：删除函数组件的 `propTypes`/`defaultProps`、字符串 ref、旧 Context、`ReactDOM.render` 等；类型上 `useRef` 必须传初始值，JSX 改为 `React.JSX` 命名空间。
- antd v6 迁移指南（ant-design 仓库 `docs/react/migration-v6.en-US.md`）：先升到 v5 最新版、清掉控制台的弃用警告再升 v6；React ≥18；默认 CSS 变量，只支持现代浏览器；不再需要 `@ant-design/v5-patch-for-react-19`；`@ant-design/icons` 必须 ≥6，且 icons 6 与 antd 5 不兼容，两者一起升；弃用 API 在 v6 仍可用但打印警告，v7 删除，本项目涉及 Alert `message`→`title`、Space `direction`→`orientation`、Drawer `width`→`size`、Modal `destroyOnClose`→`destroyOnHidden`、`bordered`→`variant`、输入框 `addonBefore`/`addonAfter`→`Space.Compact`；尺寸统一为 `large`/`medium`/`small`，旧值打印警告。
- ProComponents 3 迁移指南与 API 变更页（仓库 `site/en-US/docs/`）：3.x 只发单包 `@ant-design/pro-components`，要求 antd ≥6、React ≥18；删除项有 ProTable 的 `columnsStateMap`、`hideInSearch`、`fixHeader`、列 `tip`，ProCard 的 `TabPane`，ProLayout 的 `rightContentRender`，字段的 `plain` 等；不建议用 overrides 把 v3 强塞给需要 v2 的依赖。npm 上 3.x 从2025-07-24（3.0.0-beta.1）到2026-11-01（3.1.15-9）共38个版本，没有正式版。
- Umi：@umijs/plugins 4.7.36 的布局插件（`dist/layout.js` 的 `getPkgPath`）优先用项目自己声明的 ProComponents，自带的 v2 只在项目没声明时兜底；按 antd `^5.0.0 || ^6.0.0` 选样式文件；仍把右侧区域作为 `rightContentRender` 传给 ProLayout。它的 qiankun 插件依赖 qiankun 2.10.17-beta.0（qiankun 正式版最新为2.10.16，3.0 仍是 rc）。
- TypeScript：7.0（Go 原生实现，npm latest 7.0.2）只带 `tsc` 命令，没有 JS 接口；6.0.3 是最后一个带 JS 接口的版本。typescript-eslint 8.73.0 的 peer 要求 `typescript >=4.8.4 <6.1.0`；prettier-plugin-organize-imports 用 TypeScript 的语言服务。
- Node.js 发布计划（nodejs/Release 仓库 `schedule.json`）：20 已于2026-04-30停止支持；22、24 已进入维护期；26 自2026-10-28起为 Active LTS，支持到2029-04-30。Vitest 5.1.1 要求 Node `^22.12.0 || ^24.0.0 || >=26.0.0`。
- GitHub Actions 安全加固指南与 OpenSSF Scorecard「Pinned-Dependencies」：action 固定到完整提交 SHA。本机 user 等 Java 仓库的 CI 已这样做，官方模板也是。
- OWASP CSRF Prevention Cheat Sheet：单页应用用自定义请求头携带令牌（第5阶段已落实，本阶段保持）。

### 论文与书

- Peltonen, Mezzalira, Taibi. Motivations, benefits, and issues for adopting Micro-Frontends: A Multivocal Literature Review. Information and Software Technology 136 (2021)。微前端的主要收益来自多个自治团队独立开发与发布，代价是复杂度、包体积与体验一致性——单人维护一个后台时成本大于收益。另见 Cam Jackson, Micro Frontends（martinfowler.com，2019）。
- Bogart, Kästner, Herbsleb, Thung. How to Break an API: Cost Negotiation and Community Values in Three Software Ecosystems. FSE 2016。破坏性变更的成本主要落在依赖方的迁移上；跟随上游弃用提示一次迁完、锁定版本、用测试兜底是常见做法。
- Ambler, Sadalage. Refactoring Databases: Evolutionary Database Design（2006）。“Rename Column”重构在多个应用共用一张表时需要过渡期（新旧列并存、触发器同步）；本项目每张表只由一个服务读写、前后端同批发布，直接改名，不留过渡列。
- Feathers. Working Effectively with Legacy Code（2004）。改动现有代码之前先写刻画现有行为的测试，再在测试保护下修改；对应本设计“先立门禁、再升级”的顺序。

## 方案比较

### 依赖升级路径

| 方案 | 做法 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A 一步到位（照官方模板） | React 19 + antd 6 + ProComponents 3 beta + Umi Max 4.7；弃用写法一次改完 | 与官方模板同代；只迁移一次 | 依赖 beta 版 | **采用** |
| B 先全正式版，分两步 | 先 React 19 + antd 5 + ProComponents 2.8，等 ProComponents 3 正式版再升 antd 6 | 全部是正式版 | 同样的迁移做两遍；antd 5 最后一次发布是5.29.3（2025-12-18），之后11个月没有新版；偏离路线定的 antd 6 | 不采用 |
| C 换掉 Umi | Vite + React Router + antd 6 + ProComponents 3 | 社区 SPA 新写法 | 要重写 Umi 插件提供的权限、全局状态、请求与布局；与路线定的 Umi 4.7、qiankun 冲突 | 排除 |

### 工具链

| 方案 | 做法 | 依据 | 代价 | 结论 |
|---|---|---|---|---|
| T1 照官方模板 | TypeScript 7 + Biome（检查与格式化）+ Vitest + Playwright | 官方模板 | 10个项目里检查和格式化用 Biome 的只有3个，只用 TS 7 的只有它一个，违反“不以单个项目为准” | 不采用 |
| T2 主流多数 | TypeScript 6.0.3 + ESLint 10（typescript-eslint、react-hooks）+ Prettier 3 + Vitest + Playwright + husky 与 lint-staged | 各项都是多数（见上表）；端到端测试样本打平，按生态用量取 Playwright | TS 7 的类型检查提速暂时用不上：typescript-eslint 还不支持 TS 7 | **采用** |
| T3 保持现状 | `node:test` + 每个测试文件自写转译加载 + Prettier 2，CI 不跑检查与浏览器测试 | — | 测试加载方式与主流不符；Prettier 2 已停更；升级没有浏览器回归兜底 | 不采用 |

TypeScript 7 等 typescript-eslint 的 peer 范围包含 7.x 时再升（列入“不在本阶段”）。

### 微前端（我定，用户过目时可改）

本阶段不拆。依据见上文论文与 Fowler 一文：收益来自多团队自治，本项目单人维护、单一网关、单一后台；qiankun 3 仍在 rc，Umi 的 qiankun 插件用的是 2.10.17-beta.0。以后确有独立开发、独立上线的需要时再拆，届时按路线用 Umi 自带的 qiankun。

### overrides（裁定）

不加。Ant Design Pro 用 overrides 让 `@umijs/plugins` 改用项目的 ProComponents，但 Umi 布局插件运行时本来就优先用项目声明的那一份（源码已核），overrides 只是少装一份用不上的 v2；ProComponents 指南明确不建议这样做。代价：依赖目录里多一份不会打包的 ProComponents v2（及它带的 antd）；构建后核对产物里只有 v3。

## 现状（2026-11-22核对）

- admin-web `main@abcce35`，1.2.1。实际解析：React 18.3.1、antd 5.29.3（即 v5 的最后一版；另有旧依赖带入的 antd 4.24.16）、ProComponents 2.8.10、@umijs/max 4.6.56、TypeScript 5.9.3、pnpm 10.28.2。本机 Node 26.13.1；CI 仍用 Node 20（已停止支持），action 用 `@v4` 标签。
- CI 只跑单元测试、`build:dev` 与密钥扫描，没有格式检查、代码检查、类型检查和浏览器测试。`.eslintrc.js`、`.stylelintrc.js` 用 Umi 自带的 ESLint 8 与 stylelint 14 配置，只在 `.lintstagedrc` 里引用，而 lint-staged 没接提交钩子；Prettier 2.8。
- 74个 ts/tsx 文件；从 antd 导入约50个名字；ProComponents 用到 PageContainer 15处、ModalForm 9、ProTable 7、StatisticCard 4、ProCard 3；图标用在12个文件；样式文件5个。
- 单元测试27个文件、178条，全部通过：`node:test`，每个文件自己用 `ts.transpileModule` 转译源码、在 `vm` 里运行，组件用 `react-dom/server` 渲染后断言。`tsc --noEmit` 在 TS 5.9.3 下0错误。
- 要迁移的写法（按源码计数，实施时以控制台警告为准）：Alert `message` 37处、Space `direction` 13、`useRef()` 无参13、Drawer `width` 6、`bordered` 6、输入框前后缀4、`destroyOnClose` 1、尺寸 `middle`/`default` 1；ProTable 的 `hideInSearch` 等 v3 已删除的写法14处；`forwardRef` 2处（React 19 仍可用）；静态 `message` 调用33处（不在弃用列表）。
- 右上角由 Umi 布局插件的 `rightRender` 渲染（`src/app.ts`，`LayoutRightContent`：头像、账号菜单含设置与退出）；插件把它作为 `rightContentRender` 传给 ProLayout，而 v3 已删除该属性。
- 脚本 `test:services`、`test:request-auth` 写在 README、`docs/ai-coding` 的 TESTING_SPEC 与两份 RAG 说明里；kellen-workspace 不调用 admin-web 的脚本；共享运行配置 `.run/admin-web dev.run.xml` 只用 `dev`。
- `sorting`：
  - 数据库：按迁移脚本推算（建表减去后来删掉的），带该列的表 user 9、message 1、ai 15、ai-memory 7；rag、mcp、report、ai-agent 没有表带它。实施时以真实库 `information_schema` 为准。
  - 代码：utils `EntityBase` 的字段（各服务实体经它或 `TenantEntityBase` 继承）及其测试；user 10个主代码文件（查询、VO、BO）与4个测试；ai 的 H2 测试建表脚本 `schema.sql`、`storage-schema.sql`；user、message、ai、ai-memory 的表结构测试；rag、mcp、report 的 `FlywayMigrationLiveTest` 基础字段清单。user 没有缓存实体的代码。
  - admin-web：6个文件16处（类型定义、部门与资源树排序、角色、部门、资源页）；`tests/unit/rag-document-model.test.mjs` 里的 “sorting” 是英文用词，不改。
  - 文档：9个仓库的 AGENTS、README、`PROJECT_CODING_SPEC`、`AI_CODING_GUIDE` 与 `examples`，共30个文件。
  - 不涉及：rpc-api、gateway、全链路脚本；utils `ops/github-dev/acceptance.json` 是首次迁移的快照（同目录 README 写明不作当前依据），不改；kellen-workspace 的历史设计与计划不改。

## 改动设计

分三部分按顺序交付，每部分合入 main、读回 CI 后再开始下一部分：先立门禁，再升级依赖，最后改名。

### 第一部分：工具链与门禁（admin-web 1.2.1 → 1.2.2，代码仍是 React 18 与 antd 5）

1. 运行环境：CI 改用 Node 26；`package.json` 加 `engines.node: ">=26"`，只声明测过的版本；`packageManager` 升到 pnpm 12.21.0，配置迁移按 pnpm 官方升级说明。
2. TypeScript 5.9.3 → 6.0.3；新增 `typecheck` 脚本（`tsc --noEmit`）。TS 6.0 发布说明里改了默认值的选项（`types` 默认改为 `[]`、`rootDir` 默认改为 tsconfig 所在目录等）在 `tsconfig.json` 显式写出；用到 Node 全局的，显式声明 `@types/node`（与 Node 26 对应）。
3. 代码检查：ESLint 10 扁平配置 `eslint.config.mjs`，用 `@eslint/js`、typescript-eslint、eslint-plugin-react-hooks 各自的 recommended 预设，再加 eslint-config-prettier 关掉与格式化冲突的规则，不自定义规则。存量违规先量出数量，全部按规则文档改写，不加 disable 注释；确属上游误报的，附上游 issue 链接后在配置里按文件关闭该条并记台账。删除 `.eslintrc.js`、`.stylelintrc.js`，不再用 `max lint`（stylelint 在10个项目里不是多数，5个样式文件交给 Prettier）。
4. 格式化：Prettier 2.8 → 3，保留现有 `.prettierrc` 与两个插件（升到当前版本）；全仓格式化单独一个提交，提交号写进 `.git-blame-ignore-revs`（GitHub 的追溯视图会跳过它）；`.prettierignore` 补上构建产物与锁文件。
5. 提交钩子：加 husky，pre-commit 跑 lint-staged（只处理暂存文件：ESLint `--fix` 与 Prettier）；最终门禁仍是 CI。
6. 单元测试：从 `node:test` 换到 Vitest 5（`environment: 'node'`，路径别名与 tsconfig 一致）。27个文件改为 `.test.ts` 或 `.test.tsx`，直接 import 源码，`@umijs/max` 等运行时依赖用 `vi.mock` 替身，删掉各文件自带的转译加载代码。断言改用 `expect`，深比较用 `toStrictEqual`，不放宽；用例不少于178条、名称不变。脚本改为主流的 `test`（`vitest run`），README 与 `docs/ai-coding` 里的命令同步。
7. 浏览器测试：仓库内加 `e2e/` 与 `playwright.config.ts`（Playwright Test），分两组：
   - mock（进 CI）：用构建产物起预览服务（`max preview`），`page.route` 拦截后端接口，返回 `e2e/fixtures/` 里的固定数据（全部是合成数据，含已登录用户与全部权限）。逐个打开 `.umirc.ts` 里的每个菜单页，断言主内容出现、没有页面错误、控制台没有错误与警告；再打开 `/login?error`，断言不跳转、只有「重新登录」。
   - live（本机，经真实网关与 Keycloak）：起开发服务（代理到本机网关），用全链路已在用的管理员测试账号登录，逐页打开同一批页面（断言相同）；在部门页新建再删除一条带 `e2e-` 前缀与时间戳的合成数据，失败也清理；最后退出并确认会话失效。账号与口令只由 `run_chain_verify.py` 按现有方式取用，以环境变量传给 Playwright 进程，不落盘、不打印。
   - 浏览器：本机用系统 Chrome（与全链路脚本一致），CI 用 `playwright install --with-deps chromium`。
   - 警告允许清单默认空。只有能证明来自第三方的才可列入，并注明来源：第一部分里 React 18.3 对旧版第三方库（如 ProComponents v2）的弃用提示可暂列，第二部分结束时必须清空，除非附上游 issue。基线里来自本项目代码的 antd 5 与 React 18.3 弃用警告在本部分改掉（antd 迁移指南第1步）。
8. CI（`.github/workflows/ci.yml`）：action 升到当前大版本（checkout v7、setup-node v7、pnpm/action-setup v6、upload-artifact v7）并固定到完整提交 SHA；顺序为安装 → 格式检查 → 代码检查 → 类型检查 → 单元测试 → `build:dev` → mock 浏览器测试（失败时上传报告）→ 密钥扫描。
9. 文档：README、AGENTS、`docs/ai-coding` 的技术栈、命令与验证说明；TESTING_SPEC 写明两组浏览器测试与允许清单规则。

本部分完成时，在当前 React 18 与 antd 5 代码上全部门禁通过、live 浏览器测试通过，作为升级前的基线。

### 第二部分：依赖升级与弃用迁移（admin-web 1.2.2 → 1.2.3）

1. 依赖：react、react-dom 19.3；@types/react、@types/react-dom 19.3；antd 6.7 与 @ant-design/icons 6.3 一起升；@ant-design/pro-components 取实施当天 npm 上 3.x 最新 beta 并精确锁定（写设计时为3.1.15-9）；@umijs/max 4.7.36。不加 overrides；构建后核对产物里只有 ProComponents v3。
2. 按 antd 6 与 ProComponents 3 的迁移指南逐条改写“现状”列出的写法，以控制台警告清零、警告允许清单清空为准。
3. 布局右侧：照 Ant Design Pro 6，头像与名称放 `avatarProps`，账号菜单（设置、退出）用 `avatarProps.render` 包成下拉，其他按钮放 `actionsRender`；去掉插件专用的 `rightRender`、`logout` 与 `umi-plugin-layout-*` 样式类。退出照旧 `POST /logout` 带 `X-XSRF-TOKEN`。
4. React 19：`useRef()` 补初始值；`forwardRef` 照旧可用，不强改。
5. 测试：补右上角（头像、账号菜单、退出带 CSRF）的单元测试；第一部分的全部门禁照跑。
6. 文档：技术栈与版本说明。

### 第三部分：`sorting` → `sort`（跨仓，同一批上线）

- utils 2.0.8 → 2.0.9：`EntityBase.sorting` 改为 `sort`（列 `sort`）；测试、AGENTS、README、`PROJECT_CODING_SPEC`、`examples` 同步；README 写明迁移步骤与消费方影响。发布后各消费方一起升。
- user 1.5.6 → 1.5.7、message 1.4.5 → 1.4.6、ai 1.9.5 → 1.9.6、ai-memory 1.3.4 → 1.3.5：
  - 新增 Flyway 迁移，名为 `rename_sorting_to_sort`（user `V7`、message `V7`、ai `V8`、ai-memory `V6`）：本库每张带 `sorting` 列的表执行 `ALTER TABLE … RENAME COLUMN sorting TO sort`，数据、注释与索引随列保留，索引名带 `sorting` 的一并改名（如有）。表清单以真实库为准，与上面的推算核对。
  - 查询、VO、BO、排序与手写 SQL 一起改，接口 JSON 字段随之变为 `sort`；ai 的 H2 测试建表脚本与各服务的表结构测试同步；迁移测试确认新列在、旧列不在。
- ai-agent 0.4.4 → 0.4.5、rag 1.4.4 → 1.4.5、mcp 1.4.4 → 1.4.5、report 1.3.4 → 1.3.5：升 utils 2.0.9；rag、mcp、report 的 `FlywayMigrationLiveTest` 基础字段清单改为 `sort`；文档与示例同步。
- admin-web 1.2.3 → 1.2.4：类型定义、部门与资源树排序、角色、部门、资源页的表单与列改为 `sort`。
- 上线顺序：用户在 IDEA 停下全部服务 → 确认四个库的 `undo_log` 为空（Seata AT 的回滚镜像里记的是列名）→ 用 pg_dump 备份四个库（沿用第5阶段改 user 库前备份的做法，存本机受限目录，不进 Git）→ 用户启动服务，Flyway 在启动时改名 → 两遍全链路 → live 浏览器测试。
- 不动：rpc-api（契约里没有该字段）、gateway。

### 运维与验证工具

- `run_chain_verify.py` 加一个运行 admin-web live 浏览器测试的入口（改前备份）：口令取用沿用它现有的方式，以环境变量传给子进程，输出照旧遮掩。
- 全链路脚本本身不涉及 `sorting`，不改。

## 验证

- 每部分：格式检查、代码检查、类型检查、单元测试、`build:dev`、mock 浏览器测试在本机与 CI 都通过；live 浏览器测试在本机通过。
- 第二部分另核：警告允许清单为空；产物里只有 ProComponents v3。
- 第三部分另核：各服务带 PG 测试库全量通过；bootJar 里的 utils 为 2.0.9；真实库改名后读回 `information_schema`，新列在、旧列不在；用户重启后两遍全链路（带 / 不带灰度授权）与 live 浏览器测试通过。
- 变异检查：新门禁、右上角、改名的关键处各故意改坏一次，测试要能发现。
- 各仓库精确提交的 CI 全绿；user、message、ai 推送后手动触发并核对 headSha。

## 回退

- admin-web：三部分各是一个版本，用 revert 提交退回（不改历史，版本号照常顺延）；第三部分要与后端一起退。
- 后端：Flyway 社区版没有撤销迁移，旧版本服务也认不出已执行的新迁移；回退时恢复改名前的备份，再启动上一版服务（utils 2.0.8）。
- ProComponents 3 beta 遇到阻断性缺陷且上游短期不修：停在第一部分完成的状态，评估退到方案 B，由用户定。

## 不在本阶段

- 拆微前端（qiankun）；换构建工具（保持现有的 utoopack）；Keycloak 登录页主题；国际化；视觉改版；Spring Boot 4.1 那一轮。
- TypeScript 7：等 typescript-eslint 支持 7.x 再升。
- Testing Library：现有组件测试用服务端渲染后断言，真实交互由浏览器测试覆盖，本阶段不引入。
- commitlint：10个项目里恰好一半在用，不算多数；提交说明沿用现有中文约定。
- 其他仓库 CI 的 action 大版本：Java 仓库已固定 SHA（仍是 v4），不随前端阶段改。
- 静态 `message` 改 `App.useApp()`：不在 antd 6 的弃用列表；只有控制台出现相关警告才改。

## 需要安装的工具（设计批准后安装）

- npm 依赖随 pnpm 装进 admin-web（属项目依赖改动，随本设计批准）。
- pnpm 12：按 `packageManager` 由本机 pnpm 10.28.2 自动下载对应版本（pnpm 默认开启 manage-package-manager-versions），不改全局安装。
- 浏览器：本机用已装的 Chrome；CI 在运行时安装 Playwright Chromium。

## 留给实施确定的细节

- ProComponents 3 的精确版本（规则见第二部分第1条）。
- 各仓库版本号：实施时 fetch 后按规范核对远端 main、在途分支、tag 与私库占用，已被占用就按编号顺序顺延。
- 每张表的改名清单（以真实库为准）；各服务有没有缓存或消息载荷存了带 `sorting` 的 JSON，有则上线时清掉或写明影响。
- ESLint 存量违规的数量与逐条改法。
- pnpm 12 与 TypeScript 6 的配置迁移细节（按官方说明）。
- Umi 4.7.36 在 Node 26 上若有不兼容，改用 Node 24 并写明。
- 浏览器测试的固定数据、页面清单与等待条件（用有总时限的条件等待，不用固定等待）。
