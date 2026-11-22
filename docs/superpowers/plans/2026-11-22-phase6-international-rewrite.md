# 第6阶段第四部分：admin-web 按国际主流重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删掉 Umi 旧应用，在 admin-web 里用 Vite、React Router、Module Federation 2.0、shadcn/ui 等国际主流方案，重建一个主应用和四个子应用；物料、主题、插件做成共享包；功能与 1.2.4 对齐。

**Architecture:**
- pnpm workspace 单仓，由 Turborepo 编排任务。
- `apps/shell` 是 Module Federation 的宿主。`apps/{system,message,ai,rag}` 是远程模块，主应用按 `apps.json` 注册它们，启动时并行加载各自的 `./routes`。
- `packages/*` 放共享包：
  - 主题：DTCG 令牌经 Style Dictionary 生成 CSS 变量；
  - 物料：shadcn/ui 源码与业务组件，配 Storybook 和注册表；
  - 请求与会话：契约照搬 1.2.4；
  - 多语言；
  - 构建预设。
- 在一个短分支上整个替换，满足合入条件后快进合入。

**Tech Stack:** Node 26、pnpm 12.21.0、Turborepo 2.11、Vite 8.3、React 19.3、React Router 8.5、@module-federation/vite 1.24、Tailwind CSS 4.3、shadcn/ui（CLI 4.23）、TanStack Query 5 与 Table 9、react-hook-form 7 与 zod 4、axios 1.21、i18next 27、Recharts 3、Cytoscape.js 3.35、Storybook 10.6、Style Dictionary 5.8、Vitest 5、Testing Library、MSW 3、Playwright 1.65、TypeScript 6.0、ESLint 10、Prettier 3。

**Spec:** `docs/superpowers/specs/2026-11-22-phase6-international-rewrite-design.md`

## Global Constraints

- **前置条件**：第6阶段计划的第三部分（任务13–19）已完成，admin-web main 为 1.2.4，字段已改为 `sort`。本计划从那之后的 `origin/main` 开始。
- **范围**：
  - 只动 admin-web（重写）和 kellen-workspace（文档）。
  - 不改后端、网关、Keycloak、NAS。
  - `~/.config/kellen-github/tools/run_chain_verify.py` 不改，所以根脚本保留 `test:e2e:test`（真实网关一组）。
- **判定口径**：只看国际主流（用户 11:58）。偏离设计或主流做法时，在台账写 `Ruling:` 并附依据，规则同第6阶段。
- **整个替换**（用户 12:15）：任务1 删除旧应用。替换后，仓库里不得有 `umi`、`@umijs/*`、`antd`、`@ant-design/*`、`@antv/*` 的依赖或引用，任务15 检查。
- **分支**：admin-web 只用一个短分支 `codex/phase6-rewrite`，从最新 `origin/main` 建（`--no-track`）。全部任务提交在它上面，任务15 一次快进合入。不强推；推送后读回精确提交的 CI。
- **版本**：
  - 合入时按 `VERSIONING_SPEC.md` 取下一个号，即 1.2.5；用户指定 2.0.0 就用 2.0.0。
  - 根 `package.json` 和五个应用合入时同号。
  - 共享包设为 `"private": true`、`"version": "0.0.0"`，不接 Changesets（设计修订 12:30）。
- **依赖版本**：取写计划时的 npm latest，放进 pnpm catalog，用 `^` 加锁文件。执行时若有更新的稳定版，就取新的并记台账。
  - react、react-dom `^19.3.1`
  - react-router `^8.5.2`
  - vite `^8.3.8`
  - @vitejs/plugin-react `^6.3.0`
  - @module-federation/vite `^1.24.1`
  - @module-federation/runtime、@module-federation/retry-plugin `^2.9.3`
  - tailwindcss、@tailwindcss/vite `^4.3.5`
  - shadcn `4.23.0`（CLI，用 `pnpm dlx`）
  - lucide-react `^1.77.0`
  - sonner `^2.0.8`
  - @tanstack/react-query `^5.108.2`
  - @tanstack/react-table `^9.3.1`
  - react-hook-form `^7.92.0`
  - zod `^4.6.7`
  - @hookform/resolvers `^7.0.8`
  - axios `^1.21.1`
  - recharts `^3.11.0`
  - cytoscape `^3.35.0`
  - i18next `^27.2.3`
  - react-i18next `^18.0.3`
  - storybook、@storybook/react-vite `^10.6.8`
  - style-dictionary `^5.8.0`
  - colorjs.io `^0.7.1`
  - turbo `^2.11.13`
  - vitest `^5.1.1`
  - @testing-library/react `^16.3.3`
  - msw `^3.0.7`
  - @playwright/test `^1.65.1`
  - eslint `^10.15.0`
  - typescript-eslint `^8.73.0`
  - prettier `^3.10.1`
- **TypeScript 留在 6.0**：typescript-eslint 8.73.0 的 peer 是 `>=4.8.4 <6.1.0`，不上 7.0。任务1 记一条 `Ruling:`。
- **运行环境**：`engines.node` 为 `">=26"`，`packageManager` 为 `pnpm@12.21.0`。CI 的四个 action 沿用现有 `ci.yml` 锁定的完整 SHA。
- **测试稳定性**：
  - Playwright `retries: 0`；
  - 条件等待都带总时限；
  - 结果不依赖真实时间和执行顺序；
  - 不加允许清单掩盖控制台警告。
- **契约照搬 1.2.4**，名称和取值都不变：
  - 登录入口 `/oauth2/authorization/keycloak`。
  - 登录失败页 `/login?error`，只给“重新登录”。
  - 退出：先清掉选中的租户，再 `POST /logout`。XHR 回 202 和 `Location` 时整页导航过去，失败时导航到 `/`。
  - 请求头：
    - `X-Requested-With: XMLHttpRequest`；
    - 写请求带 `X-XSRF-TOKEN`，值取自 `XSRF-TOKEN` Cookie；
    - `X-Tenant-Id` 取自 localStorage 的 `admin_web_selected_tenant_id`；
    - 不带 `Authorization`。
  - 灰度头：`X-Release-Version`（默认 `1.0.0`）、`X-Traffic-Lane`（默认 `stable`），可选 `X-Canary-Tag`、`X-Canary-Weight`。取值必须匹配 `/^[A-Za-z0-9._:-]{1,64}$/`，不匹配就丢掉。
  - 响应包装为 `{ success, data, code, errorMessage | msg }`。`success === false` 时抛错，提示取 `errorMessage`、`msg`，都没有则为“请求处理失败”。
  - 错误处理：
    - 取消的请求不提示；
    - 401 整页跳登录，并发多个 401 也只跳一次；
    - 403 提示服务端信息，没有就提示“无权限访问”；
    - `POST /api/rag/graphs/sync` 带合法的 `X-Graph-Submission-Key`（`/^[A-Za-z0-9_-]{16,128}$/`）且没有状态码的失败，不提示，由页面自己查回；
    - 其余依次提示服务端信息、错误信息或“网络请求异常”。
  - 会话启动：取当前资源和可切换的租户。401 或 `A0220`、`A0230` 视为身份被拒，跳登录；资源格式不对时进恢复页（`resources-unavailable`）。
  - 权限：照搬 1.2.4 `src/access.ts` 的全部标志位和判断，以 1.2.4 的提交为准。
- **环境变量**：
  - `ADMIN_WEB_ENV` 的 dev、test、prod 对应 Vite 的 `--mode`。
  - `.env.<mode>` 里的 `UMI_APP_RELEASE_VERSION`、`UMI_APP_TRAFFIC_LANE`、`UMI_APP_CANARY_TAG`、`UMI_APP_CANARY_WEIGHT` 改名为 `VITE_RELEASE_VERSION`、`VITE_TRAFFIC_LANE`、`VITE_CANARY_TAG`、`VITE_CANARY_WEIGHT`。
  - `ADMIN_WEB_GATEWAY_URL` 不变，只给代理用，不进浏览器。
  - 删掉 `UMI_APP_BACKEND_BASE_URL`，一律同源。
  - 取值都不变，每个应用目录各放一份。
- **端口**：
  - 开发：主应用 8000，子应用 8101–8104（依次为 system、message、ai、rag）。
  - 浏览器测试用构建产物加 `vite preview`：固定数据一组主应用 8001，真实网关一组主应用 8000；子应用两组都用 8201–8204。
- **包名**：共享包为 `@admin-web/{config-typescript,config-eslint,config-vite,theme,theme-emerald,ui,api,auth,i18n,test-utils}`；应用为 `@admin-web/{shell,system,message,ai,rag}`。Module Federation 的 name 分别是 `shell`、`system`、`message`、`ai`、`rag`。
- **共享单例**：react、react-dom、react-router、@tanstack/react-query、i18next、react-i18next、sonner、`@admin-web/{ui,api,auth,i18n}`。
- **请求客户端**：每个应用在自己的 `src/http.ts` 用 `createHttpClient` 建一个客户端，灰度头取本应用构建时的 `import.meta.env.VITE_*`。租户、401 跳登录、提示都走共享的 `@admin-web/auth` 和 sonner。
- **页面清单**：与 1.2.4 对齐，路径 → 归属 → 权限标志位如下。
  - `/dashboard` → shell → 不受控
  - `/system/tenant` → system → `canSeeTenant`
  - `/system/dept` → system → `canSeeDept`
  - `/system/user` → system → `canSeeUser`
  - `/system/role` → system → `canSeeRole`
  - `/system/resource` → system → `canSeeResource`
  - `/message/send` → message → `canSeeMessageSend`
  - `/ai/model` → ai → `canSeeAiModel`
  - `/configuration/models` → ai → `canManageConfiguration`
  - `/configuration/storage` → ai → `canManageConfiguration`
  - `/rag-knowledge/documents` → rag → `canSeeRagDocument`
  - `/rag-knowledge/graph` → rag → `canSeeRagGraph`
  - `/login?error` → shell
- **行为来源**：各页面、组件和工具的行为以 1.2.4 提交里的对应文件为准，任务里写的是旧路径。用 `git show <1.2.4 的提交>:<路径>` 查看。
- **命令**（都在根目录跑）：
  - `pnpm format:check`、`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build:dev`、`pnpm test:e2e`；
  - `pnpm check` 跑以上全部；
  - `pnpm test:e2e:test` 只经 `python3 -I ~/.config/kellen-github/tools/run_chain_verify.py --admin-web-e2e` 运行。
- **凭据、时间、台账**：
  - 凭据规则同第6阶段，测试口令只在子进程环境里用。
  - 时间写新加坡时间。
  - 台账为 `kellen-workspace/.superpowers/sdd/2026-11-22-phase6-international-rewrite/progress.md`。

## Review Focus

1. **某个子应用的清单取不到或很慢**：主应用的布局和其它域照常，只有该域显示错误页和“重试”，它的菜单项标为不可用。对应任务8 的 `remote load failure only disables its own domain`，以及任务14 的 `remote-failure.spec.ts`。
2. **用着用着会话过期**：任何子应用的请求回 401，整页跳登录入口只跳一次，不出现半截界面或循环跳转。对应任务5 的 `401 redirects once across concurrent requests`。
3. **切换租户**：各子应用缓存的查询全部清掉，后续请求带新的 `X-Tenant-Id`，不显示上一个租户的数据。对应任务8 的 `switching tenant clears the shared query cache`，以及任务14 的 `tenant-switch.spec.ts`。
4. **直接打开或刷新子应用里的页面地址**（如 `/system/user`）：预览构建下直接进页面，不是 404。对应任务14 的 `deep-link.spec.ts`。
5. **没有某页权限的用户**：菜单里看不到，直接输地址进入时看到 403 页。对应任务5 的权限表驱动用例，以及任务14 的 `access.spec.ts`（受限固定数据）。

---

### Task 1: 删除旧应用，建单仓骨架和配置包

**Files:**
- Delete:
  - 旧应用：`src/`、`config/`、`.umirc.ts`、`tests/`；
  - 旧浏览器测试：`e2e/mock/`、`e2e/live/`、`e2e/support/`、`e2e/warm-up.setup.ts`；
  - 旧配置：`playwright.config.ts`、`vitest.config.mts`、`tsconfig.json`，以及 Umi 专用的其它文件。
- Keep: `e2e/fixtures/*.json`、`.env.*`（任务8–12 分到各应用后删掉根目录这份）、`docs/`、`scripts/check-secrets.sh`、`.github/workflows/ci.yml`、`.husky/`、Prettier 配置、`README.md`、`AGENTS.md`
- Create:
  - 根目录：`pnpm-workspace.yaml`（`apps/*`、`packages/*`，含 catalog）、`package.json`、`turbo.json`、`eslint.config.js`、`vitest.config.ts`（`test.projects`）、`.gitignore`
  - `packages/config-typescript/{package.json,base.json,react.json}`
  - `packages/config-eslint/{package.json,index.js,boundaries.js}`
- Test: `packages/config-eslint/test/boundaries.test.ts`

**Interfaces:**
- Produces:
  - `@admin-web/config-eslint`：默认导出扁平配置数组，严格程度与 1.2.3 相同（typescript-eslint 带类型检查、react-hooks 7 推荐规则、`--max-warnings 0`）。另导出 `appBoundaries(appName: string)`，禁止 `apps/<其它应用>/**` 的相对引用和 `@admin-web/*/src/**` 的深层引用；导出 `noHardcodedCjk`，禁止 `apps/*/src` 里出现含中日韩字符的 JSX 文本和字符串字面量，测试文件除外。两条都用 ESLint 内置规则（`no-restricted-imports`、`no-restricted-syntax`）实现。
  - 根脚本：`dev`、`build`、`build:dev`、`build:test`、`lint`、`typecheck`、`test`、`format`、`format:check`、`test:e2e`、`test:e2e:test`、`check`，都经 `turbo run`。

- [ ] **Step 1:** fetch，从最新 `origin/main` 建 `codex/phase6-rewrite`。Expected：根 `package.json` 版本为 1.2.4，`git grep -n -w sorting -- src` 无结果。
- [ ] **Step 2:** 写失败测试 `boundaries.test.ts`，用 ESLint 的 `Linter` 对内联代码跑配置。下面的“文件”指传给 `Linter` 的文件名。
  - 文件 `apps/system/src/a.ts` 里写 `import x from '../../rag/src/x'` → 1 个错误，消息含“子应用之间”；
  - 写 `import { Button } from '@admin-web/ui'` → 0 个错误；
  - 写 `import x from '@admin-web/ui/src/components/button'` → 1 个错误；
  - 文件 `apps/system/src/a.tsx` 里写 `<p>中文</p>` → 1 个错误，消息含“i18n”；
  - 同样的 JSX 放在 `apps/system/test/a.test.tsx` → 0 个错误。
  - Expected：FAIL，因为包不存在。
- [ ] **Step 3:** 删除旧应用，建骨架和两个配置包，`pnpm install`。
- [ ] **Step 4:** Run `pnpm test && pnpm lint && pnpm typecheck`。Expected：边界用例全过，空工作区的 lint 和类型检查都通过。
- [ ] **Step 5:** 更新 CI 的步骤为：`pnpm install --frozen-lockfile` → `format:check` → `lint` → `typecheck` → `test` → `build:dev` → 密钥扫描。浏览器测试步骤在任务14 加回。`scripts/check-secrets.sh` 里的旧路径按新结构改。
- [ ] **Step 6:** 提交 `refactor!: 删除 Umi 旧应用，改为 pnpm workspace 单仓（Turborepo）`。台账记 `Ruling:`，内容为 TypeScript 留在 6.0 及其依据。

### Task 2: 主题包

**Files:**
- Create:
  - `packages/theme/{package.json,style-dictionary.config.mjs,src/index.ts}`
  - `packages/theme/tokens/{base,light,dark}.json`
  - `packages/theme-emerald/{package.json,style-dictionary.config.mjs}`
  - `packages/theme-emerald/tokens/{light,dark}.json`
- Test: `packages/theme/test/theme.test.ts`

**Interfaces:**
- Produces：
  - `@admin-web/theme` 导出：
    - `./theme.css`：`:root` 为浅色，`.dark` 为暗色；
    - `./tailwind.css`：Tailwind 4 的 `@theme inline` 映射；
    - `.`：`tokens`（`{ light: Record<CssVariable, string>; dark: Record<CssVariable, string> }`）和 `CSS_VARIABLES`（只读的变量名清单）。
  - `@admin-web/theme-emerald` 只导出 `./theme.css`，映射沿用 `@admin-web/theme/tailwind.css`。
- `CSS_VARIABLES` 即 shadcn/ui 的变量名：
  - 基础：`--background --foreground --card --card-foreground --popover --popover-foreground`
  - 主色等：`--primary --primary-foreground --secondary --secondary-foreground --muted --muted-foreground --accent --accent-foreground --destructive`
  - 边框：`--border --input --ring`
  - 图表：`--chart-1…--chart-5`
  - 侧边栏：`--sidebar --sidebar-foreground --sidebar-primary --sidebar-primary-foreground --sidebar-accent --sidebar-accent-foreground --sidebar-border --sidebar-ring`
  - 圆角：`--radius`
- 取值：基础色用 shadcn/ui 的 neutral，颜色一律写 OKLCH。默认主题的 `--primary` 用 1.2.4 的品牌蓝 `#1677ff`，用 colorjs.io 转成 OKLCH。emerald 主题只覆盖主色、`--ring`、`--sidebar-primary` 和图表色。

- [ ] **Step 1:** 写失败测试：
  - `theme.css declares every CSS_VARIABLES entry in :root and .dark`（两个主题包都测）；
  - `tokens export matches theme.css values`；
  - `every token has $value and $type per DTCG 2025.10`（`$type` 可以从分组继承）；
  - `text pairs meet WCAG AA 4.5:1`：两种模式、两个主题下，(foreground, background)、(primary-foreground, primary)、(muted-foreground, background)、(card-foreground, card)、(sidebar-foreground, sidebar) 用 colorjs.io 算对比度，都要 ≥ 4.5。
  - Run `pnpm --filter @admin-web/theme test`。Expected：FAIL。
- [ ] **Step 2:** 写令牌文件和 Style Dictionary 配置，构建脚本为 `build`，输出到 `dist/`。
- [ ] **Step 3:** Run 同上。Expected：PASS。如果对比度不够，调令牌值，不改阈值。
- [ ] **Step 4:** 提交 `feat(theme): 设计令牌与默认、emerald 两个主题包`。

### Task 3: 物料包基础：shadcn/ui 组件与 Storybook

**Files:**
- Create:
  - `packages/ui/{package.json,components.json,vitest.config.ts}`
  - `packages/ui/src/styles/globals.css`：依次引入 `tailwindcss`、`@admin-web/theme/tailwind.css`、`@admin-web/theme/theme.css`
  - `packages/ui/src/lib/utils.ts`（`cn`）
  - `packages/ui/src/components/*.tsx`：用 shadcn CLI 按官方单仓做法加入
  - `packages/ui/.storybook/{main.ts,preview.tsx}`
  - `packages/ui/src/components/{button,dialog,sheet,table}.stories.tsx`
- Test: `packages/ui/test/utils.test.ts`，以及 Storybook 交互测试（Vitest 插件）

**Interfaces:**
- Produces：`@admin-web/ui` 导出 `cn` 和下列 shadcn/ui 组件。
  - 表单：button、input、label、textarea、select、checkbox、switch、radio-group
  - 弹层：dialog、alert-dialog、sheet、dropdown-menu、popover、command、tooltip
  - 数据展示：table、badge、card、tabs、separator、skeleton、avatar、alert、progress
  - 导航：pagination、breadcrumb、sidebar
  - 其它：sonner、scroll-area、collapsible、accordion、chart
  - 后续任务确实需要的，可以再加。
  - 另有样式入口 `@admin-web/ui/globals.css`。
- Storybook：`@storybook/react-vite`，加 docs、a11y、vitest 三个插件。a11y 设为 `parameters.a11y.test = 'error'`。用装饰器切换浅色和暗色（在 html 上加 `.dark`）。

- [ ] **Step 1:** 写失败测试 `cn merges conflicting tailwind classes`：`cn('px-2', 'px-4') === 'px-4'`。写四个组件的 story，各带一个 play 交互，例如 dialog 打开后焦点落在对话框内、按 Esc 关闭。Run `pnpm --filter @admin-web/ui test`。Expected：FAIL。
- [ ] **Step 2:** 初始化包，用 `pnpm dlx shadcn@4.23.0 add …` 加入组件，配好 Storybook。
- [ ] **Step 3:** Run 同上。Expected：PASS，a11y 零违规。Storybook 的浏览器测试用已装的 Playwright Chromium。CI 在 `test` 之前加一步 `pnpm exec playwright install --with-deps chromium`。
- [ ] **Step 4:** 提交 `feat(ui): shadcn/ui 组件、样式入口与 Storybook`。

### Task 4: 业务组件

**Files:**
- Create: `packages/ui/src/business/` 下的 `PageContainer`、`DataTable`、`ItemList`、`FormDialog`、`ConfirmAction`、`GeneratedCodeInput`、`TreeView`、`TreeSelect`、`UploadDropzone`、`StatusBadge`、`EmptyState`、`ErrorState`、`DescriptionList`（都是 `.tsx`），每个配一个 `.stories.tsx`。
- Test: `packages/ui/test/business/*.test.tsx`
- 行为来源：1.2.4 的 `src/components/{AppPageContainer,AppList,GeneratedCodeInput,TenantSelect}`，以及各页面里 ProTable、ModalForm、Tree、Upload 的用法。

**Interfaces:**
- Produces（从 `@admin-web/ui` 导出）：
  - `PageContainer({ title: string; extra?: ReactNode; children: ReactNode })`
  - `DataTable<T>({ columns: ColumnDef<T, unknown>[]; data: T[]; rowKey: (row: T) => string; pagination?: { page: number; size: number; total: number; onChange: (page: number, size: number) => void }; loading?: boolean; toolbar?: ReactNode; emptyText?: string })`
  - `ItemList<T>({ items: T[]; rowKey: (item: T) => string; renderItem: (item: T) => ReactNode; header?: ReactNode; emptyText?: string; size?: 'default' | 'small' })`
  - `FormDialog<S extends z.ZodType>({ open: boolean; onOpenChange: (open: boolean) => void; title: string; schema: S; defaultValues: z.input<S>; onSubmit: (values: z.output<S>) => Promise<void>; children: (form: UseFormReturn<z.input<S>, unknown, z.output<S>>) => ReactNode; submitText?: string; variant?: 'dialog' | 'sheet' })`
  - `ConfirmAction({ title: string; description?: string; confirmText: string; onConfirm: () => Promise<void>; children: ReactElement })`
  - `GeneratedCodeInput({ value?: string; onChange: (v: string) => void; generate?: () => Promise<string>; disabled?: boolean })`
  - `type TreeNode = { id: string; label: string; children?: TreeNode[]; disabled?: boolean }`
  - `TreeView({ nodes: TreeNode[]; checkable?: boolean; checked?: string[]; onCheckedChange?: (ids: string[]) => void; selected?: string; onSelect?: (id: string) => void; defaultExpandAll?: boolean })`
  - `TreeSelect({ nodes: TreeNode[]; value?: string | string[]; multiple?: boolean; onChange: (v: string | string[] | undefined) => void; placeholder?: string })`
  - `UploadDropzone({ accept: string; maxFiles?: number; validate?: (f: File) => string | undefined; onFiles: (files: File[]) => void; hint?: ReactNode })`
  - `StatusBadge({ value: string; options: Record<string, { label: string; tone: 'default' | 'success' | 'warning' | 'destructive' }> })`
  - `EmptyState({ title: string; description?: string })`
  - `ErrorState({ title: string; onRetry?: () => void })`
  - `DescriptionList({ items: { label: string; value: ReactNode }[] })`
- 界面文字一律由调用方传入，组件里不写死中文；默认文案用 `common` 命名空间的 i18n 键。

- [ ] **Step 1:** 写失败测试：
  - `DataTable renders rows by rowKey`；`calls pagination.onChange(2, size) on next page`；`shows emptyText`；`shows skeleton while loading`
  - `FormDialog blocks submit and shows zod error`；`disables submit while onSubmit is pending (no double submit)`；`closes on success`；`stays open and shows the error when onSubmit rejects`
  - `TreeView moves focus with arrow keys per WAI-ARIA tree pattern`；`checking a parent checks all descendants`；`partial selection sets aria-checked="mixed"`
  - `TreeSelect returns ids in multiple mode`
  - `GeneratedCodeInput fills the value from generate() and disables while generating`
  - `UploadDropzone rejects a file failing validate() and shows its message`
  - `ConfirmAction calls onConfirm only after confirming`
  - `ItemList`：照搬 1.2.3 `app-list.test.tsx` 的四条用例
  - Expected：FAIL。
- [ ] **Step 2:** 实现组件和 story。树形控件按 WAI-ARIA APG 的 Tree View 模式实现（`role="tree"`、`treeitem`、方向键、`aria-expanded`、`aria-checked`）。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/ui test`。Expected：PASS，story 的 a11y 零违规。
- [ ] **Step 4:** 提交 `feat(ui): 业务组件（数据表、表单弹窗、树、上传等）`。

### Task 5: 请求与会话包

**Files:**
- Create:
  - `packages/api/src/{client,envelope,errors,traffic,sessionHeaders,query,table,index}.ts`
  - `packages/auth/src/{types,bootstrap,access,tenant,login,endpoints,index}.ts`
  - `packages/auth/src/{SessionProvider,RequireAccess}.tsx`
  - `packages/test-utils/src/{msw,render,index}.ts`
- Test: `packages/api/test/*.test.ts`、`packages/auth/test/*.test.ts(x)`
- 行为来源：1.2.4 的下列文件：
  - `src/utils/{requestAuth,traffic,table,sessionBootstrap,sessionLogin,auth}.ts`
  - `src/app.ts` 里的 `request` 配置
  - `src/access.ts`
  - `src/services/auth/{index.ts,types.d.ts}` 里的会话接口与类型

**Interfaces:**
- Produces，`@admin-web/api`：
  - `type TrafficEnv = { releaseVersion?: string; lane?: string; canaryTag?: string; canaryWeight?: string }`；`getTrafficHeaders(env: TrafficEnv): Record<string, string>`
  - `applySessionHeaders(config: InternalAxiosRequestConfig, cookies: string, tenantId: string | null): InternalAxiosRequestConfig`
  - `type HttpClientOptions = { traffic: TrafficEnv; getTenantId: () => string | null; onUnauthorized: () => void; notify: (message: string) => void }`；`createHttpClient(options: HttpClientOptions): AxiosInstance`
  - `type ApiResponse<T> = { success: boolean; data: T; code?: string; errorMessage?: string; msg?: string }`；`class ApiError extends Error { code?: string; status?: number }`；`unwrap<T>(r: ApiResponse<T>): T`
  - `type PageResult<T>`：字段照 1.2.4 的分页类型
  - `toPageQuery`、`cleanPayload`：照搬旧实现
  - `createQueryClient(): QueryClient`：`retry: false`，`refetchOnWindowFocus: false`，与旧应用不重试一致
- Produces，`@admin-web/auth`：
  - 类型：`CurrentUser`、`AuthTenantVO`、`AuthCurrentResourceVO`，照 1.2.4；`type SessionState = { currentUser?: CurrentUser; currentTenantId?: string; availableTenants?: AuthTenantVO[]; name?: string; sessionRecovery?: { reason: 'resources-unavailable' } }`
  - 会话：`bootstrapSession(client: AxiosInstance): Promise<SessionState>`
  - 权限：`type Access`，标志位同 1.2.4；`computeAccess(user?: CurrentUser): Access`
  - React：`SessionProvider`、`useSession(): SessionState & { refresh(): Promise<void> }`、`useAccess(): Access`、`RequireAccess({ flag: keyof Access; children })`（没有权限时显示 403 页）
  - 菜单契约：`type MenuEntry = { path: string; labelKey: string; access?: keyof Access; group?: { key: string; labelKey: string } }`；`type RemoteRoutes = { routes: RouteObject[]; menu: MenuEntry[] }`
  - 租户：`getSelectedTenantId`、`setSelectedTenantId`、`clearSelectedTenantId`、`clearLegacyTokens`
  - 登录：`LOGIN_ENTRY`、`LOGIN_PAGE = '/login'`、`isLoginPage(pathname)`、`redirectToLogin()`（同一页面只跳一次）、`logoutAndLeave(logout, nav?)`
  - 接口：`fetchCurrentResources(client)`、`fetchCurrentTenants(client)`、`logout(client)`
- Produces，`@admin-web/test-utils`：`setupMsw(handlers)`、`renderWithProviders(ui, { session?, locale? })`、`loadFixture(name)`（读 `e2e/fixtures`）

- [ ] **Step 1:** 写失败测试。
  - **照搬 1.2.3 对应单元测试的断言**：
    - 只有写请求带 `X-XSRF-TOKEN`；
    - 从不带 `Authorization`；
    - 已有 `X-Tenant-Id` 就不覆盖；
    - 灰度头的默认值与非法值处理；
    - 会话启动遇 `A0220`、`A0230`、401 跳登录，资源无效进恢复页；
    - 退出时 202 加 `Location` 导航过去，失败导航到 `/`，并且先清租户。
  - **错误处理表驱动**：取消不提示；403；图谱提交的合法键静默、非法键提示；其余提示的取值顺序。
  - **`401 redirects once across concurrent requests`**：3 个并发请求都回 401，`onUnauthorized` 只调 1 次。
  - **`computeAccess` 表驱动**：每个标志位都有 true 和 false 两个用例，规则逐条对照 1.2.4 的 `access.ts`。
  - **MSW 接口测试**：`/auth/current/resources`、`/auth/current/tenants`、`POST /logout` 的路径、方法和请求头。
  - Expected：FAIL。
- [ ] **Step 2:** 实现三个包。
- [ ] **Step 3:** Run `pnpm --filter "@admin-web/{api,auth,test-utils}" test`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(api,auth): 请求客户端、会话与权限（契约照 1.2.4）`。

### Task 6: 多语言包

**Files:**
- Create: `packages/i18n/src/{index,resources}.ts`、`packages/i18n/src/i18next.d.ts`、`packages/i18n/locales/{zh-CN,en-US}/{common,shell,system,message,ai,rag}.json`
- Test: `packages/i18n/test/locales.test.ts`

**Interfaces:**
- Produces：
  - `type Locale = 'zh-CN' | 'en-US'`
  - `NAMESPACES`
  - `LOCALE_STORAGE_KEY = 'admin_web_locale'`
  - `initI18n(options?: { lng?: Locale }): Promise<i18n>`：默认 zh-CN，存的值不认识时回落 zh-CN
  - `setLocale(lng: Locale): Promise<void>`：持久化，并同步 `document.documentElement.lang`
  - `i18next.d.ts` 声明 `CustomTypeOptions`，让文案键有类型检查
- 各命名空间的文案由任务8–12 逐个补齐，本任务只放 `common`（确定、取消、保存、删除、重试、空状态、加载失败等）。

- [ ] **Step 1:** 写失败测试：
  - `zh-CN and en-US have identical keys in every namespace`
  - `no empty strings`
  - `setLocale persists and updates html lang`
  - `unknown stored locale falls back to zh-CN`
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/i18n test`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(i18n): i18next 底座与通用文案`。

### Task 7: Vite 预设与 Module Federation 构建配置

**Files:**
- Create: `packages/config-vite/src/{index,proxy,shared,env}.ts`
- Test: `packages/config-vite/test/config.test.ts`
- 行为来源：1.2.4 `.umirc.ts` 的 `businessApi`、`gatewayOwned` 代理选项，以及 `config/apiPrefixes.ts`

**Interfaces:**
- Produces：
  - `defineAppConfig(options: { name: string; role: 'host' | 'remote'; devPort: number; previewPort: number; exposes?: Record<string, string> }): UserConfigFnObject`。组合的内容有：
    - 插件：@vitejs/plugin-react、@tailwindcss/vite、@module-federation/vite（`filename: 'remoteEntry.js'`、`manifest: true`、`shared: sharedConfig()`；`exposes` 只给 remote）；
    - 环境变量：`envPrefix: 'VITE_'`，`ADMIN_WEB_GATEWAY_URL` 用 `loadEnv` 读，只用于代理；
    - 代理：`server.proxy` 与 `preview.proxy` 都用 `createProxy`；
    - 端口：`server.port`、`preview.port`，`strictPort: true`；
    - 跨源：remote 允许宿主跨源取资源，`server.cors` 与 `preview.cors` 都要配。
  - `API_PROXY_PREFIXES = ['/auth', '/user', '/message', '/report', '/api']`、`GATEWAY_PREFIXES = ['/oauth2', '/login/oauth2', '/logout']`
  - `createProxy(gatewayUrl: string): Record<string, ProxyOptions>`：选项照搬 1.2.4，Host 不改写，网关要按它生成 Keycloak 回调地址
  - `SHARED_SINGLETONS`；`sharedConfig(): Record<string, { singleton: true; requiredVersion: string }>`：版本取 catalog

- [ ] **Step 1:** 写失败测试：
  - `proxy covers every API and gateway prefix and keeps the Host header`
  - `remote config exposes the given modules and emits a manifest`
  - `host config has no exposes`
  - `shared singletons include react, react-router and @admin-web/auth`
  - `ports are strict and envPrefix is VITE_`
  - `gateway url never reaches define/env exposed to the browser`
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/config-vite test`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(config-vite): Vite 预设（Module Federation、代理、环境变量）`。

### Task 8: 主应用 shell

**Files:**
- Create:
  - 配置：`apps/shell/{package.json,index.html,vite.config.ts,.env.dev,.env.test,.env.prod}`、`apps/shell/public/apps.json`
  - 入口：`apps/shell/src/{main.tsx,App.tsx,router.tsx,styles.css}`
  - 远程模块：`apps/shell/src/remotes/{registry,loadRemotes}.ts`、`apps/shell/src/remotes/RemoteBoundary.tsx`
  - 布局：`apps/shell/src/layout/` 下的 `AppLayout`、`AppSidebar`、`AppHeader`、`TenantSwitcher`、`AccountMenu`、`LanguageSwitcher`、`ThemeToggle`、`BellSlot`（`.tsx`）
  - 页面：`apps/shell/src/pages/` 下的 `Dashboard`、`LoginError`、`Forbidden`、`NotFound`、`SessionRecovery`（`.tsx`）
  - 文案：在 `shell` 命名空间里补两种语言
- Test: `apps/shell/test/*.test.tsx`
- 行为来源：1.2.4 的下列文件：
  - `src/app.ts`（布局、请求配置、会话启动）
  - `src/pages/{Dashboard,Login}`
  - `src/components/{AvatarDropdown,TenantSwitcher,SessionRecovery,MessageCenter}`

**Interfaces:**
- Consumes：Task 4、5、6、7 的导出。
- Produces：
  - `type RemoteApp = { name: string; entry: string; routePrefixes: string[] }`；`parseRegistry(json: unknown): RemoteApp[]`：拒收不合法的名称、地址，以及重复的前缀；`loadRegistry(url = '/apps.json'): Promise<RemoteApp[]>`
  - `type LoadedRemote = { app: RemoteApp; status: 'ready' | 'failed'; module?: RemoteRoutes }`；`loadRemotes(apps: RemoteApp[]): Promise<LoadedRemote[]>`：用 `@module-federation/runtime` 的 `registerRemotes` 和 `loadRemote('<name>/routes')`，并行加载，配 retry-plugin
  - `buildMenu(remotes: LoadedRemote[], access: Access): SidebarGroup[]`：加载失败的子应用，其菜单项标为不可用
  - 子应用接入契约（任务9–12 遵守）：暴露 `./routes`，默认导出 `RemoteRoutes`；message 另外暴露 `./Bell`，导出 `Bell({ userId: string; tenantId?: string })`
  - 在 `@admin-web/auth` 加 `StandaloneShell({ remote: RemoteRoutes })`：子应用单独运行时的最小外壳，含会话启动、路由、Toaster，不带侧边栏。各子应用的 `src/dev/main.tsx` 用它挂载。遇到 401 时不跳登录，提示“先在主应用登录”，并链接到 `http://localhost:8000/`
- `apps.json` 按 mode 生成：dev 指向 8101–8104。浏览器测试在测试里拦截它，改成指向预览端口。

- [ ] **Step 1:** 写失败测试：
  - `parseRegistry rejects duplicate prefixes and non-http entries`
  - `buildMenu hides entries the user cannot access`
  - `remote load failure only disables its own domain`：system 加载失败时，`/system/*` 显示 ErrorState 和“重试”，其它域的菜单与路由照常
  - `switching tenant clears the shared query cache`：切换后 `queryClient.getQueryCache().getAll()` 为空，并写入新的租户
  - `login error page offers only 重新登录 and it navigates to LOGIN_ENTRY`
  - `account menu logout clears the tenant before calling logout`
  - `theme toggle sets the dark class and persists`
  - `session recovery offers retry`：行为照 1.2.4 的 SessionRecovery
  - `bell slot renders nothing when message/Bell fails to load`
  - `standalone shell shows the sign-in-at-shell notice on 401 instead of redirecting`
  - Expected：FAIL。
- [ ] **Step 2:** 实现主应用。工作台照 1.2.4 的两个列表改用 `ItemList`。账户菜单的设置对话框字段照 1.2.4：用户名、昵称、管理员分类、当前租户、可用租户数。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/shell test && pnpm --filter @admin-web/shell build`。Expected：PASS，构建产出主应用。
- [ ] **Step 4:** 提交 `feat(shell): 主应用（布局、远程注册、会话、租户、账户、主题、语言）`。

### Task 9: system 子应用

**Files:**
- Create:
  - 配置：`apps/system/{package.json,index.html,vite.config.ts,.env.*}`
  - 入口：`apps/system/src/routes.tsx`（暴露为 `./routes`）、`apps/system/src/dev/main.tsx`（用 `StandaloneShell`）
  - 接口：`apps/system/src/services/{tenant,dept,user,role,resource,code}.ts`
  - 工具：`apps/system/src/lib/{deptTree,resourceTree}.ts`
  - 页面：`apps/system/src/pages/{Tenant,Dept,User,Role,Resource}/*.tsx`
  - 文案：在 `system` 命名空间里补两种语言
- Test: `apps/system/test/**`
- 行为来源：1.2.4 的下列文件：
  - `src/pages/System/*`
  - `src/services/auth/index.ts` 的管理接口与类型
  - `src/utils/{deptTree,resourceTree}.ts`
  - `src/hooks/useTenantOptions.ts`
  - `src/components/{GeneratedCodeInput,TenantSelect}`

**Interfaces:**
- Consumes：Task 4、5、6、7、8 的导出。
- Produces：`./routes`，菜单分组 `system:menu.group`（系统管理），五项依次为：

| 路径 | 文案键 | 权限 |
|---|---|---|
| `/system/tenant` | `system:menu.tenant` | `canSeeTenant` |
| `/system/dept` | `system:menu.dept` | `canSeeDept` |
| `/system/user` | `system:menu.user` | `canSeeUser` |
| `/system/role` | `system:menu.role` | `canSeeRole` |
| `/system/resource` | `system:menu.resource` | `canSeeResource` |

- [ ] **Step 1:** 写失败测试：
  - **MSW 接口测试**：每个管理接口的路径、方法和请求体都与 AGENTS.md 接口契约一致；更新请求带 `version`；分页查询映射与 `toPageQuery` 一致。
  - **树工具**：`buildDeptTree`、`buildResourceTree` 按 `sort` 升序排，缺省为 0（照第三部分任务18 的用例）。
  - **页面**：租户列表显示，新建表单校验与提交；删除要先确认；角色授权提交勾选的资源编号；用户绑定角色；数据范围选部门；业务编码由 `POST /auth/manage/codes` 生成。
  - **单独运行**：`src/dev/main.tsx` 用 `StandaloneShell` 挂上本应用的路由，冒烟用例打开 `/system/tenant` 能渲染。401 的提示在任务8 测过。
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/system test && pnpm --filter @admin-web/system build`。Expected：PASS，产出 `mf-manifest.json`。
- [ ] **Step 4:** 提交 `feat(system): 系统管理子应用`。

### Task 10: message 子应用

**Files:**
- Create:
  - 配置：`apps/message/{package.json,index.html,vite.config.ts,.env.*}`
  - 入口：`apps/message/src/{routes.tsx,Bell.tsx}`、`apps/message/src/dev/main.tsx`（用 `StandaloneShell`）
  - 接口：`apps/message/src/services/message.ts`
  - 页面：`apps/message/src/pages/Send/*.tsx`
  - 文案：在 `message` 命名空间里补两种语言
- Test: `apps/message/test/**`
- 行为来源：1.2.4 的 `src/pages/Message/Send`、`src/components/MessageCenter`、`src/services/message/index.ts`

**Interfaces:**
- Produces：
  - `./routes`：`/message/send`，文案键 `message:menu.send`，权限 `canSeeMessageSend`
  - `./Bell`：`Bell({ userId, tenantId })`。用户或租户变化时重新挂载，旧应用是用 key 实现的

- [ ] **Step 1:** 写失败测试：
  - 发送表单的校验和请求体；
  - 铃铛显示未读数；
  - 打开后列出消息；
  - 标为已读后重新取未读数；
  - 租户变化时重新请求；
  - MSW 测试覆盖五个消息接口的路径。
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/message test && pnpm --filter @admin-web/message build`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(message): 消息子应用与铃铛组件`。

### Task 11: ai 子应用

**Files:**
- Create:
  - 配置：`apps/ai/{package.json,index.html,vite.config.ts,.env.*}`
  - 入口：`apps/ai/src/routes.tsx`、`apps/ai/src/dev/main.tsx`（用 `StandaloneShell`）
  - 接口：`apps/ai/src/services/{model,controlPlane,storage}.ts`
  - 页面：`apps/ai/src/pages/{Model,ConfigurationModels,ConfigurationStorage}/*.tsx`
  - 模型工作台：`apps/ai/src/model/{ModelRouteWorkbench.tsx,modelSelectionModel.ts}`
  - 文案：在 `ai` 命名空间里补两种语言
- Test: `apps/ai/test/**`
- 行为来源：1.2.4 的 `src/pages/{Ai,Configuration}/*`、`src/services/ai/*`

**Interfaces:**
- Produces：`./routes`，菜单分组为“AI管理”和“统一配置”。

| 路径 | 文案键 | 权限 |
|---|---|---|
| `/ai/model` | `ai:menu.model` | `canSeeAiModel` |
| `/configuration/models` | `ai:menu.configModels` | `canManageConfiguration` |
| `/configuration/storage` | `ai:menu.configStorage` | `canManageConfiguration` |

  另加 `/configuration` 重定向到 `/configuration/models`。

- [ ] **Step 1:** 写失败测试：
  - 照搬 1.2.3 里模型选择与存储相关单元测试的断言，包括 `canSelectProvider` 的各种情况；
  - 切换模型需要 `canChangeAiModel`；
  - 存储探测抽屉列出实际探测步骤；
  - MSW 测试覆盖全部 AI 与存储接口。
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/ai test && pnpm --filter @admin-web/ai build`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(ai): AI 与统一配置子应用`。

### Task 12: rag 子应用

**Files:**
- Create:
  - 配置：`apps/rag/{package.json,index.html,vite.config.ts,.env.*}`
  - 入口：`apps/rag/src/routes.tsx`、`apps/rag/src/dev/main.tsx`（用 `StandaloneShell`）
  - 接口：`apps/rag/src/services/rag.ts`
  - 文档页：`apps/rag/src/pages/Document/`，含上传抽屉、切片抽屉、检索测试抽屉
  - 图谱页：`apps/rag/src/pages/Graph/`，含构建状态、来源、详情、全屏
  - 工具：`apps/rag/src/lib/{documentModel,uploadValidation,graphModel,constants}.ts`
  - 文案：在 `rag` 命名空间里补两种语言
- Test: `apps/rag/test/**`
- 行为来源：1.2.4 的 `src/pages/Rag/*`、`src/services/rag/index.ts`、`src/constants/rag.ts`

**Interfaces:**
- Produces：`./routes`。

| 路径 | 文案键 | 权限 |
|---|---|---|
| `/rag-knowledge/documents` | `rag:menu.documents` | `canSeeRagDocument` |
| `/rag-knowledge/graph` | `rag:menu.graph` | `canSeeRagGraph` |

  检索按 `canUseRagRetrieval`、图谱管理按 `canManageRagGraph` 控制按钮。
- 知识图谱用 Cytoscape.js 渲染，替换 @antv/g6；布局由实施时定（见设计“留给实施”）。

- [ ] **Step 1:** 写失败测试：
  - 照搬 1.2.3 rag 相关单元测试的断言：上传校验、按身份范围作废过期请求、图谱提示文案模型；
  - 同步请求带合法的 `X-Graph-Submission-Key`，网络失败时页面自己查回提交状态；
  - 图谱渲染的节点数等于固定数据的节点数；
  - 全屏时画布填满面板；
  - MSW 测试覆盖十个 rag 接口。
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run `pnpm --filter @admin-web/rag test && pnpm --filter @admin-web/rag build`。Expected：PASS。
- [ ] **Step 4:** 提交 `feat(rag): 知识库子应用（文档、检索、Cytoscape 图谱）`。

### Task 13: 物料注册表与“新建子应用”生成器

**Files:**
- Create:
  - `packages/ui/registry.json`：条目包括业务组件、`crud-page` 页面模板、主题 `theme-default` 与 `theme-emerald`
  - `turbo/generators/config.ts`
  - `turbo/generators/templates/remote/**`：package.json、vite.config.ts、src/routes.tsx、src/pages/Example.tsx、文案、测试
- Test: `packages/ui/test/registry.test.ts`、`scripts/test-generator.mjs`

**Interfaces:**
- Produces：
  - `pnpm --filter @admin-web/ui registry:build`：执行 `shadcn build`，输出到 `packages/ui/public/r/*.json`；
  - `pnpm turbo gen remote --args <name> <devPort> <previewPort>`：生成一个可以直接构建的子应用。

- [ ] **Step 1:** 写失败测试：
  - `registry build emits one json per item and each parses with the shadcn registry-item schema`：用 shadcn 包自带的 schema 校验；
  - `test-generator.mjs`：生成 `apps/gen-check`，对它跑 `typecheck`、`test`、`build`，最后删掉它，断言退出码为 0。
  - Expected：FAIL。
- [ ] **Step 2:** 实现。
- [ ] **Step 3:** Run 两个测试。Expected：PASS，工作区里不留 `apps/gen-check`。
- [ ] **Step 4:** 提交 `feat: 物料注册表与新建子应用生成器`。

### Task 14: 浏览器测试（固定数据一组）与 CI

**Files:**
- Create:
  - `playwright.config.ts`：单份配置，`ADMIN_WEB_E2E=live` 时切到真实网关一组；webServer 先 `pnpm build:dev`，再起五个预览服务
  - `e2e/support/{mockApi,console,pages,remotes}.ts`
  - `e2e/mock/` 下的 `pages`、`dialogs`、`account-menu`、`login-error`、`graph`、`i18n-theme`、`tenant-switch`、`access`、`deep-link`、`remote-failure`（都是 `.spec.ts`）
  - `e2e/fixtures/session-restricted.json`
- Modify: `.github/workflows/ci.yml`，在 `build:dev` 之后加 `pnpm test:e2e`，失败时上传 `e2e-out`。Chromium 在任务3 已经装上。
- 行为来源：1.2.3 的 `e2e/support/*` 和 `e2e/mock/*`，包括按查询参数匹配固定数据、漏拦截接口检测、控制台检查

**Interfaces:**
- Consumes：各应用的构建产物；`e2e/fixtures/*.json`。
- `remotes.ts` 在测试里拦截 `/apps.json`，改成指向预览端口；`remote-failure` 用例让 system 的清单回 503。

- [ ] **Step 1:** 写用例。全部用例都要求控制台无错误、无警告，且没有漏拦截的接口。
  - `pages`：12 个菜单页各自打开，等内容出现后不再显示加载中；
  - `dialogs`：每页的“新建、新增、上传、检索测试”按钮都可点，弹层能打开，按 Esc 能关；
  - `account-menu`：能打开，设置对话框字段齐全；
  - `login-error`：只给“重新登录”；
  - `graph`：Cytoscape 渲染出节点，全屏填满面板；
  - `i18n-theme`：切到英文后菜单是英文；切到暗色后 html 上有 `.dark`；
  - `tenant-switch`：切换后请求带新的 `X-Tenant-Id`，列表重新取数（对应审查重点 3）；
  - `access`：受限的固定数据下菜单看不到该页，直接输地址进入显示 403（对应审查重点 5）；
  - `deep-link`：12 个页面直接输地址进入都能渲染（对应审查重点 4）；
  - `remote-failure`：system 不可用时，只有 `/system/*` 报错并提供重试，其它域正常（对应审查重点 1）。
- [ ] **Step 2:** Run `pnpm test:e2e`。Expected：全过。如有不过，按缺陷修，不加允许清单，不加重试。
- [ ] **Step 3:** 推送分支，看 CI。Expected：分支上的 CI 全绿。
- [ ] **Step 4:** 提交 `test(e2e): 固定数据一组浏览器测试覆盖主应用与四个子应用`。

### Task 15: 真实网关一组、截图对比、文档、版本与合入

**Files:**
- Create: `e2e/live/session.spec.ts`，照搬 1.2.3 的五条用例：登录后每个菜单页无错误、警告和 5xx；部门新建后能删除；登录失败页；退出后会话失效。
- Create: `scripts/verify-independent-deploy.mjs`
- Modify：
  - 文档：`README.md`、`AGENTS.md`、`docs/ai-coding/*` 全部改写为新栈，删掉 Umi、antd、ProComponents 的写法约定，补上 shadcn/ui 与业务组件、i18n、子应用边界、主题令牌、Module Federation 接入契约；
  - 版本：根目录与五个应用的 `package.json`。

- [ ] **Step 1:** 写真实网关一组。主应用在 8000 端口以 `build:test` 加预览运行，子应用在 8201–8204，同样拦截 `/apps.json`。Run `python3 -I ~/.config/kellen-github/tools/run_chain_verify.py --admin-web-e2e`。Expected：五条全过。若首跑撞上 C0110，按已知后端缺陷记台账，不算前端回归。
- [ ] **Step 2:** 写 `verify-independent-deploy.mjs`：
  1. 全部构建，记下主应用 `dist` 的哈希；
  2. 只用 `VITE_RELEASE_VERSION=verify-<时间戳>` 重建 system；
  3. 断言主应用 `dist` 的哈希不变；
  4. 断言在预览服务里打开 `/system/tenant` 时，请求头 `X-Release-Version` 为新值。

  Run 它。Expected：通过。
- [ ] **Step 3:** 截图对比：
  1. 在临时工作树里检出合入前的 `origin/main`（1.2.4 旧应用），用同一份固定数据截 before；
  2. 在本分支截 after；
  3. 生成本地对比页，交用户过目。

  **这一步停下等用户确认。**
- [ ] **Step 4:** 改文档，定版本。版本按 Global Constraints：用户指定 2.0.0 就用 2.0.0，否则 1.2.5。
- [ ] **Step 5:** 收尾检查，依次满足：
  - `git grep -n -E "umi|@umijs|antd|@ant-design|@antv" -- ':!docs/**' ':!**/CHANGELOG*'` 无结果；
  - `pnpm why antd` 无结果；
  - `pnpm check` 全过；
  - 密钥扫描过；
  - 改动的文件里没有本机路径。
- [ ] **Step 6:** fetch；远端前进了就 rebase 并重跑 Step 5。快进合入 `main`，推送，读回精确提交的 CI，删掉短分支。提交信息为 `feat!: admin-web 按国际主流重写（<版本>）`。

### Task 16: 收尾

- [ ] **Step 1:** 第6阶段原计划的任务20 合并到这里做，第三部分的复查一并在这里做。台账记一条 `Ruling:`。
- [ ] **Step 2:** 整体复查：用 `review-package` 打包本分支的完整范围，交给全新上下文、最强模型的子代理复查。按 executing-plans 的规则重新评级；Critical 和 Important 一轮修完，每条修复都要先红后绿，最后全套测试通过。
- [ ] **Step 3:** 清理：临时工作树、截图目录、`apps/gen-check` 残留、第5阶段遗留的备份与旧令牌文件（按提醒清单）。
- [ ] **Step 4:** 归档 Wiki，用 llm-wiki-archive 技能，范围是第6阶段第一至第四部分。另外：更正第5阶段退出登录的结论；写入 C0110 的排查结论；记下 Node 26 V8 崩溃这一观察项；同步路线页，写明微前端已改为 Module Federation。以“已提交推送 <hash>”回执为准。
