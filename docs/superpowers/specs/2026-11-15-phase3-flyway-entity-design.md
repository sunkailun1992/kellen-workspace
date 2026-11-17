# 第3阶段 Flyway 与实体基类规范化设计

## 已确认目标

用户2026-11-13定：个人GitHub微服务按6个阶段升级（底座升级、utils改造、Flyway、RocketMQ、Keycloak、前端），兼容性优先，每一步验证通过再进入下一步。第1、2阶段已完成；本文覆盖第3阶段。

第2阶段规格「不在本阶段」推到本阶段的四项：ai存储表时区类型；`EntityBase`字段与列改名（`create_time`、`update_time`、`creator`、`updater`，逻辑删除字段`deleted`、列`is_deleted`）与自动填充创建人、修改人，`code`、`description`、`label`、`sorting`是否移出基类、`sorting`是否改名届时由用户定，创建人存用户ID，ID来源随第5阶段Keycloak再考虑；数据源改用dynamic-datasource原生配置（含Seata代理）；DDL改Flyway。

用户2026-11-15确认（新加坡时间）：

- 18:14「按规范处理吧，以主流规范和github关注度最高的一些规范项目来修改删除。」：基类与相关字段按主流规范项目修改和删除。
- 18:16「初始化表采用Flyway了。这个是你的，所有sql初始化都迁移到这个。之前的模式删除吧」：所有建表与初始化SQL迁到Flyway，具体做法由AI定；MyBatis-Plus DDL方式整套删除。
- 18:25 选「写设计文档」：按当时列出的方案写本文。

范围只有个人GitHub项目，美联项目不动。

为什么现在做：第4、5阶段（RocketMQ、Keycloak）都要改表。现在的建表靠MyBatis-Plus DDL加utils自写执行器，新库要先手工执行公共脚本，也不校验已执行的脚本是否被改过；基类让每张表都带着4个大多没人用的通用列，审计字段命名与主流不一致，创建人、修改人从不填写。

## 修订记录

- 2026-11-16 22:00–22:15（用户决定）：实体基类恢复旧字段（`id`、`code`、`description`、`label`、`sorting`、`version`），迁移不删任何列（已提交但没在任何真实库执行过的V4删列迁移撤回）；每行自动记录创建部门，按部门过滤仍按表开启；Seata固定开启、不设总开关；只删OSS专用代码；清理Nacos。
- 2026-11-16 23:31（用户）：「这些基础字段，每次创建表都必须存在的」——每张表都必须包含全部基础字段，不得省略。
- 2026-11-16 23:39、2026-11-17 10:03（用户）：参考主流项目以多数为准，不只参考芋道一家；2026-11-17 09:51 定审计列改为多数项目的`create_by`、`update_by`。
- 下文已按以上决定改写；原「字段集合按芋道」「基类只留审计字段」「V4删列」等初版做法见Git历史。

## 判定口径

- 主流规范的取法：对照多个主流项目（RuoYi-Vue、RuoYi-Vue-Plus、JeecgBoot、芋道ruoyi-vue-pro、MyBatis-Plus官方文档），以多数做法为准，不以单个项目为准；列与字段命名按阿里巴巴Java开发手册（布尔概念的列用`is_`前缀，对象字段不带`is`）；迁移做法按Flyway官方文档与Spring Boot的Flyway集成；数据源与Seata按dynamic-datasource官方集成方式；Java时间类型与SQL类型按JDBC 4.2映射。
- 冲突时：以多数主流项目为准；多数之间仍不一致时按阿里手册；与admin-web正在使用的接口字段冲突时兼容优先，接口字段留到第6阶段与前端一起改。
- 「没用」：服务代码、手写SQL、HTTP接口、rpc-api与admin-web都不读写，且库里没有数据（只读计数核对）。有数据的不删，停下报告用户。

## 依据

- 主流项目的实体基类（2026-11-17读源码核对）：[RuoYi-Vue `BaseEntity`](https://github.com/yangzongzhuan/RuoYi-Vue/blob/master/ruoyi-common/src/main/java/com/ruoyi/common/core/domain/BaseEntity.java)为`createBy`、`createTime`、`updateBy`、`updateTime`、`remark`；[RuoYi-Vue-Plus `BaseEntity`](https://github.com/dromara/RuoYi-Vue-Plus/blob/5.X/ruoyi-common/ruoyi-common-mybatis/src/main/java/org/dromara/common/mybatis/core/domain/BaseEntity.java)为`createDept`、`createBy`、`createTime`、`updateBy`、`updateTime`（租户在`TenantEntity`）；[JeecgBoot `JeecgEntity`](https://github.com/jeecgboot/JeecgBoot/blob/main/jeecg-boot/jeecg-boot-base-core/src/main/java/org/jeecg/common/system/base/entity/JeecgEntity.java)为`id`、`createBy`、`createTime`、`updateBy`、`updateTime`；[芋道 `BaseDO`](https://github.com/YunaiV/ruoyi-vue-pro/blob/master/yudao-framework/yudao-spring-boot-starter-mybatis/src/main/java/cn/iocoder/yudao/framework/mybatis/core/dataobject/BaseDO.java)为`createTime`、`updateTime`、`creator`、`updater`、`deleted`。创建人、更新人多数用`createBy`、`updateBy`，只有芋道用`creator`、`updater`；逻辑删除各家不一（若依系与JeecgBoot用`del_flag`，芋道与MyBatis-Plus示例用`deleted`），按阿里手册用布尔列`is_deleted`配字段`deleted`。
- [阿里巴巴Java开发手册（p3c）](https://github.com/alibaba/p3c)：表达是与否概念的列命名为`is_xxx`，POJO布尔字段不加`is`前缀；表必备`id`、`create_time`、`update_time`。
- [Flyway支持的数据库与版本](https://documentation.red-gate.com/flyway/getting-started-with-flyway/system-requirements/supported-databases-and-versions)：兼容表列到PostgreSQL 18；[发布说明](https://documentation.red-gate.com/fd/release-notes-for-flyway-engine-179732572.html)未单列18，须实测。
- Spring Boot 4.0.9依赖管理（本机Gradle缓存核对）：Flyway 11.14.1，模块`spring-boot-starter-flyway`与`flyway-database-postgresql`。
- [dynamic-datasource文档](https://doc.iocoder.cn/dynamic-datasource)与[Seata AT模式](https://seata.apache.org/docs/user/mode/at)：开启dynamic-datasource的Seata集成后，要关闭Seata自带的数据源自动代理，避免重复代理；每个参与全局事务的库都要有`undo_log`。
- JDBC 4.2：`LocalDateTime`对应`TIMESTAMP`，`OffsetDateTime`对应`TIMESTAMP WITH TIME ZONE`。
- MyBatis-Plus：自动填充（`MetaObjectHandler`）、逻辑删除（`@TableLogic`）、乐观锁插件（`@Version`）。

## 方案比较

1. **Flyway怎么运行**
   - 采用：服务启动时由Spring Boot自动迁移。Flyway用自己的直连（`spring.flyway.url/user/password`），不经过Seata代理与主/灰度路由。从IDEA启动的方式不变；Flyway自带的库级锁保证主实例与灰度实例同时启动时只执行一次。
   - 未采用：部署前用命令行或Gradle单独执行。能让应用账号不持有改表权限，但本机每次启动都多一步，两套入口容易漏执行。
2. **现有库怎么接管**
   - 采用：baseline。V1是每个服务现在的表结构快照，只在空库上执行；现有库在首次迁移时登记为「V1已执行」，从V2开始执行本阶段的改动。改列名用`ALTER TABLE ... RENAME COLUMN`，不搬数据。
   - 未采用：重建库再导回数据。要停写、搬数据，出错面大。
   - 未采用：把现有结构拆成多个历史版本。没有真实的历史可拆，只会多出文件。
3. **迁移归谁**
   - 采用：每个服务自带完整的迁移链，包括Seata的`undo_log`；utils不放任何迁移SQL。
   - 未采用：utils提供公共迁移。版本号要跨仓库协调，utils升级就会改各服务的库结构。
4. **基类**
   - 采用：两层：公共字段基类加租户子类（RuoYi-Vue-Plus的`BaseEntity`/`TenantEntity`、芋道的`BaseDO`/`TenantBaseDO`都是两层）。旧基类的公共字段全部保留在基类（`id`、`code`、`description`、`label`、`sorting`、`version`），审计列按多数命名`create_time`、`create_by`、`update_time`、`update_by`，租户子类另加`tenant_id`与创建部门`create_dept`（对照RuoYi-Vue-Plus的`createDept`）。每张表都必须有全部基础字段。
   - 未采用：基类只留审计与逻辑删除字段、其余由实体自己声明（初版）。用户要求旧字段全部保留、每张表都必须齐全。
5. **时间类型**
   - 采用：审计时间统一为`timestamp`配`LocalDateTime`（39张表里36张已经如此，符合JDBC 4.2映射，若依系、芋道等主流项目的审计时间也都不带时区）；ai存储台记录真实时刻的列（操作开始、结束、凭据更新）保留`timestamptz`配`Instant`。
   - 未采用：全部改为`timestamptz`配`OffsetDateTime`（PostgreSQL社区更推荐）。要改所有实体和JSON格式，而全系统只用+8一个时区，收益小；以后需要多时区再统一。
6. **数据源**
   - 采用：dynamic-datasource原生配置`spring.datasource.dynamic.*`，开启它的Seata集成（AT），关闭Seata自带的自动代理。
   - 未采用：保留utils自写的数据源配置。与主流做法不一致，连接池上限校验与超时换算都要自己维护。

## 现状（2026-11-15核对）

- **建表方式**：MyBatis-Plus DDL（`IDdl`、`ddl_history`）加utils的`PostgresqlDdlApplicationRunner`（库级咨询锁、单事务、脚本规则校验`PostgresqlDdlScriptPolicy`，由`PostgresqlDdlAutoConfiguration`装配）。ai、ai-memory、message、user、mcp、rag、report各有一个`PostgresqlDdl`登记要执行的脚本。新库要先手工执行utils的`db/postgresql/common-infra-schema.sql`（`ddl_history`与`undo_log`）。
- **表**：业务表39张：ai 18、ai-memory 7、message 1、user 13；每个库另有`ddl_history`与`undo_log`。rag、mcp、report只有这两张公共表。ai存储三表在`ai-schema.sql`与`ai-storage-console-20261111.sql`里各声明一次，两处定义一致。user另有`user-bootstrap.sql`，含14条默认租户、角色、权限（`ON CONFLICT DO NOTHING`）。
- **历史MySQL脚本**：ai 2个、ai-memory 1个、message 1个、rag 2个、user 5个、utils 1个，迁到PostgreSQL后不再执行。
- **基类**：`EntityBase`有`id`、`code`、`description`、`createDateTime`、`createName`、`modifyDateTime`、`modifyName`、`isDelete`、`label`、`sorting`、`version`、`tenantId`。user的13个实体经`AuthEntity`（另含`type`、`state`）继承，ai-memory 7个、message 1个直接继承；ai的7个实体继承自己的`AiGovernanceEntity`（只有`modifyDateTime`），其余字段各自声明；ai存储三表由JDBC仓库访问，时间字段用`Instant`。没有配置`tenant.ignore-tables`，所有映射表都按租户隔离。
- **字段使用**：`label`没有任何读取；`sorting`用于user的租户、角色、部门、用户、资源的查询与默认排序，admin-web的7个类型定义与资源页在用；`code`在admin-web的user接口类型中13处；`version`在user与message的更新接口里由admin-web回传；`description`与审计字段admin-web不用；rpc-api不含这些字段。
- **自动填充**：utils的`MyMetaObjectHandler`只填创建时间、修改时间与租户ID，创建人、修改人从不填。
- **时间类型**：ai存储三表的审计列是`timestamptz`，其余36张是`timestamp`。
- **未被引用的ai表8张**：`ai_chat_conversation`、`ai_chat_conversation_message`、`ai_integration_outbox`、`ai_questionnaire_answer`、`ai_questionnaire_session`、`ai_questionnaire_template`、`ai_wechat_session`、`ai_wechat_user`，ai代码里没有任何读写。
- **数据源**：utils的`DynamicDataSourceConfig`自建Druid的master与gray（读`postgresql.*`、`postgresql-gray.*`），校验连接池上限（master≤4、gray≤2、合计≤6），把URL里的`connectTimeout`、`socketTimeout`换算成Druid超时（默认连接3秒、读30秒），再手工包上Seata的`DataSourceProxy`交给`DynamicRoutingDataSource`。github-dev的`seata.enabled`为false。
- **运维工具**：`utils/ops/github-dev/scripts/prepare_databases.py`是MySQL迁PostgreSQL时生成建表计划的工具；`verify_configuration.py`用它核对每个实体的表都有建表语句。
- **测试**：各服务没有在真实PostgreSQL上跑迁移的测试。

## 改动设计

### utils（1.6.8 → 1.6.9，一次发版）

1. **删除旧建表方式**：`PostgresqlDdlApplicationRunner`、`PostgresqlDdlAutoConfiguration`、`PostgresqlDdlScriptPolicy`及其测试与自动配置登记；`db/postgresql/common-infra-schema.sql`与`db/common-infra-schema.sql`。
2. **引入Flyway**：以`api`依赖`spring-boot-starter-flyway`与`flyway-database-postgresql`，版本由Spring Boot依赖管理决定。utils不放迁移SQL。
3. **基类两层**（类名沿用`EntityBase`，新增`TenantEntityBase`）：
   - `EntityBase`（抽象）：`id`（字符串雪花ID）、`code`、`description`、`createTime`（插入时填）、`createBy`（插入时填）、`updateTime`（插入与更新时填）、`updateBy`（插入与更新时填）、`deleted`（`@TableLogic`，列`is_deleted`）、`label`、`sorting`、`version`（乐观锁）。日期的JSON格式与注解沿用现有写法（`yyyy-MM-dd HH:mm:ss`，GMT+8）。
   - `TenantEntityBase extends EntityBase`：`tenantId`（插入时填）、`createDept`（插入时取登录用户部门）。实体统一继承`TenantEntityBase`，每张表都必须有全部基础字段。
4. **自动填充**：`MyMetaObjectHandler`在插入时填`createTime`、`updateTime`、`createBy`、`updateBy`、`tenantId`、`createDept`，更新时填`updateTime`、`updateBy`。创建人、修改人取当前登录用户ID；没有登录用户（种子、后台任务）时不填，与RuoYi-Vue-Plus、芋道一致。
5. **数据权限**：默认用户列`create_by`、默认部门列`create_dept`；过滤按表在`security.data-permission.table-rules`开启（github-dev的三张表规则写明了各自的列）。
6. **数据源**：删除`DynamicDataSourceConfig`，改用dynamic-datasource原生配置：`primary`为master，`datasource.master`与`datasource.gray`，连接池类型Druid，`seata: true`（固定开启、不设总开关）、`seata-mode: AT`；utils的`EarlyFlywayMigration`在创建任何Bean之前先按`spring.flyway.*`迁移，新空库开着Seata也能首次启动。连接池上限、超时与健康检查参数改由配置声明并保持现值（master 4、gray 2、连接3秒、读30秒）。灰度切库沿用`DynamicSourceTtl`，数据源名master、gray不变。
7. **文档**：README、`UTILS_TOOL_CATALOG.md`、`PROJECT_CODING_SPEC.md`（公共治理字段改为新基类）、`NACOS_CONFIG_SPEC.md`、`AGENTS.md`同步；删去「空库首次启动前手工执行`common-infra-schema.sql`」「修改DDL前确认`ddl_history`」等旧规则，改为Flyway规则：已发布的迁移文件不再修改，改结构只新增版本。

### 服务（ai、ai-memory、message、user、mcp、rag、report，每个服务一个提交）

1. **迁移文件**放`src/main/resources/db/migration`：
   - `V1__baseline.sql`：现有结构快照，按现在的执行顺序合并现有脚本（ai存储三表只取一份定义），包含`undo_log`，不包含`ddl_history`；user的V1包含现有种子。只在空库上执行。
   - `V2__drop_ddl_history.sql`：删除`ddl_history`（不存在时跳过）。
   - `V3__rename_audit_columns.sql`：所有业务表`create_date_time`→`create_time`、`modify_date_time`→`update_time`、`create_name`→`creator`、`modify_name`→`updater`、`is_delete`→`is_deleted`（已提交，不再修改）。
   - `V4__add_create_dept.sql`：所有业务表加`create_dept`（不删任何列）；`V5__rename_create_by_update_by.sql`：`creator`→`create_by`、`updater`→`update_by`。
   - ai：`V4`删8张未被引用的空表并把存储三表审计列由`timestamptz`改为`timestamp`（按Asia/Shanghai换算），`V5`给7张业务表、`V6`给存储三表加`create_dept`，`V7`改名`create_by`、`update_by`。
   - rag、mcp、report：V1只有`undo_log`，V2删除`ddl_history`。
   - Flyway配置放各服务`application.yml`（不分环境）：`locations: classpath:db/migration`、`baseline-on-migrate: true`、`baseline-version: 1`、`validate-on-migrate: true`、`clean-disabled: true`；连接引用master数据源的同一组URL、账号、密码。
2. **删除**：各服务的`PostgresqlDdl`、`db/postgresql/`下的脚本、`db/`下的历史MySQL脚本。
3. **实体**：
   - user：`AuthEntity`改为继承`TenantEntityBase`，保留`type`、`state`；13个实体声明`id`、`version`与在用的`code`、`description`、`sorting`。
   - ai-memory 7个、message 1个：改继承`TenantEntityBase`，按需声明同上字段。
   - ai：7个实体逐个核对删除路径后再定继承。现在已用手写条件做逻辑删除的，改继承`TenantEntityBase`；有物理删除语义的（例如隐私删除、过期清理）保持物理删除，不继承带`@TableLogic`的基类，避免`deleteById`悄悄从删除变成标记。`AiGovernanceEntity`的`modifyDateTime`改名`updateTime`；所有实体都不再需要它时删除该类。
   - 手写SQL、Lambda引用与排序白名单里的旧字段名、旧列名随之替换。
4. **接口**：VO里的审计字段随实体改名（admin-web与rpc-api不使用）；`sorting`、`code`、`version`、`tenantId`等admin-web在用的字段名本阶段不变，`sorting`→`sort`放到第6阶段与前端一起改。
5. **测试**：每个有库的服务加真实PostgreSQL迁移测试（Testcontainers，PostgreSQL 18）。
   - 空库从V1迁到最新成功，最终结构里新列在、旧列不在。
   - 关键实体插入后`createTime`、`updateTime`、`creator`、`updater`、`tenantId`按规则填写，逻辑删除生效。
   - CI照跑这些测试。
6. **版本**：各服务补丁位递增；CI里固定的utils检出提交改为1.6.9的发布提交。

### Nacos（github-dev）

- 7个`{service}-spring.yaml`：`postgresql.*`、`postgresql-gray.*`改为`spring.datasource.dynamic.datasource.master/gray.*`（含Druid连接池参数），逐行注释。先新旧键并存发布，所有服务升级并验证后再删除旧键。
- `seata.yaml`：增加`seata.enable-auto-data-source-proxy: false`。
- 发布沿用第2阶段的工具与步骤：只读盘点并备份、保值渲染（ai-agent、rag仍按远端值）、逐键比对、CAS发布、读回、同步描述。

### 运维工具（utils/ops）

- `prepare_databases.py`生成建表计划的功能删除。
- `verify_configuration.py`核对「实体的表都有建表语句」改为读取各服务的`db/migration`（创建减去删除）。
- README同步：新库只需由管理流程建库与角色，表由服务首次启动时的Flyway建立。新库必须保持空库，不能再手工执行任何公共脚本；否则Flyway会把它当作已有库登记为V1，跳过建表。

## 验证

1. **先实测，不碰现有库**：在NAS的PostgreSQL 18上建临时库。
   - 用Flyway 11.14.1从空库迁到最新。
   - 把一个现有库的结构与数据复制到另一个临时库，用baseline接管后迁到最新。
   - 两者最终结构逐表逐列一致：类型、默认值、可空、约束、索引。验证后删除临时库。
2. **删列、删表前**：只读统计每个待删列的非空值数、每张待删表的行数，只记数量；有值就停下报告。
3. **utils**：全套测试、javadoc、依赖树（Flyway 11.14.1与PostgreSQL模块在，旧建表类不在）。
4. **服务**：本机测试（含迁移测试）通过，精确提交的CI通过。
5. **运行态**：7100等端口当前被另一个项目的Codex任务占用，执行到这一步时先请用户确认端口空出。
   - 升级前对7个库各做一次结构加数据备份，存Git外0700目录。
   - 启动9个服务：启动日志里Flyway显示baseline与各版本成功，健康检查为UP，没有缺Bean报错。
   - 整链复验沿用第2阶段的路径：登录、错误报文、管理列表、message→user、资源、RAG、AI对话、存储探针、上传。
   - 新增一条测试数据（如部门）：创建时间、修改时间、创建人、修改人、租户ID正确；删除后列表不再返回，库里`is_deleted`为true；随后清理。
   - 7个库的Flyway版本一致，旧列不在。

## 回退

- 列已改名，旧版本不能直接连新结构。回退时先用升级前的备份恢复对应库，再启动上一版服务与utils 1.6.8；升级后的新写入在dev环境按丢弃处理。
- Nacos：新旧键并存期间可直接回退；删除旧键后按备份恢复。
- Flyway社区版没有撤销迁移，本阶段不写回滚脚本，依靠备份。

## 不在本阶段

- 主键改为Long：不改，接口与RPC契约都用字符串ID（JeecgBoot也用字符串主键）。
- `sorting`→`sort`以及admin-web在用字段的改名：第6阶段与前端一起。
- 创建人来源换成Keycloak：第5阶段。
- RocketMQ：第4阶段。
- 全部时间列改为`timestamptz`：不做，原因见方案比较。
- 第2阶段已列为不做的MinIO 9等：继续不做。
- 美联项目。

## 留给实施确定的细节

- 各服务V3起的具体语句，以及每张表保留哪些通用列：按代码与接口的使用和只读计数确定，结果写进台账。
- Druid在原生配置下的键名与现值对齐方式；新旧键并存期间，旧版本服务不受新键影响（实施时启动旧版本核对）。
- 数据源健康检查在原生配置下的接法。
- Flyway与MyBatis的启动先后：确认任何数据库访问都在迁移完成之后，不满足时补依赖顺序。
- 复制现有库到临时库的具体办法（使用私有管理凭据，命令与输出不含凭据）。
- Testcontainers的复用方式与CI时长。
