# 第6阶段第四部分：admin-web 按国际主流重写设计

微前端、物料、主题、插件前期就拆；每一层按国际主流选型。

## 已确认目标

用户原话（2026-11-22，新加坡时间）：

- 上午：“另外现在前端有一个架构体系是，npm的选择主题和颜色、插件。之前使用过飞冰+这个主题物料的npm统一管理方式？”“还有微前端没拆吗？后期拆是不是不方便？”
- 11:45：“采用靠谱的物料和主题和插件的吧？要求是大量主流github项目以及高关注的方案，拆微前端也是。另外阿里支付宝的这套ui靠谱吗？不行也可以换了。这个项目是一个基础框架项目，后期如果那些公司需要就会从这里裂变出一套架构。所有要求前期就拆了”
- 11:58：“不要考虑国内，我希望要的是国际主流方案”
- 12:01：“重写整个其实也不影响太多，因为本事就是基础框架底座，没什么功能和业务的”
- 12:15：“可以整个替换删除，不需要逐步淘汰，因为这是基础框架。”
- 长期规则：“记住所有升级都要求找github主流方案和主流做法，使用文献和论文等。不要自己为了减少改动范围采用偷懒的。”

据此：admin-web 定位为可裂变的基础框架底座；前端整体重写，每一层取国际主流；微前端、物料、主题、插件这一轮就拆好；旧应用整个删除替换，不做新旧并存。

## 修订记录

- 2026-11-22 初稿。本设计取代 [第6阶段设计](2026-11-22-phase6-frontend-design.md) 的两处：一是“微前端本阶段不拆”（那条由我定、用户过目时可改）；二是新栈不再用 Umi Max、antd、ProComponents。路线页“拆微前端时用 Umi 自带的 qiankun”按用户 11:58 的要求改为 Module Federation。第一、二部分（1.2.2、1.2.3）已交付；第三部分（`sorting` → `sort`）照原计划先做。
- 2026-11-22 12:30 独立审阅提了 5 个问题，处理如下：
  - **菜单与权限**（项目已有答案）：照 1.2.4 现行做法。菜单由各子应用定义，即路由元数据：路径、文案键、所需权限；后端的资源编码与权限码只决定显示与否，规则照搬 `access.ts`。不改成按后端资源动态生成菜单树，那样要改后端数据，菜单文字也没法翻译。主应用启动时加载各子应用的 `./routes`（含菜单元数据，页面懒加载），不用导航时补路由。
  - **是否批准**：12:18 已批准。
  - **子应用单独运行时的登录**（我定）：一律到主应用的 8000 端口登录，不在 Keycloak 里加回调地址。localhost 的 Cookie 不分端口，登录后单独运行的子应用也能带上会话。单独运行时遇到 401，提示“先在主应用登录”，并给出链接。
  - **灰度头**（我定）：`X-Release-Version`、`X-Traffic-Lane`、`X-Canary-Tag`、`X-Canary-Weight` 由各应用取自己构建时的环境变量，因为各应用能单独上线。取值、校验规则和默认值（`1.0.0`、`stable`）照旧。所以 `@admin-web/api` 提供 `createHttpClient`，每个应用各建一个请求客户端。
  - **版本**（问用户）：`VERSIONING_SPEC.md` 规定破坏性改动也按逢 9 进位的顺序走，只有用户指定才例外。所以本部分合入时默认取下一个号（第三部分之后是 1.2.5），用户指定 2.0.0 就用 2.0.0。五个应用各有自己的版本号，合入时都从这个号起步，之后哪个应用改了就只升哪个。共享包在决定发布 npm 之前不单独定版本，标为 private、版本保持 0.0.0。Changesets 推迟到决定发布时再接，因为它按 SemVer 升版，会出 `x.y.10`，与本仓库的规则冲突。
- 2026-11-22 12:18 用户批准：“刚刚那个方案，采用。”（批准的是含整个替换的这一版；独立审阅者的问题按“留给实施”或“我定”处理，结果记在修订记录里）
- 2026-11-22 12:15 按用户要求改为整个替换：去掉旧应用挪到 `apps/legacy` 并存的做法，在一个短分支上一次替换完再合入。第三部分的任务18仍改旧应用的字段（改动小），保证本部分合入前 main 能正常用。

## 目标与反目标

### 目标

1. **拆成主应用加四个子应用**，用 Module Federation 2.0。每个子应用都能单独开发、单独构建、单独上线，开发时也能单独跑起来。
2. **物料、主题、插件都做成共享包**，在仓库内直接引用。是否发布到 npm、发布时怎么定版本，另行确认（见修订记录 12:30）。
3. **功能与 admin-web 1.2.4 对齐**：
   - 全部菜单页，以及各页的弹窗和抽屉；
   - 经网关的会话登录、`/login?error`、退出；
   - 租户切换、消息中心；
   - 按后端资源生成菜单和权限。
4. **每一层都按国际主流选型**，依据和来源列在下文。
5. **门禁不降**：格式、lint、类型、单元与组件测试、接口测试、浏览器两组（固定数据、真实网关）、密钥扫描，以及推送后精确提交的 CI。
6. **多语言底座**（我定）：用 i18next，简体中文为默认，再完整做一份英文，证明机制可用。

### 不做

- 不改后端接口和网关。第三部分先完成，新前端直接用改名后的契约。
- 不做生产托管与发布流水线，只产出构建产物，并能在本机预览。共享包发布到 npm 要用维护者账号，另行确认。
- 不做视觉改版和新功能。除了换成 shadcn/ui 的默认样式加我们的主题，不加新交互。
- 不做服务端渲染：后台是放在 BFF 后面的单页应用。
- 后端是否也按国际主流复核（Nacos、Dubbo、Seata 等），如有需要另议。
- 不含移动端。

### 反目标（做了也算失败）

- 替换后，仓库里还留着 Umi、antd、ProComponents 或 @antv 的代码和依赖。
- 子应用直接 import 别的子应用的源码。子应用之间只能经 Module Federation 暴露的入口或共享包往来，用 lint 规则拦住。
- 子应用绕开主应用的源，直连后端。所有接口必须与主应用同源，保留会话 Cookie 和 `X-XSRF-TOKEN`。
- 靠降级、跳过测试或加允许清单来掩盖警告。
- 在浏览器里生成业务编码。仍调后端的编码接口。

## 判定口径

- **只看国际主流**（用户 11:58）。依据是 GitHub 星数、npmjs 全球周下载、State of React 2025 调查和官方文档，不用国内镜像的下载数据。
- **指标不一致时写明取哪个、为什么**。以多个主流项目的多数做法为准，不以单个项目为准。
- **版本**取实施时的最新稳定版。如果被主流依赖的兼容范围卡住，就按兼容范围取，并写明原因。
- 下文数据均为 2026-11-22 查询。“国际主流项目样本”指 10 个仓库：refine、react-admin、shadcn-admin、MUI、shadcn/ui、Mantine、Chakra UI、Next.js、TanStack Router、Storybook。

## 主流依据

### UI 组件库

| 库 | GitHub 星 | npm 周下载 | 说明 |
|---|---|---|---|
| shadcn/ui | 128,708 | 命令行 1924 万；底层 Radix 对话框 1.02 亿，Tailwind CSS 1.91 亿 | 组件源码进自己的仓库；MIT |
| MUI | 99,381 | 1495 万 | State of React 2025 中使用率第一；高级数据表格（MUI X Pro/Premium）收费 |
| antd | 100,333 | 556 万 | 生态与路线以国内为主 |
| Chakra UI | 40,747 | 235 万 | |
| Mantine | 31,965 | 383 万 | |
| HeroUI | 31,278 | 110 万 | Apache-2.0 |
| React Aria（Adobe） | 16,058 | 992 万（react-aria-components） | 无样式的底层组件 |

**结论：选 shadcn/ui**（我定，用户过目时可以改成 MUI）。理由：
- 它是关注度最高的组件库（星数第一）。State of React 2025 的结果是 MUI 使用率仍居第一，shadcn/ui 增长很快；RedMonk 2025 年也专门分析过它“复制进仓库”的模式。
- 组件源码就在仓库里，裂变出去的公司可以随意改。
- 主题就是一组 CSS 变量，换品牌只要换变量。
- 官方支持单仓多包（模板就是 Turborepo 加 `packages/ui`），也支持物料注册表，正好承担“物料用 npm 统一管理”这件事。

**备选 MUI**：组件最全、使用率最高。但高级表格功能收费，裂变出去的公司得各自买授权，外观也偏 Material 风格。

### 构建、路由与数据

| 项 | 选择 | 依据 |
|---|---|---|
| 构建 | Vite 8 | 84,019 星，周下载 2.89 亿，同类第一 |
| 路由 | React Router（实施时的最新稳定版，现为 8.x） | 56,650 星，8026 万；TanStack Router 为 15,341 星、2898 万 |
| 服务端状态 | TanStack Query | 50,607 星，9200 万；State of React 2025 的报道称它“保持第一” |
| 表格 | TanStack Table | 28,600 星，3864 万；shadcn/ui 官方数据表就是这么写的 |
| 表单与校验 | react-hook-form + zod | 44,926 星、6811 万；44,378 星、4.74 亿 |
| 请求 | axios | 1.50 亿（ky 为 1076 万） |
| 图表 | Recharts | 27,727 星，7126 万；shadcn/ui 的图表组件基于它 |
| 知识图谱 | Cytoscape.js | 11,300 星，2927 万。Sigma.js 为 12,246 星、73 万；xyflow 有 39,109 星，但面向流程编辑器。现用的 @antv/g6 为 35.8 万 |
| 多语言 | i18next + react-i18next | 8,666 星；3706 万、2765 万 |
| 接口模拟 | MSW | 3601 万（已在用） |
| 图标、提示 | lucide-react、sonner | shadcn/ui 默认搭配 |

不另引入全局状态库：服务端数据交给 TanStack Query，会话放在 React context 里。确实需要时用 Zustand。

### 微前端

| 方案 | 依据 |
|---|---|
| Module Federation 2.0 | 运行时 @module-federation/runtime 周下载 1466 万，enhanced 435 万；webpack（66,018 星）首创，Rspack 内置；Vite 插件 @module-federation/vite 1.24.1，周下载 196 万，支持 Vite 5–8 |
| single-spa | 13,883 星，60.7 万 |
| qiankun | 16,709 星，7.6 万（按 11:58 的要求不考虑） |

**结论：Module Federation 2.0 加 Vite 插件**。

**接入方式（我定）**：子应用暴露自己的路由表，由主应用的一个路由器统一挂载。这是 Module Federation 最常见的模块级用法。另有一种 Bridge 方式（每个子应用自建 React 根节点和路由），留作以后挂其它技术栈应用的扩展点。原因：Bridge 的 @module-federation/bridge-react 2.9.3 只支持 react-router ^6 或 ^7，周下载 18.3 万，而且我们的子应用技术栈一致，用不上它的隔离。

### 单仓、版本与物料工具

| 项 | 选择 | 依据 |
|---|---|---|
| 包管理与工作区 | pnpm workspace，用 catalog 统一版本 | 37,150 星；国际样本 10 个里 6 个用 pnpm workspace，另有 3 个用 yarn |
| 任务编排 | Turborepo | 31,386 星，4817 万；Nx 为 29,485 星、1562 万 |
| 版本与发布 | Changesets（推迟到决定发布 npm 时再接） | 12,589 星，942 万；样本中 4 个用 Changesets，3 个用 Lerna。它按 SemVer 升版，与本仓库逢 9 进位的规则冲突 |
| 物料文档 | Storybook 10 | 91,675 星，3877 万 |
| 物料分发 | shadcn/ui 注册表 | 官方文档，含示例与 MCP |
| 设计令牌 | W3C DTCG 2025.10 格式 + Style Dictionary 5 | DTCG 2025-10-28 发布首个稳定版（格式与解析两个模块）；Style Dictionary 4,889 星、319 万，v4 起原生支持该格式 |
| 样式 | Tailwind CSS 4 | 98,699 星，1.91 亿；CSS 优先的 `@theme` |

**任务编排为什么选 Turborepo**：国际样本里 Nx 出现 4 次、Turborepo 2 次，这一项按仓库数是 Nx 多。但 Turborepo 的星数和下载量更高（下载是 Nx 的 3 倍），shadcn/ui 官方单仓模板用的是它，而且它与构建工具无关；Nx 的 Module Federation 生成器绑的是 webpack 和 Rspack。裂变出去的公司也更容易上手。所以选 Turborepo，这一项我定。

### 标准、论文与书

- Peltonen, Mezzalira, Taibi. Motivations, benefits, and issues for adopting Micro-Frontends: A Multivocal Literature Review. Information and Software Technology 136（2021）106571。文中列的主要问题，本设计的对策是：
  - 包体积大：共享单例依赖；
  - 代码重复：共享包；
  - 体验不一致：统一主题和物料；
  - 监控复杂：主应用统一捕获子应用的加载错误。
- Cam Jackson, Micro Frontends（martinfowler.com，2019）。本设计取其中三点：运行时 JavaScript 集成、按业务域垂直拆分、子应用之间少通信并经约定接口。
- Luca Mezzalira, Building Micro-Frontends（O'Reilly）。决策框架：按业务域垂直拆分、浏览器端组合、由主应用负责路由。
- W3C Design Tokens Community Group：Format Module 2025.10、Resolver Module 2025.10。
- WAI-ARIA Authoring Practices Guide：Radix 按它实现交互；树形控件等 shadcn/ui 没有的组件，也按它自己实现。
- OWASP CSRF Prevention Cheat Sheet：单页应用用自定义请求头带令牌（第5阶段已落实，保持不变）。
- Semantic Versioning 2.0.0：Changesets 按它给共享包定版本。

## 方案比较

| 方案 | 结论 |
|---|---|
| A. Umi + qiankun（国内主流，路线原定） | 用户 11:58 否决 |
| B. Umi（webpack）+ Umi 的 mf 插件 | Umi 不是国际主流（umi 周下载 8.2 万）；mf 插件直接用 webpack 自带的 ModuleFederationPlugin，没有 2.0 的清单、类型提示和运行时插件。排除 |
| **C. Vite + React Router + Module Federation 2.0 + shadcn/ui** | **选用**：每一层都是国际主流 |
| C'. 同 C，组件库换 MUI | 备选，见上文“UI 组件库” |
| D. Rsbuild/Rspack + Module Federation 2.0 | Rspack 原生支持 2.0，但用量远低于 Vite（Rsbuild 255 万、Rspack 1316 万，Vite 2.89 亿）。留作退路：Vite 插件卡住时改用 |
| E. Nx + webpack/Rspack 的 Module Federation 生成器 | 绑定 webpack 和 Rspack，与选 Vite 冲突 |

## 现状（2026-11-22 核对）

- admin-web 1.2.3（main `5031838`）：Umi Max 4.7.36 加 utoopack、React 19.3、antd 6.7、ProComponents 3.1.15-9、@antv/g6。
- 路由按域分为：
  - `/dashboard`
  - `/system/{tenant,dept,user,role,resource}`
  - `/message/send`
  - `/ai/model`
  - `/configuration/{models,storage}`
  - `/rag-knowledge/{documents,graph}`
  - 外加 `/login?error`
- 共享组件 11 个。跨域耦合有：
  - `Configuration/Model` 引用了 `Ai/Model` 的 `ModelRouteWorkbench`；
  - `AppPageContainer` 用在 6 个域，`AppList` 用在 3 个，`TenantSelect` 用在 2 个；
  - 6 个页面读全局初始状态。
- 测试：单元测试 32 个文件、201 条；浏览器固定数据一组 5 个文件、28 条；真实网关一组 1 个文件、5 条。CI 是一个任务，SHA 锁定的 Actions。
- 契约（不变）：
  - 登录：浏览器整页跳 `GET /oauth2/authorization/keycloak`。
  - 退出：`POST /logout`，返回 202 和注销地址，前端整页导航过去。
  - 接口：`/auth/**`、`/message/**`、`/api/**` 等，与页面同源，开发时由代理转给网关。
  - 请求头：`X-Requested-With: XMLHttpRequest`；写请求带 `X-XSRF-TOKEN`，取自 Cookie；`X-Tenant-Id` 带选中的租户。
  - 任何请求 401，都整页跳登录入口。
  - 本地存储只放选中的租户。
- 模板遗留的 `src/pages/{Access,Home,Table}` 和 `services/demo` 不在路由里，重写时不带过去。

## 改动设计

### 仓库结构（仍是 admin-web 一个仓库）

```text
admin-web/
  apps/
    shell/     主应用：布局、侧边栏菜单、页头（账户菜单、租户切换、消息铃铛位）、工作台、登录失败页
    system/    子应用：租户、部门、用户、角色、权限资源
    message/   子应用：消息发送；另向主应用暴露消息铃铛组件
    ai/        子应用：AI 模型、统一配置（模型与能力、文件存储），合并消掉跨域引用
    rag/       子应用：文档向量管理、检索测试、知识图谱
  packages/
    ui/              物料：shadcn/ui 组件源码与业务组件，Storybook，注册表
    theme/           设计令牌 → CSS 变量与 TS；默认主题、暗色
    theme-<名称>/    一个额外主题包，证明“换主题就是换包”
    api/             axios 实例与拦截器、接口类型、TanStack Query 工具
    auth/            会话初始化（当前用户、资源、租户）、权限判断、路由守卫
    i18n/            i18next 初始化与各应用的文案
    config-vite/     Vite 预设：React、Tailwind、Module Federation、代理、环境变量
    config-eslint/   ESLint 扁平配置，含子应用之间的引用边界规则
    config-typescript/
    test-utils/      MSW 处理器、渲染工具
  e2e/        Playwright：固定数据一组、真实网关一组，跨主应用和子应用
  turbo.json  pnpm-workspace.yaml（含 catalog）
```

包名作用域先用 `@admin-web/*`，只在仓库内部用；发布前按维护者的 npm 账号再定。

### 微前端

- **加载**：主应用 `shell` 是宿主，四个子应用是远程模块。每个子应用构建时产出 `mf-manifest.json`；主应用启动时读一份运行时配置 `apps.json`（子应用名、清单地址、负责的路由前缀）。所以加一个子应用不用重新构建主应用。
- **路由与菜单**：每个子应用暴露 `./routes`，内容是路由对象（页面懒加载）和菜单元数据（路径、文案键、所需权限）。主应用启动时并行加载各子应用的 `./routes`，再用它们建出唯一的路由器和侧边栏。`/ai/*` 和 `/configuration/*` 归 `ai` 子应用。某个子应用加载失败时，它的菜单项标为不可用，它的路由显示错误页。
- **共享单例**：react、react-dom、react-router、@tanstack/react-query、i18next、react-i18next，以及 `@admin-web/{ui,auth,api,i18n}`。版本由 pnpm catalog 统一，共享配置用 `singleton` 和 `requiredVersion`。
- **故障**：子应用加载失败时，只有该域显示错误页和“重试”，其它域照常。用运行时插件 @module-federation/retry-plugin 重试。主应用记录加载错误。
- **单独运行**：每个子应用都有一个只在开发时用的最小外壳，配同样的代理，可以单独跑起来。登录统一到主应用的 8000 端口完成（见修订记录 12:30）。
- **样式**：主题变量和 Tailwind 的基础样式只由主应用加载一次；子应用只带自己用到的工具类，配置相同，重复不会冲突。挂其它技术栈的应用时，按官方 CSS 隔离指南处理。

### 会话、请求与权限（契约不变）

- **开发代理**：主应用和各子应用的 Vite `server.proxy` 都配同一组前缀转给网关（`ADMIN_WEB_GATEWAY_URL`）：
  - 业务前缀 `/auth`、`/user`、`/message`、`/report`、`/api`；
  - 网关自己的前缀 `/oauth2`、`/login/oauth2`、`/logout`。
- **请求拦截**：`@admin-web/api` 的 axios 拦截器沿用现行规则：
  - 统一带 `X-Requested-With`；
  - 写请求带 `X-XSRF-TOKEN`；
  - 带 `X-Tenant-Id`；
  - 401 整页跳登录入口；
  - 不带 `Authorization`；
  - 带灰度头 `X-Release-Version`、`X-Traffic-Lane`，按需再带 `X-Canary-Tag`、`X-Canary-Weight`。取值来自各应用自己的构建环境变量，校验规则和默认值照旧。每个应用用 `createHttpClient` 各建一个客户端。
- **退出**：经网关 `POST /logout` 后导航到返回的地址，先清掉选中的租户。
- **权限**：`@admin-web/auth` 在主应用启动时取当前用户、资源和可切换的租户，经共享单例提供给子应用。路由守卫和按钮权限照搬 1.2.4 的 `access.ts`：同时看权限码、`menu:*` 资源编码和管理员分类。

### 物料

- **`packages/ui`**：用 shadcn/ui 命令行按官方单仓做法放组件源码。
- **业务组件**补齐现有能力：
  - 页面容器；
  - 服务端分页数据表（TanStack Table 加筛选）；
  - 表单弹窗和抽屉（react-hook-form 加 zod）；
  - 编码输入（调后端编码接口）；
  - 租户选择、确认删除、空状态；
  - 树形选择（按 WAI-ARIA 的树形模式）；
  - 上传。
- **Storybook 10**（React + Vite）：给每个物料写文档和交互测试，并开启无障碍检查。
- **注册表**：按 shadcn/ui 注册表格式，用 `shadcn build` 产出组件、页面模板（增删改查页）和主题。裂变出去的公司用 `npx shadcn add <地址>` 取用。托管地址以后再定。
- **生成器**：`turbo gen` 提供“新建子应用”模板，带好 Vite 与 Module Federation 配置、路由、文案和测试骨架。

### 主题

- **令牌源文件**：`packages/theme/tokens/*.json`，用 DTCG 2025.10 格式，包括颜色、圆角、字体、间距、阴影，分浅色和暗色两套。
- **生成**：用 Style Dictionary 5 生成两样东西。
  - `theme.css`：变量名与 shadcn/ui 一致（`--background`、`--primary` 等），并用 Tailwind 4 的 `@theme inline` 映射；
  - `tokens.ts`：给图表、图谱等非 CSS 场景用。
- **选主题就是选包**：应用在样式入口里引入哪个主题包，用的就是哪套主题。额外做一个主题包来证明这一点。
- **运行时**：在浅色、暗色、跟随系统之间切换，记在本地存储里，只是个人偏好。

### 插件

- **构建时**：`@admin-web/config-vite` 导出 `defineAppConfig({ name, role: 'host' | 'remote', exposes, port })`，组合 React、Tailwind、Module Federation、代理和环境变量。这相当于 Umi 插件集在 Vite 生态里的做法。
- **运行时**：用 Module Federation 的运行时插件做加载重试和错误上报。
- **裂变出去的公司加功能**有三步：
  1. 用生成器建子应用；
  2. 在 `apps.json` 加一行；
  3. 需要按权限控制显示时，在后端加对应的资源编码或权限码，并在权限规则里加一条。

### 多语言

- 每个应用一个命名空间，默认简体中文，英文完整一份。
- 后端返回的提示原样显示。
- 用 lint 规则拦住写死在界面里的中文。

### 测试与 CI

- **单元与组件**：Vitest 加 Testing Library。
- **接口**：用 MSW 验证请求路径、参数、请求头和响应转换。
- **物料**：Storybook 交互测试。
- **浏览器**：Playwright 跑两组。
  - 固定数据一组照现在的做法：每个菜单页及其弹窗、抽屉打开时控制台干净，没有漏拦截的接口；页头账户菜单、登录失败页、图谱样式都要测。
  - 真实网关一组照现在的五条用例跑。`run_chain_verify.py --admin-web-e2e` 跟着改。
- **Module Federation 专项**：
  - 只重建一个子应用，主应用不重建也能加载到新版本；
  - 停掉一个子应用时，只有该域报错。
- **CI**：GitHub Actions 加 Turborepo，按改动范围跑格式、lint、类型、测试、构建和固定数据一组浏览器测试；Actions 按 SHA 锁定，保留密钥扫描。
- **截图对比**：旧应用（合入前的 main）与新应用各截一套，交用户过目。

### 替换方式（用户 12:15 定：整个替换，不逐步淘汰）

在 admin-web 的一个短分支（`codex/` 前缀）上一次替换完：

1. 第一个提交删掉旧 Umi 应用：`src/`、`config/`、`.umirc.ts`、Umi 与 antd 系的依赖、旧的单元测试与浏览器测试。同时建好单仓工具链。接口固定数据 `e2e/fixtures` 与界面无关，留给新测试用。
2. 做共享包（配置、主题、物料、请求、会话、多语言）和 Storybook。
3. 依次做主应用和四个子应用，每做完一个，补齐它的对等测试。
4. 满足下面全部条件后定版本，快进合入 main。版本默认按 `VERSIONING_SPEC.md` 取下一个号（1.2.5）；用户指定 2.0.0 就用 2.0.0。合入条件：
   - 全部菜单页及其弹窗、抽屉与 1.2.4 对齐；
   - 门禁、浏览器两组、Module Federation 专项全过；
   - 截图对比经用户确认。

分支期间 main 上仍是旧应用，这只是正常的分支流程，不是新旧并存。截图对比的“旧”取自合入前的 main。共享包不单独发版，标为 private、版本保持 0.0.0。

## 验证

- 每一步都跑：Turborepo 门禁（格式、lint、类型、测试、构建）、固定数据一组浏览器测试、密钥扫描，推送后读回精确提交的 CI。
- 合入前另外验证：
  - 真实网关一组全过；
  - 上文的 Module Federation 专项全过；
  - 截图对比经用户确认。
- C0110 修好之前，真实网关一组首跑撞上它的，按已知后端缺陷记录，不算前端回归。

## 回退

- **合入前**：main 上一直是旧应用，分支不影响它。
- **合入后**：用 `git revert` 撤销合入的那批提交，main 恢复成合入前的内容，不改写历史。回滚动作按 `BRANCHING_SPEC.md` 的授权要求先确认。旧应用在 Git 历史里完整保留。

## 不在本部分

- 生产托管与发布流水线；把共享包发布到 npm；注册表的公网托管。
- 后端接口、网关、Keycloak 登录页主题。
- 后端的国际主流复核。
- 视觉改版与新功能。

## 需要安装的工具（设计批准后安装）

- 都是项目依赖，由 pnpm 安装，版本写进 catalog。Node 26、pnpm 12.21.0 沿用。
- Playwright 浏览器沿用已安装的版本。
- 不装全局工具。

## 留给实施确定的细节

- 各依赖的确切版本，取实施时的最新稳定版。
- TypeScript 用 6.0 还是 7.0：以 typescript-eslint 与 Vite 生态是否支持为准。
- React Router 的确切大版本：不用 Bridge 时取最新；用 Bridge 时，取在其兼容范围内的版本。
- 共享单例清单的细调；Tailwind 基础样式在子应用里怎么排除。
- 端口：主应用沿用现在的端口，子应用依次往后排。
- 树形选择的实现：自己按 WAI-ARIA 写，或用国际主流的无样式树组件。
- Cytoscape.js 用哪种布局；图谱样式测试怎么改写。
- Storybook 插件选哪些；注册表放在哪个目录。
- 测试文件的布局，以及固定数据从旧应用迁过来的方式。
