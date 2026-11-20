# 第5阶段Keycloak统一认证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NAS上线Keycloak 26.8.2（HTTPS、PostgreSQL单独建库）；登录改为网关BFF、各服务做标准资源服务器、业务权限仍由user提供；服务间改用Keycloak令牌（ai→ai-agent令牌交换、rag→RAG引擎客户端凭证）；开启Nacos客户端鉴权；jjwt与自研认证接口全部删除。

**Architecture:** 先在NAS部署Keycloak并用Terraform配好域，再给utils做资源服务器、业务上下文与出站OAuth2，删除旧认证后一次发2.0.0；rpc-api删旧契约发2.0.0；各服务逐个接入（user管账号与业务上下文、网关做BFF、admin-web改会话登录、ai与ai-agent令牌交换、rag与引擎客户端凭证、mcp受保护资源元数据）；最后开Nacos鉴权、迁移账号、整体切换并验证。

**Tech Stack:** Gradle 9.9.1、JDK 21、Spring Boot 4.0.9（Spring Security 7.0.x、Spring Session、Spring Cloud Gateway Server WebFlux）、`spring-boot-starter-security-oauth2-resource-server` / `-client`、`keycloak-admin-client`（Jackson 3版本）、Testcontainers Keycloak（dasniko）、Keycloak 26.8.2、Terraform（`keycloak/keycloak` Provider）、mkcert、PyJWT（RAG引擎）、Playwright（Python，验证脚本）。

**Spec:** `docs/superpowers/specs/2026-11-20-phase5-keycloak-design.md`

## Global Constraints

- 范围：utils、rpc-api、gateway、user、message、ai、ai-agent、ai-memory、rag（含`engine/`）、mcp、report、admin-web、kellen-workspace；NAS只动新增的Keycloak、Nacos鉴权、Seata连Nacos的账号、Prometheus抓RAG引擎的认证。美联项目不动；不动orb-litellm；代理、VPN、组网相关配置一律不碰。
- 主流规则（用户2026-11-20 21:52）：每个实现选择以GitHub多个主流项目的多数做法与官方文档、RFC、OWASP/NIST、论文为依据；偏离设计或主流做法要在台账写`Ruling:`并附依据；不为缩小改动范围简化。
- 删除（用户22:25）：旧接口、旧表、旧契约、旧配置直接删除，不留兼容层与过渡期。
- 版本（推送前按远端顺延）：utils 1.7.2→2.0.0；rpc-api 1.1.0→2.0.0；gateway 1.2.2→1.3.0；user 1.4.6→1.5.0；ai 1.8.2→1.9.0；ai-agent 0.3.7→0.4.0；rag 1.3.5→1.4.0（Engine 0.3.5→0.4.0）；mcp 1.3.0→1.4.0；admin-web 1.1.1→1.2.0；ai-memory 1.2.2→1.2.3；message 1.3.1→1.3.2；report 1.2.9→1.3.0（补丁位到9进位）。
- Keycloak：镜像`quay.io/keycloak/keycloak:26.8.2`（部署时固定摘要写进编排）；按官方容器指南用Dockerfile执行`kc.sh build --db=postgres --health-enabled=true --metrics-enabled=true`；容器`orb-keycloak`，内存上限1280MiB，`db-pool-max-size=10`；HTTPS宿主`18443`→容器`8443`；管理端口`9000`与`http-enabled=true`的`8080`只在compose网络内；`hostname=https://<NAS主机>:18443`（NAS主机取本机访问NAS中间件所用的同一主机，不写进Git与Wiki），`hostname-backchannel-dynamic=true`；证书由mkcert签发，挂只读卷。
- 域`github-dev`：默认语言zh-CN，暴力破解防护开；访问令牌5分钟；SSO空闲30分钟、最长10小时。客户端：`github-gateway`（机密，授权码，PKCE S256必填，默认作用域含`github-api`）、`github-ai`（机密，服务账号，标准令牌交换，可请求受众`github-ai-agent`）、`github-rag`（机密，只开客户端凭证，受众`github-rag-engine`）、`github-prometheus`（机密，只开客户端凭证，受众`github-rag-engine`，只用于抓`/metrics`）、`github-user-admin`（机密，只开客户端凭证，服务账号只给`realm-management`的`manage-users`、`view-users`）；`github-ai-agent`、`github-rag-engine`、`github-mcp`只作受众。客户端作用域`github-api`：受众映射`aud=github-api`，用户属性`user_id`映射为访问令牌声明`user_id`。
- 已定裁定（写计划时定，执行时台账各记一条`Ruling:`，设计修订记录同步）：
  - `github-gateway`不开刷新令牌轮换（设计写的是“刷新令牌轮换”）。依据：RFC 9700对机密客户端以客户端认证约束刷新令牌，轮换主要针对公共客户端；BFF并发刷新时轮换会让同一会话的其他请求失效。
  - 新增客户端`github-prometheus`：Prometheus抓RAG引擎`/metrics`现在也靠那把共享密钥，按“服务间一起换”改为Prometheus内置的OAuth2客户端凭证抓取。
  - OTel Collector接收遥测的静态令牌不换：OTel Java探针的OTLP导出只支持固定请求头，不能自行取OAuth令牌；主流做法是静态令牌或mTLS。
  - 顺带修第4阶段的缺口：RocketMQ的user、message两组口令现在分发给全部9个服务，按最小权限加作用域（任务5）。
- 资源服务器配置（Spring Boot原生属性，每个服务自己的`<服务>-spring.yaml`里成整棵树）：`spring.security.oauth2.resourceserver.jwt.issuer-uri: ${custom.keycloak-issuer-uri}`、`jws-algorithms: RS256`、`audiences`（默认`github-api`；ai-agent为`github-ai-agent`，mcp为`github-mcp`）。`custom.keycloak-issuer-uri`在`reuse-configuration.yaml`，取`${GITHUB_KEYCLOAK_ISSUER_URI}`。
- utils配置前缀`fleet.security.oauth2`：`permit-paths`（缺省`/actuator/health/**`、`/v3/api-docs/**`、`/swagger-ui/**`、`/error`）、`user-id-claim`（缺省`user_id`）、`context-cache-ttl`（缺省30秒）、`machine-clients`（允许的机器令牌`azp`列表，缺省空）、`required-azp`（缺省空，ai-agent为`github-ai`）、`token-exchange.<registrationId>.audience`。
- 业务上下文缓存：本地Caffeine（TTL 30秒、上限10000）→ Redis（键`fleet:security:ctx:<userId>:<版本>:<租户或_>`，TTL 30分钟；版本键`fleet:security:ctx:v:<userId>`，user改动时`INCR`）→ Dubbo `SecurityContextRpcService`。活动租户取请求头`X-Tenant-Id`，缺省取用户默认租户。
- 浏览器会话：Spring Session Redis（WebSession），空闲30分钟；Cookie名`__Host-SESSION`，Secure、HttpOnly、`SameSite=Lax`、Path `/`；CSRF Cookie `XSRF-TOKEN`、请求头`X-XSRF-TOKEN`。
- 私有值：`~/.config/kellen-github/phase5/`（0700）下`keycloak.env`（0600：数据库口令、引导管理客户端、正式管理员、各客户端密钥）、`terraform/`（状态，0700）、`fleet-kc-test.env`（0600：真库测试用）、`accounts-temporary-passwords.txt`（0600，你自己看）。本机`dev.env`新增`GITHUB_KEYCLOAK_ISSUER_URI`、`GITHUB_KEYCLOAK_GATEWAY_CLIENT_SECRET`、`GITHUB_KEYCLOAK_AI_CLIENT_SECRET`、`GITHUB_KEYCLOAK_RAG_CLIENT_SECRET`、`GITHUB_KEYCLOAK_USER_ADMIN_CLIENT_SECRET`、`GITHUB_NACOS_ADMIN_USERNAME`、`GITHUB_NACOS_ADMIN_PASSWORD`；`GITHUB_NACOS_USERNAME/PASSWORD`改为服务账号；删`GITHUB_RAG_ENGINE_API_KEY`。每次改前备份`dev.env.before-<事项>-20261120`。
- 环境变量作用域（kellen-workspace）：`githubkeycloakgateway`→gateway、`githubkeycloakai`→ai、`githubkeycloakrag`→rag、`githubkeycloakuseradmin`→user、`githubrocketmquser`→user、`githubrocketmqmessage`→message、`githubnacosadmin`→不分发给任何服务。
- 测试：单元测试用Spring Security测试支持；CI集成测试用Testcontainers Keycloak（`@Testcontainers(disabledWithoutDocker = true)`，本机无Docker时跳过）；本机真库测试读`fleet-kc-test.env`，不加载时跳过；构建一律`export JAVA_HOME=$(/usr/libexec/java_home -v 21)`。
- Git：开工、提交前、推送前`git fetch`；每个任务从最新`origin/main`建短分支`codex/phase5-<任务>`（`--no-track`），本机验证全过后快进合入`main`、推送、删短分支；不强推；推送后读回精确提交CI（user、message、ai推送不触发CI，手动`gh workflow run CI --repo sunkailun1992/<仓库> --ref main`并核headSha）。
- NAS：只用`ssh -o BatchMode=yes qw-nas`与`sudo -n docker …`；改编排前备份`docker-compose.yml.before-<事项>-20261120`；文件操作用临时容器；输出遮掉IPv4与9位以上数字串；不打印任何口令。
- 凭据：私有Maven只经`run-maven-release.py`；Nacos只经私有`dev.env`与`utils/ops/github-dev/scripts`；测试登录口令只在子进程环境里使用；任何密钥不进Git、Wiki、Nacos明文与对话。
- 运行态：9个服务（端口7100、7200、7300、7301、7310、7400、7500、7600、7700）由用户在IDEA启停；运行配置不挂JVM参数；不用IDEA MCP的`execute_run_configuration`运行这九个配置。
- 时间写新加坡时间。台账：`kellen-workspace/.superpowers/sdd/2026-11-20-phase5-keycloak/progress.md`，逐任务记完成行与`Ruling:`行；可复用脚本放`~/.config/kellen-github/tools`。

## Review Focus

1. Keycloak或JWKS不可达（NAS链路断）时：各服务照常启动，受保护接口返回401、不放行、不打印堆栈——任务6`startsWhenIssuerUnreachableAndRejectsRequests`。
2. 访问令牌到期瞬间的多个并发请求：都成功，用户不被踢下线——任务14`concurrentRequestsAtExpiryAllSucceed`。
3. 本机与NAS时钟差：60秒内的偏差接受，超过拒绝——任务6`clockSkewWithinSixtySecondsAccepted`、`clockSkewBeyondSixtySecondsRejected`。
4. ai调ai-agent时令牌交换失败（Keycloak不可用或拒绝）：ai返回明确错误，绝不在没有交换令牌时调用ai-agent——任务16`tokenExchangeFailureNeverCallsAgent`。
5. 经网关的写请求没带CSRF头：返回403 JSON；Swagger调试带头成功——任务14`postWithoutCsrfIsForbidden`、`swaggerUiSendsCsrfHeader`。

---

### Task 1: 提交计划

- [ ] **Step 1:** 本计划提交到kellen-workspace的`main`并推送（纯文档），读回Workspace CI。Expected：success。

### Task 2: Mac侧工具安装与登记

**Files:**
- Modify: 记忆库`/Volumes/KellenSSD/kellen-knowledge-vault/tools/bootstrap/manifest.yaml`（`tools`节加terraform、mkcert；Playwright虚拟环境写进`paths`与说明）

- [ ] **Step 1:** 安装：`brew install hashicorp/tap/terraform`、`brew install mkcert`；`python3 -m venv ~/.config/kellen-github/tools/venv-playwright`后`pip install playwright`、`python -m playwright install chromium`。Expected：`terraform version`、`mkcert -version`有输出；`venv-playwright/bin/python -c "import playwright"`退出0。
- [ ] **Step 2:** 请用户在自己的终端执行`JAVA_HOME=$(/usr/libexec/java_home -v 21) mkcert -install`（要用户密码）。Expected：`security find-certificate -c mkcert`能找到；`keytool -list -cacerts -storepass changeit | grep -i mkcert`有一行。
- [ ] **Step 3:** 登记manifest，在记忆库根目录跑`ruby scripts/doctor.rb --quiet`、`build_index.rb`、两道门禁，提交推送。Expected：doctor无该三项缺失；“已提交推送 <hash>”。

### Task 3: NAS部署Keycloak（数据库、证书、容器）

**Files:**
- Create（会话草稿区生成器）：`gen_keycloak_compose.py`（口令从`keycloak.env`读，只写0600文件）、`keycloak/Dockerfile`
- Modify（NAS）：编排加`orb-keycloak`服务、证书卷与配置卷

- [ ] **Step 1:** `nas_pg.py`加`create-owner <库> <角色>`子命令（角色只拥有该库、连接上限10），同时把写死的第3阶段测试角色常量改为参数，`run_flyway.py`的env引用改为传入路径（第4阶段推迟的小问题6）；建`keycloak`库与同名角色，口令写入`keycloak.env`。Expected：读回1库1角色，角色只拥有`keycloak`库；`nas_pg.py`与`run_flyway.py`里不再出现`fleet_pg_test_phase3`。
- [ ] **Step 2:** `mkcert`签发证书（SAN为NAS主机），经临时容器拷进NAS卷（0640，属主为Keycloak容器用户）。Expected：`openssl x509 -noout -subject -ext subjectAltName`含NAS主机。
- [ ] **Step 3:** 备份编排，写入`orb-keycloak`（构建上下文为Dockerfile、内存上限、端口、`KC_*`环境、引导管理客户端`KC_BOOTSTRAP_ADMIN_CLIENT_ID/SECRET`、健康检查走9000），`compose config -q`，构建并启动。Expected：健康检查`/health/ready` UP；`curl https://<NAS主机>:18443/realms/master/.well-known/openid-configuration`（用mkcert根证书）返回的`issuer`为该HTTPS地址；其他容器ID与启动时间不变。
- [ ] **Step 4:** 资源复核。Expected：`free -m`可用≥3500MB；vmstat 6次×2秒无持续换出；PostgreSQL连接数≤55。不满足就停下报告用户。

### Task 4: Keycloak域配置（Terraform）与只读核对工具

**Files:**
- Create: `utils/ops/github-dev/keycloak/terraform/`（`versions.tf`、`provider.tf`、`realm.tf`、`clients.tf`、`scopes.tf`、`variables.tf`、`outputs.tf`、`README.md`），不含任何密钥
- Create: `~/.config/kellen-github/tools/nas_kc.py`（只读：域、客户端、作用域、用户数，不输出密钥）
- Test: `utils/ops/github-dev/tests/test_keycloak_terraform.py`（静态检查：客户端与作用域齐全、`github-gateway`的PKCE与回调、`github-user-admin`只有两个角色、无明文密钥）

**Interfaces:**
- Produces：域与客户端名、受众、`user_id`声明（见Global Constraints）；客户端密钥由Keycloak生成，Terraform输出写入`keycloak.env`与`dev.env`（0600）。

- [ ] **Step 1:** 写`test_keycloak_terraform.py`。Expected：失败（目录不存在）。
- [ ] **Step 2:** 写Terraform文件（Provider固定版本；回调`http://localhost:7100/login/oauth2/code/keycloak`与`http://localhost:8000/login/oauth2/code/keycloak`；注销后回`http://localhost:8000/`）。`terraform init`、`plan`、`apply`（凭引导管理客户端）。Expected：测试通过；`apply`成功。
- [ ] **Step 3:** 建正式管理员（口令进`keycloak.env`，提醒用户存入1Password），删除临时引导客户端；Terraform改用正式管理客户端。Expected：`terraform plan -detailed-exitcode`退出0（无差异）。
- [ ] **Step 4:** `nas_kc.py`读回；用`github-rag`客户端凭证取令牌并在本机解码头与声明（不打印令牌）。Expected：`aud`含`github-rag-engine`、`azp=github-rag`、`alg=RS256`。
- [ ] **Step 5:** 提交`feat(ops): Keycloak域配置（Terraform）`，交付并读回CI。

### Task 5: kellen-workspace服务环境变量作用域

**Files:**
- Modify: `kellen-workspace/scripts/service_runtime_environment.py`（`MODEL_SCOPES`同类的作用域表加Global Constraints里的七条）
- Modify: `kellen-workspace/docs/service-environment.md`
- Test: `kellen-workspace/tests/test_service_environment.py`

- [ ] **Step 1: 写失败测试**
  - `keycloakClientSecretsOnlyReachTheirService`：`GITHUB_KEYCLOAK_GATEWAY_CLIENT_SECRET`只在gateway的结果里，`..._AI_...`只在ai，`..._RAG_...`只在rag，`..._USER_ADMIN_...`只在user。
  - `rocketmqCredentialsOnlyReachUserAndMessage`：USER两个键只给user，MESSAGE两个键只给message；`GITHUB_ROCKETMQ_HOST/PORT`仍给全部。
  - `nacosAdminCredentialsReachNoService`；`keycloakIssuerReachesAllServices`。
- [ ] **Step 2:** 运行。Expected：前三条失败。
- [ ] **Step 3:** 实现作用域规则；被限定作用域的键计入`sensitive_github_name`。
- [ ] **Step 4:** `python3 -m unittest discover -s tests`。Expected：全过。
- [ ] **Step 5:** 提交`feat: Keycloak与RocketMQ口令按服务分发`，交付并读回Workspace CI。

### Task 6: utils资源服务器与业务上下文

**Files:**
- Modify: `utils/build.gradle`（`api 'org.springframework.boot:spring-boot-starter-security-oauth2-resource-server'`、Caffeine）
- Create: `utils/src/main/java/com/kellen/security/oauth2/`：`FleetOAuth2Properties.java`、`FleetResourceServerAutoConfiguration.java`、`FleetJwtAuthenticationConverter.java`、`FleetAuthenticationToken.java`、`MachineAuthenticationToken.java`、`SecurityContextRpcService.java`、`SecurityContextResult.java`、`SecurityUserCache.java`、`FleetSecurityContextFilter.java`（把认证结果写进`UserContextHolder`、`TenantContextHolder`与数据权限上下文，请求结束清理）
- Modify: `utils/src/main/java/com/kellen/security/SecurityUser.java`（加`boolean enabled`、`List<String> tenantIds`）；`AutoConfiguration.imports`登记
- Test: `utils/src/test/java/com/kellen/security/oauth2/FleetResourceServerTest.java`（MockMvc + 测试内生成的RSA JWK，`NimbusJwtDecoder`指向本地JWKS）、`SecurityUserCacheTest.java`

**Interfaces:**
- Produces：`interface SecurityContextRpcService { SecurityContextResult resolve(String userId, String requestedTenantId); }`（Dubbo契约，放utils；`requestedTenantId`可为null）。
- Produces：`record SecurityContextResult(Status status, SecurityUser user)`，`enum Status { OK, USER_NOT_FOUND, DISABLED, TENANT_DENIED }`；`Serializable`。
- Produces：`FleetAuthenticationToken extends JwtAuthenticationToken`（带`SecurityUser`，供令牌交换取subject token）；`MachineAuthenticationToken extends JwtAuthenticationToken`（`azp`在`machine-clients`里且没有`user_id`）。
- Produces：`SecurityUserCache.get(String userId, String tenantId, Supplier<SecurityContextResult> loader)`、`SecurityUserCache.bumpVersion(String userId)`。
- Produces：默认`SecurityFilterChain fleetApiSecurityFilterChain(HttpSecurity, ...)`（`@Order`排在utils现有的actuator过滤链之后；无状态；关CSRF；`permit-paths`放行；其余认证；401与403返回统一`ApiResponse` JSON）。仅在`spring.security.oauth2.resourceserver.jwt.issuer-uri`存在时装配。

- [ ] **Step 1: 写失败测试**
  - `missingTokenIs401`、`wrongIssuerIs401`、`wrongAudienceIs401`、`expiredTokenIs401`、`hs256TokenIs401`（只收RS256）。
  - `validUserTokenPopulatesContexts`：`user_id=u1`、加载结果OK（租户t1、部门d1、权限`a:b`）时，处理器里读到`UserContextHolder`为u1/d1、`TenantContextHolder`为t1、`hasAuthority("a:b")`为真；请求结束后三者为空。
  - `disabledUserIs403`、`unknownUserIs401`、`foreignTenantHeaderIs403`（`X-Tenant-Id: t9`返回`TENANT_DENIED`）。
  - `machineTokenWithoutUserIsMachinePrincipal`（`azp=github-rag`在白名单）；`machineTokenNotInAllowlistIs403`。
  - `permitPathsNeedNoToken`（`/actuator/health`、`/v3/api-docs`）。
  - `clockSkewWithinSixtySecondsAccepted`（`iat`早于未来30秒）、`clockSkewBeyondSixtySecondsRejected`（`exp`已过120秒）。
  - `startsWhenIssuerUnreachableAndRejectsRequests`：`issuer-uri`指向不可达地址时上下文能启动，受保护请求得401，日志不含堆栈。
  - `SecurityUserCacheTest`：同一用户30秒内只调用一次loader；`bumpVersion`后Redis键换版本、loader再次调用；Redis不可用时直接走loader。
- [ ] **Step 2:** 运行这两个测试类。Expected：编译失败或全部失败。
- [ ] **Step 3:** 实现：`issuer-uri`用Spring Boot的延迟发现（`SupplierJwtDecoder`）；时钟偏差用`JwtTimestampValidator`默认60秒；转换器调用缓存、缓存回退到`SecurityContextRpcService`（`@DubboReference(check = false)`，没有提供方时按`USER_NOT_FOUND`处理）。
- [ ] **Step 4:** `./gradlew test javadoc`。Expected：全过。
- [ ] **Step 5:** 提交`feat(security): 标准OAuth2资源服务器与按请求加载的业务上下文`，交付并读回CI。

### Task 7: utils出站OAuth2（客户端凭证与令牌交换）

**Files:**
- Modify: `utils/build.gradle`（`api 'org.springframework.boot:spring-boot-starter-security-oauth2-client'`）
- Create: `utils/src/main/java/com/kellen/security/oauth2/client/FleetOAuth2ClientAutoConfiguration.java`、`FleetOAuth2Clients.java`
- Test: `utils/src/test/java/com/kellen/security/oauth2/client/FleetOAuth2ClientTest.java`（MockWebServer当令牌端点与下游）

**Interfaces:**
- Consumes：任务6的`FleetAuthenticationToken`。
- Produces：`OAuth2AuthorizedClientManager`（servlet，含`ClientCredentialsOAuth2AuthorizedClientProvider`与`TokenExchangeOAuth2AuthorizedClientProvider`）；`ClientHttpRequestInterceptor FleetOAuth2Clients.interceptor(String registrationId)`（基于`OAuth2ClientHttpRequestInterceptor`）；`ExchangeFilterFunction FleetOAuth2Clients.webClientFilter(String registrationId)`（基于`ServletOAuth2AuthorizedClientExchangeFilterFunction`）。令牌交换请求带`audience=<fleet.security.oauth2.token-exchange.<id>.audience>`，subject token取当前`FleetAuthenticationToken`的JWT。

- [ ] **Step 1: 写失败测试**
  - `clientCredentialsAttachesBearer`：注册`github-rag`（client_credentials）时，令牌端点收到`grant_type=client_credentials`与客户端认证；下游收到`Authorization: Bearer <返回的令牌>`。
  - `tokenExchangeSendsSubjectTokenAndAudience`：当前认证为用户令牌S时，令牌端点收到`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`、`subject_token=S`、`subject_token_type=urn:ietf:params:oauth:token-type:access_token`、`audience=github-ai-agent`；下游收到交换后的令牌。
  - `tokenExchangeWithoutUserFails`：没有用户认证时抛异常，下游未被调用。
  - `tokensAreCachedUntilExpiry`：两次调用只请求一次令牌端点。
- [ ] **Step 2:** 运行。Expected：失败。
- [ ] **Step 3:** 实现（只用Spring Security内置提供者与拦截器，不自写令牌请求）。
- [ ] **Step 4:** `./gradlew test javadoc`。Expected：全过。
- [ ] **Step 5:** 提交`feat(security): 出站OAuth2客户端凭证与RFC 8693令牌交换`，交付并读回CI。

### Task 8: utils删除旧认证

**Files:**
- Delete: `utils/src/main/java/com/kellen/utils/auth/JwtUtils.java`、`security/SecurityAuthenticationFilter.java`、`security/AuthTokenRedisKeys.java`及对应测试；`build.gradle`里的`jjwtVersion`与jjwt依赖
- Modify: `security/config/SecurityAuthConfig.java`、`SecurityAuthProperties.java`（删`jwt-enabled`、`header-enabled`等旧键，只留数据权限与租户）
- Test: `utils/src/test/java/com/kellen/security/OldAuthRemovedTest.java`

- [ ] **Step 1:** 写`OldAuthRemovedTest`：`io.jsonwebtoken.Jwts`、`com.kellen.utils.auth.JwtUtils`、`com.kellen.security.SecurityAuthenticationFilter`都不可加载。Expected：失败。
- [ ] **Step 2:** 删除与修改。Expected：测试通过。
- [ ] **Step 3:** `./gradlew test javadoc`；`rg -n 'jjwt|io\.jsonwebtoken|JwtUtils' src build.gradle`无输出。Expected：全过、0行。
- [ ] **Step 4:** 提交`refactor(security)!: 删除自签JWT与jjwt`，交付并读回CI。

### Task 9: utils真库与容器集成测试、文档

**Files:**
- Create: `utils/src/test/java/com/kellen/security/oauth2/KeycloakContainerIT.java`（Testcontainers Keycloak 26.8.2，导入测试域，验证资源服务器与令牌交换端到端）、`KeycloakLiveTest.java`（读`fleet-kc-test.env`连NAS，不加载时跳过）
- Create: `utils/docs/ai-coding/AUTH_SPEC.md`（服务接入、受众、白名单、机器令牌、令牌交换、禁止事项、测试）
- Modify: `utils/build.gradle`（`testImplementation`加testcontainers-keycloak，版本取Maven Central当时最新且与Testcontainers 2.x兼容）、`SECURITY_CODING_SPEC.md`、`UTILS_TOOL_CATALOG.md`、README、CI工作流（确保Docker可用）

- [ ] **Step 1:** 写两个测试类。`KeycloakContainerIT`：密码授权取用户令牌访问受保护接口得200；错误受众得401；用`github-ai`交换出的令牌`aud`为`github-ai-agent`、`azp`为`github-ai`。`KeycloakLiveTest`：同样三条，对象是NAS上的`github-dev`。Expected：本机跳过容器测试；真库测试三条通过（连续三轮）。
- [ ] **Step 2:** 写文档。Expected：`rg -n 'jjwt|JwtUtils|HS256' docs README.md`只剩退役说明。
- [ ] **Step 3:** 提交`test(security): Keycloak容器与真库测试，AUTH_SPEC`，推送后CI里容器测试实际执行。Expected：CI日志显示`KeycloakContainerIT`运行并通过。

### Task 10: utils发版2.0.0

- [ ] **Step 1:** 核对2.0.0未占用；`./gradlew clean test javadoc`、`check-secrets`；dependencyInsight核对`spring-security-oauth2-resource-server`、`-client`、`nimbus-jose-jwt`来自Spring Boot 4.0.9管理，jjwt不在类路径。Expected：全过。
- [ ] **Step 2:** 发版提交改`version = '2.0.0'`，经`run-maven-release.py`只发根项目（`clean :publish`），`maven-readback.py utils 2.0.0`。Expected：`READBACK OK`。

### Task 11: rpc-api删除旧认证契约并发版2.0.0

**Files:**
- Delete: `rpc-api/src/main/java/com/kellen/rpc/auth/AuthSessionRpcService.java`、`AuthIdentityRpcService.java`、`AuthOpenApiRpcService.java`、`AuthSessionIdentityRpcDTO.java`及只被它们使用的DTO与测试
- Modify: `rpc-api`的README、`RPC_API_CODING_SPEC.md`；`version = '2.0.0'`

- [ ] **Step 1:** 写`OldAuthContractsRemovedTest`（三个接口不可加载）。Expected：失败。
- [ ] **Step 2:** 删除。Expected：测试通过；`./gradlew clean test javadoc publishToMavenLocal`通过。
- [ ] **Step 3:** 发版并`maven-readback.py rpc-api 2.0.0`。Expected：`READBACK OK`。

### Task 12: Nacos新增认证配置（旧键暂留）

**Files:**
- Modify: `utils/ops/github-dev/nacos/DEFAULT_GROUP/reuse-configuration.yaml`（`custom.keycloak-issuer-uri`）、九个`<服务>-spring.yaml`（资源服务器整棵树；gateway的`spring.security.oauth2.client`注册与TokenRelay路由过滤器、Spring Session；ai的`github-ai`令牌交换注册；rag的`github-rag`客户端凭证注册；user的`fleet.keycloak.admin.*`；ai-agent的`required-azp`与受众）、`seata.yaml`与`dubbo.yaml`（Nacos账号占位）
- Test: `utils/ops/github-dev/tests/`里的注释与结构测试按新增键更新

- [ ] **Step 1:** 改模板（每行紧贴注释，口令只用`${GITHUB_*}`占位），跑运维单元测试与`verify_configuration.py`。Expected：OK、PASS。
- [ ] **Step 2:** 只读盘点并备份→保值渲染→`nacos_verify_candidate.py`（期望新增键清单写成文件）→CAS发布→目录维护→重新盘点。Expected：新增键恰好等于清单、删除0、修改0；21个dataId读回一致。正在运行的旧服务不受新增键影响。
- [ ] **Step 3:** 提交`feat(nacos): Keycloak资源服务器、客户端与Nacos账号占位`，交付并读回CI。

### Task 13: user——Keycloak账号同步、业务上下文提供方、删除旧接口与表

**Files:**
- Modify: `user/build.gradle`（utils 2.0.0、rpc-api 2.0.0、`keycloak-admin-client`的Jackson 3版本）
- Create: `user/src/main/java/com/kellen/auth/keycloak/KeycloakAccountClient.java`、`KeycloakAdminProperties.java`、`user/src/main/java/com/kellen/auth/rpc/SecurityContextRpcServiceImpl.java`
- Create: `user/src/main/resources/db/migration/V6__keycloak_accounts.sql`（`auth_user`加`idp_subject`唯一、删`password`；删`auth_oauth_client`、`auth_external_client`、`auth_external_identity`、`auth_external_signature_nonce`）
- Modify: `AuthUserServiceImpl.java`（建、改、启停、删、重置密码同步Keycloak，并`SecurityUserCache.bumpVersion`）；角色、部门、资源、租户关系改动处也`bumpVersion`
- Delete: `controller/OAuthController.java`、`AuthOAuthClientController.java`、`AuthController`里的`/sessions*`、`/third-party/sessions`、`/open/signatures/verify`；`OAuthAuthorizationServerProperties`、`AuthOAuth*`、`AuthExternal*`、`AuthTokenLifecycleService`、`AuthThirdPartyAuthenticationService*`、相关DTO与Mapper；`rpc/AuthIdentityRpcServiceImpl`、`AuthOpenApiRpcServiceImpl`、`AuthSessionRpcServiceImpl`及测试
- Test: `KeycloakAccountClientTest.java`（MockWebServer模拟Admin REST）、`AuthUserServiceImplKeycloakTest.java`、`SecurityContextRpcServiceImplTest.java`、`OldAuthEndpointsRemovedTest.java`、`PostgresqlSchemaLiveTest`（V6断言）、`KeycloakAccountLiveTest.java`（NAS，env门控）

**Interfaces:**
- Consumes：任务6的`SecurityContextRpcService`、`SecurityContextResult`、`SecurityUserCache`。
- Produces：`KeycloakAccountClient`：`String createAccount(String userId, String username, String nickname, String temporaryPassword)`（返回Keycloak用户ID，设属性`user_id`、必需动作`UPDATE_PASSWORD`）、`void updateProfile(String idpSubject, String username, String nickname)`、`void setEnabled(String idpSubject, boolean enabled)`、`void resetPassword(String idpSubject, String temporaryPassword)`、`void deleteAccount(String idpSubject)`。

- [ ] **Step 1:** 兼容性验证：在user里引入`keycloak-admin-client`的Jackson 3版本，写`KeycloakAdminClientCompatibilityTest`（上下文能启动；Jackson 3与Spring Boot 4.0.9共存；`dependencyInsight`无Jackson 2核心冲突）。Expected：通过；不通过时停下写`Ruling:`（依据官方文档与issue），按官方Admin REST API与Spring `RestClient`实现同一接口。
- [ ] **Step 2: 写失败测试**
  - `createUserCreatesKeycloakAccountWithUserIdAttribute`：建用户时Admin API收到用户名、属性`user_id=<新编号>`、临时密码与必需动作；`idp_subject`回填。
  - `keycloakFailureRollsBackUserCreation`：Keycloak返回500时user库没有新行，接口返回明确错误。
  - `disableUserDisablesKeycloakAccountAndBumpsVersion`、`deleteUserDeletesKeycloakAccount`。
  - `securityContextResolvesRolesPermissionsDeptAndTenant`；`unknownUserReturnsUserNotFound`；`foreignTenantReturnsTenantDenied`；`disabledUserReturnsDisabled`。
  - `oldEndpointsAreGone`：`POST /auth/sessions`、`/auth/sessions/refresh`、`/auth/sessions/logout`、`/auth/third-party/sessions`、`/auth/open/signatures/verify`、`/oauth2/token`、`GET /.well-known/openid-configuration`、`/auth/manage/oauth-clients`均为404。
  - V6：`idp_subject`唯一索引存在、`password`列不存在、四张旧表不存在。
- [ ] **Step 3:** 运行。Expected：失败。
- [ ] **Step 4:** 实现与删除；`./gradlew test`（不带/带PG env）、`check-secrets`；`rg -n -i --hidden 'jjwt|JwtUtils|oauth2/token|third-party|signatures/verify|AuthSessionRpc|AuthOpenApiRpc' src`无输出。Expected：全过、0行。
- [ ] **Step 5:** 迁移前备份user库（`nas_pg.py backup github_dev_user`）；副本库跑V6。Expected：副本迁移成功、行数不变。
- [ ] **Step 6:** 提交`feat(auth)!: 账号交给Keycloak，删除自研认证接口`，交付并读回CI。

### Task 14: gateway——BFF

**Files:**
- Modify: `gateway/build.gradle`（utils 2.0.0不适用WebFlux时单独引入`spring-boot-starter-security-oauth2-client`、`-resource-server`、`spring-session-data-redis`）
- Create: `gateway/src/main/java/com/kellen/config/security/GatewaySecurityConfig.java`、`ApiAuthenticationEntryPoint.java`（API请求401 JSON）、`CsrfCookieWebFilter.java`（订阅CSRF令牌使Cookie下发，按Spring Security SPA指引）
- Modify: `gateway-spring.yaml`（任务12已加的注册与路由）、springdoc `swagger-ui.csrf.enabled: true`
- Test: `gateway/src/test/java/com/kellen/config/security/GatewaySecurityConfigTest.java`（WebTestClient + `mockOidcLogin()`、`mockJwt()`，下游用MockWebServer）

**Interfaces:**
- Produces：`SecurityWebFilterChain gatewaySecurityFilterChain(ServerHttpSecurity http, ReactiveClientRegistrationRepository clients)`：`oauth2Login`（PKCE）、`oauth2ResourceServer`（Bearer）、CSRF（`CookieServerCsrfTokenRepository.withHttpOnlyFalse()`、SPA请求处理器）、`/logout`用`OidcClientInitiatedServerLogoutSuccessHandler`（回`http://localhost:8000/`）、放行登录与回调路径与`/actuator/health/**`。

- [ ] **Step 1: 写失败测试**
  - `sessionRequestRelaysAccessToken`：`mockOidcLogin()`带访问令牌A访问业务路由时，下游收到`Authorization: Bearer A`，且不带`Cookie`头。
  - `bearerRequestPassesThrough`：带Bearer B（`mockJwt()`）时下游收到B。
  - `unauthenticatedApiIs401Json`、`unauthenticatedBrowserNavigationRedirectsToLogin`。
  - `postWithoutCsrfIsForbidden`（403 JSON）、`postWithCsrfHeaderSucceeds`、`swaggerUiSendsCsrfHeader`（springdoc配置生效）。
  - `logoutRedirectsToKeycloakEndSession`（带`id_token_hint`、`post_logout_redirect_uri`）。
  - `sessionCookieAttributes`：`__Host-SESSION`为Secure、HttpOnly、`SameSite=Lax`、Path `/`。
  - `concurrentRequestsAtExpiryAllSucceed`：访问令牌过期时5个并发请求都成功，令牌端点的刷新请求不导致其中任一失败。
  - `localCanaryIngressStillGrants`：原有本机灰度入口测试不变。
- [ ] **Step 2:** 运行。Expected：失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** `./gradlew test`、`check-secrets`。Expected：全过。
- [ ] **Step 5:** 提交`feat(gateway): BFF登录、令牌转发与CSRF`，交付并读回CI。

### Task 15: admin-web——会话登录

**Files:**
- Modify: `admin-web/.umirc.ts`（开发代理：网关的业务路由前缀以`gateway-spring.yaml`的`Path`断言为准，加`/oauth2`、`/login`、`/logout`，`changeOrigin: false`；删登录页、OAuth客户端与第三方客户端页面的路由）、`.env.*`（`UMI_APP_BACKEND_BASE_URL`改为空）
- Modify: `src/app.ts`（`baseURL`相对；响应拦截器401时`window.location.assign('/oauth2/authorization/keycloak')`）、`src/utils/requestAuth.ts`（不再加`Authorization`；从Cookie `XSRF-TOKEN`读值加`X-XSRF-TOKEN`；`credentials: 'include'`）、`src/utils/sessionBootstrap.ts`（删令牌恢复，改为请求`/auth/current/resources`判断登录态）、`src/services/auth/index.ts`（删`/auth/sessions*`）、`components/LayoutRightContent`（退出走`POST /logout`）
- Delete: `src/pages/Login`、OAuth客户端与第三方客户端管理页面与服务
- Test: `admin-web/tests/unit/request-auth.test.mjs`、新增`session-login.test.mjs`

- [ ] **Step 1: 写失败测试**
  - `requestsCarryNoAuthorizationHeader`、`writeRequestsCarryXsrfHeaderFromCookie`、`unauthorizedRedirectsToGatewayLogin`、`logoutPostsToLogoutWithCsrf`、`noLocalStorageTokens`（`localStorage`里不再有令牌键）。
- [ ] **Step 2:** `npm run test:services`。Expected：新用例失败。
- [ ] **Step 3:** 实现与删除；`npm run build:dev`。Expected：测试全过、构建成功。
- [ ] **Step 4:** 提交`feat!: 改为网关会话登录，删除前端令牌与登录页`，交付并读回CI。

### Task 16: ai——资源服务器、审计列、令牌交换

**Files:**
- Modify: `ai/build.gradle`（utils 2.0.0、rpc-api 2.0.0）、`application-dev.yml`（不再导入`security-jwt.yaml`）
- Delete: `ai/src/main/java/com/kellen/auth/AiSessionRpcClient.java`、`agent/AiAgentRequestSigner.java`、`config/AiAgentClientAuthProperties.java`及测试
- Modify: `auth/AiRequestIdentityResolver.java`（从`FleetAuthenticationToken`取用户、租户、部门）、`auth/AiRequestIdentity.java`（加部门）、4个手动取`Authorization`的控制器、`agent/AiAgentClient.java`（接任务7的令牌交换，注册`github-ai`）
- Test: `AiAuditColumnsFromLoginUserTest.java`、`AiAgentClientTokenExchangeTest.java`

- [ ] **Step 1: 写失败测试**
  - `chatAuditRowsRecordLoginUserAndDept`：用户u1、部门d1发起对话后，`ai_chat_message`与`ai_chat_memory`新行的`create_by=u1`、`create_dept=d1`、`update_by=u1`。
  - `agentCallUsesExchangedToken`：调用ai-agent时请求头为交换后的令牌、不含任何`X-Open-*`头。
  - `tokenExchangeFailureNeverCallsAgent`：令牌端点返回400或超时时，ai返回明确错误码，ai-agent桩未收到请求。
  - `controllersNoLongerReadAuthorizationHeader`（静态检查：控制器方法没有`@RequestHeader("Authorization")`参数）。
- [ ] **Step 2:** 运行。Expected：失败。
- [ ] **Step 3:** 实现与删除；`./gradlew test`（不带/带PG env）、`check-secrets`。Expected：全过。
- [ ] **Step 4:** 提交`feat!: 统一登录身份、审计列与ai-agent令牌交换`，交付并读回CI。

### Task 17: ai-agent——只收交换令牌

**Files:**
- Modify: `ai-agent/build.gradle`（utils 2.0.0）、`agent/security/AgentRequestIdentity.java`（来自JWT）、`agent/config/AgentSecurityProperties.java`（删HMAC字段）、`agent/service/NacosAiPromptAdminClient.java`与`NacosAiPromptClient.java`（Nacos开鉴权后用账号登录取`accessToken`）
- Delete: `agent/security/AgentServiceAuthenticationFilter.java`、`AgentSignatureVerifier.java`、`AgentSessionVerifier.java`、只为HMAC服务的`ReplayableAgentRequest.java`及测试
- Test: `AgentTokenAuthenticationTest.java`

- [ ] **Step 1: 写失败测试**：`noTokenIs401`；`tokenWithAudienceGithubApiIs401`（只收`github-ai-agent`）；`tokenFromWrongAzpIs403`；`exchangedTokenYieldsUserIdentity`（`AgentRequestIdentity`的用户、租户来自令牌与业务上下文）；`hmacHeadersAreIgnored`。Expected：失败。
- [ ] **Step 2:** 实现与删除；`./gradlew test`、`check-secrets`。Expected：全过。
- [ ] **Step 3:** 提交`feat!: 只接受ai的交换令牌，删除HMAC服务签名`，交付并读回CI。

### Task 18: rag与RAG引擎——客户端凭证；Prometheus抓取改OAuth2

**Files:**
- Modify: `rag/build.gradle`（utils 2.0.0）、`rag/src/main/java/com/kellen/rag/client/RagEngineClient.java`（用任务7的`interceptor("github-rag")`，删`api-key`）、对应属性类
- Create: `rag/engine/src/rag_engine/auth.py`（`TokenVerifier`：PyJWT `PyJWKClient`，只收RS256，校验`iss`、`aud=github-rag-engine`、`exp`、`azp`）；Modify: `app.py`（中间件改用`TokenVerifier`：`/v1/**`要求`azp=github-rag`，`/metrics`要求`azp=github-prometheus`）、设置（删`api_key`，加`keycloak_issuer`、`jwks_url`）、`pyproject`（`PyJWT[crypto]`）
- Modify: `utils/ops/github-dev/observability/render.py`与`test_render.py`（引擎抓取改为Prometheus `oauth2`：`client_id: github-prometheus`、`client_secret_file`、`token_url`）
- Test: `rag/engine/tests/test_auth.py`、`RagEngineClientAuthTest.java`

- [ ] **Step 1: 写失败测试**
  - 引擎：`valid_rag_token_allowed`、`wrong_audience_rejected`、`wrong_azp_rejected`、`expired_rejected`、`hs256_and_none_rejected`、`prometheus_token_only_for_metrics`、`jwks_unreachable_returns_401_not_500`。
  - rag：`engineCallCarriesClientCredentialsToken`。
  - observability：`engine_job_uses_oauth2_client_credentials`。
- [ ] **Step 2:** 运行。Expected：失败。
- [ ] **Step 3:** 实现；引擎全量pytest、ruff，Java全量测试，`check-secrets`。Expected：全过。
- [ ] **Step 4:** 提交（rag与utils分别提交），推送后等GHCR出引擎镜像；NAS按摘要换版（改前备份编排），Prometheus配置按`render.py`重渲染。Expected：引擎health 200；Prometheus目标`up`；旧共享密钥从编排与私有env删除。

### Task 19: mcp——资源服务器与受保护资源元数据

**Files:**
- Modify: `mcp/build.gradle`（utils 2.0.0）；`mcp/security/McpVerifiedCaller.java`（来自JWT）
- Delete: `mcp/config/McpOAuthResourceServerFilter.java`、`McpOAuthResourceServerProperties.java`、`McpOAuthFilterConfig.java`、`mcp/controller/McpOAuthMetadataController.java`及测试
- Create: `mcp/src/main/java/com/kellen/mcp/config/McpResourceServerConfig.java`（按Spring Security 7受保护资源元数据配置声明授权服务器为Keycloak签发方）
- Test: `McpProtectedResourceMetadataTest.java`

- [ ] **Step 1: 写失败测试**：`metadataListsKeycloakAsAuthorizationServer`（`/.well-known/oauth-protected-resource`的`authorization_servers`为签发方、`resource`为mcp地址）；`unauthenticatedResponseHasResourceMetadataInWwwAuthenticate`；`tokenWithAudienceGithubMcpAccepted`；`tokenWithAudienceGithubApiRejected`。Expected：失败。
- [ ] **Step 2:** 实现与删除；`./gradlew test`、`check-secrets`。Expected：全过。
- [ ] **Step 3:** 提交`feat!: 标准资源服务器与RFC 9728元数据`，交付并读回CI。

### Task 20: ai-memory、message、report升级

- [ ] **Step 1:** 各升utils 2.0.0、rpc-api 2.0.0，`application-dev.yml`不再导入`security-jwt.yaml`；删除对已删类的引用。Expected：编译通过。
- [ ] **Step 2:** 每个仓库加`ResourceServerWiringTest`：无令牌访问业务接口401、有效令牌200。全量测试（不带/带PG env）、`check-secrets`。Expected：全过。
- [ ] **Step 3:** 各自提交`feat!: 接入Keycloak资源服务器`，交付并读回CI。

### Task 21: Nacos客户端鉴权

**Files:**
- Modify（NAS）：编排`NACOS_AUTH_ENABLE=true`（改前备份，令牌密钥满足至少32字节）；Seata服务端配置里的Nacos注册与配置账号
- Modify: `utils/ops/github-dev/scripts/*`与`kellen-workspace/scripts/observability_runtime.py`改用`GITHUB_NACOS_ADMIN_*`；`dev.env`（服务账号进`GITHUB_NACOS_USERNAME/PASSWORD`，管理员进`GITHUB_NACOS_ADMIN_*`）
- Test: 运维单元测试（脚本读管理员变量）

- [ ] **Step 1:** 经Nacos管理API建用户`github-dev-app`、角色`github-dev-app-role`，对`github-dev`命名空间授权读写；管理员口令不变。Expected：读回1用户、1角色、1条授权。
- [ ] **Step 2:** 改脚本与dev.env、Seata配置；开鉴权并重启orb-nacos与orb-seata。Expected：未带账号的Open API请求返回401/403；管理员脚本盘点21个dataId成功；Seata在Nacos注册。
- [ ] **Step 3:** 提交脚本改动，交付并读回CI。

### Task 22: 账号迁移与整体切换验证

**Files:**
- Create: `~/.config/kellen-github/tools/kc_migrate_accounts.py`（按user库建Keycloak账号、回填`idp_subject`；临时密码写`accounts-temporary-passwords.txt`；链路测试账号口令按`run_chain_verify.py`原规则在子进程里设置；不打印口令）
- Modify: `~/.config/kellen-github/tools/chain-verify.py`、`run_chain_verify.py`（用`venv-playwright`在Chromium里走BFF登录；新增步骤见下）

- [ ] **Step 1:** 迁移前备份user库；运行迁移脚本。Expected：Keycloak里7个账号、`idp_subject`7个非空、私有文件0600。
- [ ] **Step 2:** 请用户在IDEA里Reload All Gradle Projects，重启9个服务。Expected：9个进程工作目录为各自仓库、health UP；Nacos里9个服务注册。
- [ ] **Step 3:** 两条全链路（带/不带灰度授权），新增步骤：BFF登录、`create_by`/`create_dept`核对（含ai两表）、ai调ai-agent（交换令牌`aud`与`azp`）、rag调引擎、mcp元数据、绕过网关直连每个服务端口无令牌401、用户禁用后30秒内被拒、退出后会话失效、建账号欢迎提醒（第4阶段步骤保留）。Expected：最后一行`phase 5 chain verification OK（带灰度授权）`与`（不带灰度授权）`。
- [ ] **Step 4:** NAS按判据复核内存与PostgreSQL连接数。Expected：可用≥3500MB、无持续换出、连接数≤60。

### Task 23: 收尾

- [ ] **Step 1:** Nacos删旧键与旧dataId：`security-jwt.yaml`退役、`security-auth.yaml`里`jwt-enabled`、`header-enabled`等旧键、rag的`api-key`；保值渲染、逐键比对（期望删除清单）、CAS发布读回。Expected：删除恰好等于清单。
- [ ] **Step 2:** 各仓库AGENTS、README、规范里自签令牌、jjwt、HMAC签名、共享密钥的说明改为新做法；检索关口：`rg -n -i --hidden 'jjwt|io\.jsonwebtoken|JwtUtils|X-Open-Signature|AgentSignatureVerifier|AiSessionRpcClient|RAG_ENGINE_API_KEY|security-jwt\.yaml' <13个仓库> --glob '!**/build/**' --glob '!docs/superpowers/**'`只剩退役记载。
- [ ] **Step 3:** 规格逐节对证据；按执行方式做最终复查；汇总全部`Ruling:`与推迟的小问题；清理本计划的短分支；`phase3-backup`、RabbitMQ备份与本阶段备份按用户决定处理。
- [ ] **Step 4:** 用`llm-wiki-archive`归档（台账全文进证据页附录），以“已提交推送 <hash>”为准；台账目录在归档后由用户在终端删除。
