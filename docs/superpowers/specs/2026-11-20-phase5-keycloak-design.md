# 第5阶段 Keycloak 统一认证设计

## 已确认目标

用户2026-11-13定：个人GitHub微服务按6个阶段升级，认证采用GitHub上的主流方案，选定Keycloak；第5阶段内容为NAS部署Keycloak（PostgreSQL单独建库）、网关改校验Keycloak令牌、user替换自研授权接口、admin-web跳转Keycloak登录、开启Nacos客户端鉴权、jjwt退役，并让ai拿到统一的登录身份、审计列有值（第3阶段遗留）。

用户2026-11-20在会话里依次确认（新加坡时间）：

- 20:55「在这里完成此任务」：第5阶段在第4阶段同一会话里做。
- 21:16 改造范围选「服务间也一起换」：除了人的登录，服务对服务的调用也改用Keycloak签发的令牌，删掉user的服务签名接口。
- 21:49 浏览器登录选「网关 BFF」：网关充当登录客户端，浏览器只拿会话Cookie，令牌只留在服务端。
- 21:52 新规则：「记住所有升级都要求找github主流方案和主流做法，使用文献和论文等。不要自己为了减少改动范围采用偷懒的。」本设计的每个取舍都写了主流依据（见「主流依据」），改动范围由主流做法决定。
- 22:25「可以删除接口，现在也没什么真实业务，删完后面补上就可以了」：旧接口与按新方案不再需要的接口直接删除，不做兼容层、不设过渡期；以后真要用时再按新方案补。
- 22:3x 四项选择均取推荐：Keycloak的TLS用mkcert本地证书；7个现有账号重设密码；全链路的登录步骤用Playwright走真实登录页；PostgreSQL连接上限保持70，Keycloak连接池上限10。

## 修订记录

- 2026-11-20 21:5x：按用户新规则重查后改了三处先前的口头提议。①各服务不再“只用JWT解码器、不引入整套Spring Security”，改为各服务都是标准的Spring Security资源服务器；②服务间“代表用户调用”不再“转发用户令牌加客户端凭证”，改为RFC 8693令牌交换（OWASP身份传递速查表：接收方的策略要区分“哪个服务在调用、代表谁”时用令牌交换）；③Keycloak管理接口用官方`keycloak-admin-client`，不自己写HTTP调用。

## 目标与反目标

目标：

- 登录与令牌只由Keycloak负责：user不再签发、刷新、注销令牌，不再保存密码；jjwt与写死在utils里的HS256签名键一起删除。
- 浏览器走BFF：admin-web经网关登录，浏览器只拿HttpOnly会话Cookie；网关把访问令牌转给后端服务，到期自动刷新。
- 每个服务都自己验令牌（签名、签发方、受众、有效期），不信任绕过网关的请求；业务权限（租户、部门、数据权限、权限码）仍由user管理，按请求加载并缓存。
- 服务间调用都用Keycloak令牌：纯机器调用用客户端凭证；代表用户的调用用令牌交换，接收方同时看得到调用服务与用户。
- ai接入统一身份，ai写库的创建人、更新人、创建部门有值。
- Nacos开启客户端鉴权，所有客户端用账号访问。

反目标：

- 不把租户、部门、数据权限、菜单权限搬进Keycloak（权限模型留在user，避免两边同步）。
- 不为少改代码保留旧的自研令牌、HMAC服务签名或共享密钥并行运行；切换后旧路径删除。
- 不在浏览器里保存访问令牌或刷新令牌。
- 不引入未经主流项目验证的自研安全协议；只用OAuth 2.0 / OIDC标准流程与Spring Security、Keycloak官方能力。
- 不为兼容保留旧接口（用户22:25同意直接删除，以后需要再补）；admin-web还在用的业务接口（`/auth/manage/*`里的用户、角色、部门、菜单、租户，`/auth/current/*`）照常保留，OAuth客户端与第三方客户端的管理接口及页面一并删除。

## 判定口径

- “主流”按多个同类主流项目的多数做法，加官方文档、RFC与标准、OWASP/NIST指南和论文；只有一家这样做不算主流。
- 运行验证以本机IDEA起9个服务、NAS真实Keycloak为准；CI里用Testcontainers起Keycloak做集成测试（本机没有Docker）。
- “完成”指精确提交CI全绿、制品读回一致、Nacos发布读回、整体运行验证通过、Wiki归档回执拿到。

## 主流依据

### GitHub主流项目

| 项目 | 与本阶段相关的做法 |
|---|---|
| JHipster（Spring + Keycloak最有代表性的生成器，默认OIDC提供方就是Keycloak） | OIDC模式在服务端完成登录（回调在服务端`/login/oauth2/code/oidc`），网关把令牌转给各微服务，微服务自己验JWT |
| Spring Cloud Gateway官方文档 | `TokenRelay`过滤器把会话里的访问令牌转给下游；配合`spring-boot-starter-security-oauth2-client` |
| Baeldung《OAuth2 BFF with Spring Cloud Gateway》、Piotr Minkowski《Spring Cloud Gateway OAuth2 with Keycloak》 | 网关做BFF：`oauth2Login`、Spring Session存Redis、CSRF、TokenRelay、下游资源服务器 |
| pig、芋道、若依（国内后台主流） | 用户、角色、菜单、数据权限放在自家库里，按请求加载；安全公共层调用用户服务的契约（pig的公共安全模块依赖upms-api，芋道的安全starter依赖system-api） |
| Keycloak官方Terraform Provider（2024-12由Keycloak接管） | Keycloak 2024-09调查：域配置管理工具里Terraform约占一半，keycloak-config-cli约16% |
| dasniko/testcontainers-keycloak（Testcontainers官方指南采用） | CI里用容器起Keycloak做Spring Boot集成测试 |
| keycloak-admin-client（官方） | Java程序管理Keycloak用户的官方客户端，另有Jackson 3版本 |
| PyJWT `PyJWKClient` | Python服务按JWKS验Keycloak令牌（签发方、受众、算法白名单） |
| mkcert（开发环境本地证书的通用工具） | 开发环境给非localhost主机签可信TLS证书 |

### 标准与指南

- RFC 10017（2026-08，BCP）OAuth 2.0浏览器应用：BFF排第一，对业务应用、敏感应用、处理个人数据的应用“strongly recommended”；BFF必须是机密客户端、走授权码流程，Cookie必须Secure、HttpOnly。
- RFC 9700（OAuth 2.0安全最佳实践）、RFC 7636（PKCE）、RFC 9068（JWT访问令牌）、RFC 8725（JWT最佳实践：校验算法、签发方、受众、类型、有效期，不能只验签名）。
- RFC 8693（令牌交换）；Keycloak 26.2起“标准令牌交换（V2）”正式支持，只限同域内部交换，请求方必须是机密客户端，受众只能收窄不能扩大。
- RFC 6749 §4.4（客户端凭证）；RFC 9728（受保护资源元数据，Spring Security 7内置支持）。
- OWASP《Microservices Security Cheat Sheet》：只在网关做授权不够，每个服务自己校验；调用方身份要单独认证。
- OWASP《Identity Propagation Patterns Cheat Sheet》：令牌本就发给接收方、接收方同样校验时可以直接转发；受众要改变或策略要区分调用服务与用户时用令牌交换；不要为了能转发而放宽受众。
- NIST SP 800-204（2019）、SP 800-204A（2020）：认证与访问管理是微服务安全的核心功能，网关与服务网格各司其职。
- Keycloak官方《Configuring Keycloak for production》、TLS、反向代理、《Concepts for sizing CPU and memory resources》（每实例基础内存约1250MB，含域缓存与1万个会话；堆占容器上限70%）。
- Nacos官方鉴权文档：内置`nacos`鉴权（RBAC），令牌密钥Base64解码后至少32字节。

### 论文

- 《Authentication and Authorization in Microservices Architecture: A Systematic Literature Review》，Applied Sciences 12(6):3023，2022：文献中最常见的是OAuth 2.0、JWT、API网关、OIDC与单点登录，开源实现多用Spring。
- 《Microservice security: a systematic literature review》，PeerJ Computer Science，2022，DOI 10.7717/peerj-cs.779。
- Barabanov、Makrushin，《Authentication and authorization in microservice-based systems: survey of architecture patterns》，arXiv:2009.02114，2020。

## 方案比较

| 方案 | 做法 | 结论 |
|---|---|---|
| 一：浏览器直连Keycloak | admin-web用授权码加PKCE当公共客户端，令牌在浏览器内存 | 项目数量上最多；RFC 10017不建议用于处理个人数据的应用。未选 |
| **二：网关BFF（用户选）** | 网关当机密客户端登录，会话存Redis，浏览器只拿Cookie；TokenRelay转令牌 | RFC 10017推荐、JHipster同类做法 |
| 三：令牌自带业务权限 | 租户、部门、数据权限写进Keycloak令牌 | 权限模型要搬进Keycloak并双向同步，改权限要等令牌过期。未选 |

服务间：用户选“一起换”。按OWASP身份传递速查表：网关到各服务转发同一受众的用户令牌；ai调ai-agent要区分“ai在代表某用户调用”，用令牌交换；rag调RAG引擎是纯机器调用，用客户端凭证。

## 现状（2026-11-20核对）

- user自签HS256令牌（utils `JwtUtils`，jjwt 0.9.1，签名键写在代码里）：登录、刷新、注销`/auth/sessions*`，第三方会话`/auth/third-party/sessions`，签名校验`/auth/open/signatures/verify`；自带OAuth授权接口`/oauth2/token`、`/oauth2/authorize`与发现文档。令牌里直接带租户、部门、数据权限、权限码与角色；Redis管吊销与令牌版本。
- 网关不验令牌。user、ai-memory、message、rag、report、mcp经utils `SecurityAuthenticationFilter`各自验；ai经user的`AuthSessionRpcService`验，拿不到部门，审计列为空。
- 服务间：ai调ai-agent用HMAC签名（`X-Open-*`，客户端`github-agent`，经user的`AuthOpenApiRpcService`验签）并转发用户令牌，ai-agent经`AgentSessionVerifier`再验用户令牌；rag调RAG引擎用固定共享密钥（`Authorization: Bearer <api-key>`）。
- mcp自写OAuth资源服务器过滤器（`McpOAuthResourceServerFilter`，经`JwtUtils`验签）；user里登记的OAuth客户端已删除，现在没有在用的MCP客户端。
- user库：有效账号7个、租户2个、没有人同时属于多个租户；OAuth客户端0个有效，第三方客户端1个（`github-agent`，服务签名用），外部身份0个。密码为BCrypt哈希。
- admin-web（Umi Max 4.6、antd 5、React 18）用`UMI_APP_BACKEND_BASE_URL`跨源直连网关，令牌存在前端。
- Nacos服务端鉴权关闭（`NACOS_AUTH_ENABLE=false`，身份键与令牌密钥已配）。
- NAS：PostgreSQL上限70个连接、当前39个；内存可用5240MB、交换区4161MB、vmstat无换出（判据：可用不低于3.5GB且无持续换出）。本机没有Docker。

## 改动设计

### NAS：Keycloak（改编排前备份）

- 镜像`quay.io/keycloak/keycloak:26.8.2`（部署时固定摘要），按官方容器指南用Dockerfile执行`kc.sh build`生成优化镜像（启用`health`、`metrics`，数据库`postgres`），在NAS上构建。
- 容器`orb-keycloak`：内存上限1280MiB（官方基础1250MB），堆按官方默认占70%；`db-pool-max-size=10`（PostgreSQL总上限保持70，用户选定）；HTTPS对外`18443`，管理端口`9000`只在compose网络内（健康检查与Prometheus抓取）；`http-enabled=true`只供compose网络内部回源（RAG引擎取公钥），不映射到宿主。
- 主机名：`hostname=https://<NAS主机>:18443`（与本机访问NAS中间件同一主机），`hostname-backchannel-dynamic=true`，签发方固定为该HTTPS地址。
- TLS证书（用户选定）：mkcert生成本地CA签发的证书（SAN为NAS主机），你在本机执行一次`mkcert -install`信任该CA（执行时`JAVA_HOME`指向本机Zulu 21，同时写入Java信任库，IDEA里的服务与Gradle测试取JWKS都用它）。原因：Keycloak 26在非localhost的HTTP下会把`SameSite=None`降为`Lax`，管理控制台与账户控制台需要浏览器安全上下文（Web Crypto）。
- 数据库：在`orb-postgres`新建库`keycloak`与同名角色（只拥有该库）；纳入现有备份方式。
- 引导管理员：用Keycloak 26的临时引导管理客户端（`KC_BOOTSTRAP_ADMIN_CLIENT_ID/SECRET`）供Terraform首次配置，配置完成后建正式管理员（口令进私有文件，由你存入1Password）并删除临时引导账号。
- 观测：Prometheus经compose网络抓取管理端口的`/metrics`，事件日志按现有日志采集。
- 资源复核：上线后按判据读`free -m`与vmstat；预计可用内存约4.0GB。

### Keycloak域配置（Terraform）

- 配置即代码：utils `ops/github-dev/keycloak/terraform/`（官方Provider `keycloak/keycloak`，不含任何密钥），变量来自私有env；状态文件放`~/.config/kellen-github/phase5/terraform/`（0700，含客户端密钥）。本机安装Terraform（HashiCorp官方tap）需你点头后装，并登记进记忆库bootstrap清单。
- 域`github-dev`：中文默认语言（zh-CN），暴力破解防护开，访问令牌5分钟，SSO空闲30分钟、最长10小时，刷新令牌轮换。
- 客户端：
  - `github-gateway`：机密客户端，授权码流程，PKCE S256必填，回调`http://localhost:7100/login/oauth2/code/keycloak`，注销后回到admin-web；默认作用域含`github-api`。
  - `github-api`客户端作用域：受众映射`aud=github-api`；用户属性映射`user_id`（user的用户编号）到访问令牌。
  - `github-ai`：机密客户端，启用服务账号与标准令牌交换（可请求受众`github-ai-agent`）。
  - `github-ai-agent`、`github-rag-engine`、`github-mcp`：只作受众，不开任何流程。
  - `github-rag`：机密客户端，只开客户端凭证，令牌受众`github-rag-engine`。
  - `github-user-admin`：机密客户端，只开客户端凭证，服务账号只给`realm-management`的`manage-users`、`view-users`。
- 注销：网关用OIDC RP发起的注销（浏览器跳转）。Keycloak的后台通道注销要从NAS回调本机网关，本机网关只听`127.0.0.1`，NAS连不到，所以不启用；Keycloak侧禁用或删用户后，网关最迟在访问令牌刷新时（≤5分钟）失效，各服务按业务上下文缓存（≤30秒）拒绝。

### 命名规则

- 客户端与受众：`github-<服务>`；域`github-dev`；令牌声明`user_id`。
- 私有值：`~/.config/kellen-github/phase5/keycloak.env`（0600：管理员、各客户端密钥），本机`dev.env`加`GITHUB_KEYCLOAK_*`与`GITHUB_NACOS_*`（改前备份）。

### utils（破坏性变更：1.7.2 → 2.0.0，推送前按远端顺延）

- 删`JwtUtils`、jjwt依赖、`SecurityAuthenticationFilter`的自签JWT解析与Redis令牌版本校验、网关透传请求头信任。
- 新安全自动配置（servlet）：依赖`spring-boot-starter-security-oauth2-resource-server`；默认`SecurityFilterChain`：无状态、关CSRF（只接Bearer）、白名单（`/actuator/health/**`、OpenAPI文档等，可按服务追加），其余都要认证；`JwtDecoder`按`issuer-uri`取JWKS，校验签发方、受众（默认`github-api`，可按服务改）、有效期，只收RS256。
- 业务上下文：`Converter<Jwt, AbstractAuthenticationToken>`按`user_id`取`SecurityUser`（租户、部门、数据权限范围与部门、权限码、角色、是否禁用）：本地缓存（Caffeine，30秒）→ Redis（user写入与清除）→ 未命中时Dubbo调`SecurityContextRpcService`。活动租户取请求头`X-Tenant-Id`并校验属于该用户，缺省取用户的默认租户。结果写入`UserContextHolder`、`TenantContextHolder`、数据权限上下文与Spring的权限集合，原有权限注解与数据权限插件不变。
- 契约位置：`SecurityContextRpcService`与`SecurityUser`放在utils安全包（不放rpc-api）。理由：现有规则“utils不依赖rpc-api”；主流项目（pig、芋道）也是安全公共层调用用户服务的契约；契约只含认证横切所需字段。
- 机器令牌：没有`user_id`的令牌（客户端凭证）按`azp`与客户端角色识别为机器调用，不写用户上下文。
- 出站：提供`RestClient`的OAuth2拦截器装配（Spring Security `OAuth2ClientHttpRequestInterceptor`），支持客户端凭证与令牌交换授权（`TokenExchangeOAuth2AuthorizedClientProvider`）。
- MQ消费钩子照旧按消息属性还原上下文（第4阶段不变）。
- 文档：新增`docs/ai-coding/AUTH_SPEC.md`（服务接入、受众、白名单、机器令牌、令牌交换、禁止事项），更新`SECURITY_CODING_SPEC.md`与工具目录。

### rpc-api（破坏性变更：1.1.0 → 2.0.0）

- 删`AuthSessionRpcService`、`AuthIdentityRpcService`、`AuthOpenApiRpcService`及其DTO；`UserAccountCreatedEvent`等其余契约不变。

### user

- 用官方`keycloak-admin-client`（Boot 4用Jackson 3版本；若该版本与Boot 4不兼容，计划里先做兼容性验证并记录裁定）管理Keycloak账号：admin-web建用户时同时在Keycloak建登录账号（用户属性`user_id`、初始密码设为临时、首次登录必须改），改资料、启停、删除、管理员重置密码同步到Keycloak。第4阶段的建账号事件不变。
- Flyway V6：`auth_user`加`idp_subject`（Keycloak用户ID，唯一），删`password`；删`auth_oauth_client`、`auth_external_client`、`auth_external_identity`、`auth_external_signature_nonce`。迁移前用`nas_pg.py backup`备份。
- 删除（不留兼容层）：`/auth/sessions*`、`/auth/third-party/sessions`、`/auth/open/signatures/verify`、`/oauth2/*`与发现文档、OAuth客户端与第三方客户端管理接口（admin-web对应页面一起删）、令牌生命周期与第三方认证服务、上述三个RPC提供方。
- 新增：Dubbo提供方`SecurityContextRpcService`；权限、角色、部门、租户关系或账号状态改动时清Redis里的业务上下文。
- 现有账号迁移：脚本按user库创建7个Keycloak账号并回填`idp_subject`；密码按用户选择重设：临时密码写进本机0600私有文件、由你自己查看，首次登录强制改；链路测试账号沿用原口令，由脚本从原私有来源写入、不打印。
- 保留：`/auth/manage/*`、`/auth/current/resources`、`/auth/current/tenants`等业务接口。

### gateway（BFF）

- 依赖`spring-boot-starter-security-oauth2-client`、`spring-boot-starter-security-oauth2-resource-server`、Spring Session Data Redis（WebSession）。
- `SecurityWebFilterChain`：`oauth2Login`（Keycloak、授权码加PKCE）；同时接受Bearer令牌（脚本、Swagger以外的工具）；CSRF用`CookieServerCsrfTokenRepository`（前端读`XSRF-TOKEN`、回传`X-XSRF-TOKEN`）；会话Cookie Secure、HttpOnly、`SameSite=Lax`（Strict会让Keycloak回调丢会话），本机localhost属安全上下文可用Secure；未登录的API请求返回401，页面跳转由前端处理。
- 路由：业务路由加`TokenRelay`，向下游删掉`Cookie`头；会话里的访问令牌到期由授权客户端管理器自动刷新；`/logout`做RP发起的注销。
- 本机dev灰度入口`LocalCanaryIngress`、灰度授权与CORS保持；网关只听`127.0.0.1`不变。
- 网关自身做粗粒度拒绝（未认证的API请求），细粒度授权在各服务（OWASP）。

### admin-web

- 改为同源访问：Umi开发代理把业务接口、`/oauth2/**`、`/login/**`、`/logout`转给网关，`baseURL`改为相对路径（Cookie按主机不分端口，`localhost:8000`与`localhost:7100`共用网关会话；Keycloak登记的回调地址以网关实际生成的为准）；删登录表单与前端令牌存储（`requestAuth.ts`、`sessionBootstrap.ts`里的令牌逻辑），请求带Cookie与`X-XSRF-TOKEN`；收到401跳转网关登录入口；退出调用`/logout`。多租户切换仍用`X-Tenant-Id`。
- 不换Keycloak登录页主题（默认主题加中文）；前端框架升级留第6阶段。

### ai 与 ai-agent（令牌交换）

- ai：引入utils资源服务器，删`AiSessionRpcClient`与各控制器手动解析`Authorization`；用户上下文进入`UserContextHolder`后，审计列按登录用户与部门写入。调ai-agent时，用`github-ai`客户端把当前用户令牌交换成受众`github-ai-agent`的令牌（RFC 8693），删`AiAgentRequestSigner`。
- ai-agent：资源服务器只收受众`github-ai-agent`、`azp=github-ai`的令牌，从中取用户；删`AgentServiceAuthenticationFilter`的HMAC与`AgentSessionVerifier`；读Nacos AI配置的客户端改用Nacos账号登录（见Nacos一节）。

### rag 与 RAG 引擎（客户端凭证）

- rag：用`github-rag`客户端凭证取令牌（受众`github-rag-engine`），经`OAuth2ClientHttpRequestInterceptor`附在调用引擎的请求上；删`api-key`配置。
- RAG引擎（Python）：用PyJWT `PyJWKClient`验令牌（只收RS256，校验签发方为Keycloak的HTTPS地址、受众`github-rag-engine`、`azp=github-rag`、有效期），JWKS经compose网络内部地址取；删共享密钥校验。新镜像经GHCR构建后按摘要在NAS部署。

### mcp

- 改为Spring Security资源服务器，受众`github-mcp`；用Spring Security 7内置的受保护资源元数据（RFC 9728）声明授权服务器为Keycloak，删`McpOAuthResourceServerFilter`。MCP客户端的动态注册按Keycloak官方MCP指南留到有客户端时再开。

### 其余服务（ai-memory、message、report）

- 升utils 2.0.0、改受众与白名单配置，按AUTH_SPEC接入；Dubbo提供方与消费方不变（内网，身份经调用上下文透传）。

### Nacos 客户端鉴权

- NAS：`NACOS_AUTH_ENABLE=true`（改前备份编排，令牌密钥满足至少32字节）；建账号`github-dev-app`（`github-dev`命名空间读写，供服务读配置与注册）；运维脚本用管理员账号。
- 客户端：9个服务的配置与注册（`spring.cloud.nacos.username/password`）、Dubbo注册中心参数、Seata服务端的注册与配置、ai-agent读Nacos AI配置的客户端、kellen-workspace与utils运维脚本。账号口令走私有env，不进Git与Nacos明文。
- 控制台登录由你经1Password完成。

### 各仓库文档

- 各仓库AGENTS、README、`SECURITY_CODING_SPEC.md`里关于自签令牌、jjwt、HMAC签名、共享密钥的说明改为新做法；utils新增`AUTH_SPEC.md`。

### 运维与验证工具

- 私有工具：`nas_kc.py`（Keycloak只读查询：域、客户端、用户数，不输出密钥）；`run_chain_verify.py`与`chain-verify.py`改为用Playwright在真实浏览器里走BFF登录，新增审计列（ai的`create_by`、`create_dept`）、令牌交换、引擎调用、绕过网关直连服务无令牌被拒等步骤。
- `nas_pg.py`、`run_flyway.py`里第3阶段测试角色与env的引用一起修正（第4阶段推迟的小问题6）。

## 验证

1. Keycloak：健康检查通过；Terraform `plan`无差异；只读核对域与客户端；内存与交换区按判据复核。
2. 各仓库：单元测试（Spring Security测试支持的`jwt()`、`mockOidcLogin()`等）、CI里Testcontainers Keycloak集成测试、本机连NAS真实Keycloak的测试（私有env门控，不加载时跳过）；精确提交CI全绿；utils、rpc-api制品读回。
3. Nacos：保值渲染、逐键比对、CAS发布读回；开鉴权后9个服务与Seata注册正常。
4. 整体：9个服务在IDEA启动；两条全链路（带/不带灰度授权）经真实BFF登录全部通过；ai写库的创建人、创建部门等于登录用户与部门；ai调ai-agent的交换令牌受众与`azp`正确；rag调引擎通过；绕过网关直连各服务端口无令牌返回401；禁用用户≤30秒内被拒；退出后会话失效；mcp元数据指向Keycloak。
5. 旧路径：自签令牌、HMAC签名、共享密钥、旧RPC在代码与配置里检索为0。

## 回退

- 代码：各仓库回到本阶段前的版本（utils 1.7.2、rpc-api 1.1.0等），Nacos按发布前快照还原。
- 数据：user库V6前的备份还原；Keycloak库独立，回退时停容器即可。
- NAS：编排改前备份还原（Keycloak、Nacos鉴权）。

## 不在本阶段

- 前端框架升级与微前端（第6阶段）；Keycloak登录页定制主题。
- Keycloak授权服务（UMA、细粒度策略）；权限模型仍在user。
- 服务网格与mTLS（NIST SP 800-204A路线，6阶段之外）；actuator令牌的既有问题（待复核清单已有）。
- MCP客户端动态注册（没有在用客户端）。

## 需要安装的工具（随设计批准后安装，装完登记进记忆库bootstrap清单）

- Terraform：HashiCorp官方tap（`hashicorp/tap/terraform`）当前稳定版，用于Keycloak域配置。
- mkcert：Homebrew官方核心库，用于签发NAS主机的本地证书。
- Playwright（Python）与Chromium：装在专用虚拟环境`~/.config/kellen-github/tools/venv-playwright`，浏览器在`~/Library/Caches/ms-playwright`（约150MB），用于全链路登录。
- 由你本人执行：`mkcert -install`（`JAVA_HOME`指向本机Zulu 21），需要你的密码，会改系统钥匙串与该JDK的信任库。

## 留给实施确定的细节

- 各服务业务版本号（各升一次，推送前按远端顺延）。
- Keycloak各令牌与会话时长的微调、密码策略的具体规则。
- `keycloak-admin-client`的Jackson 3版本是否已发布到与Keycloak 26.8兼容的版本；不兼容时的处理先验证再裁定。
- Terraform资源的拆分与命名。
