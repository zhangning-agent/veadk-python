# Managed Agents 云 PostgreSQL 迁移

2026-09-12，`managed-agents-demo` 从集群内 PostgreSQL 16 迁移至北京区域
`zn_test`（`postgres-ba6eaa3d42f0`，PostgreSQL 17）。MA Server、Task Server
和 Agent Loop 共用 `veadk` 数据库；Agent Loop 的会话 schema 为 `managed_agents`。

## 连接与白名单

- 内网地址：`postgresba6eaa3d42f0.rds-pg.ivolces.com:5432`。
- VPC：`vpc-iinc4dw5vta874o8ctv72kvx`，与 VKE `zn-test` 相同。
- 白名单：`managed-agents-vke`，覆盖 VKE 配置中的 Pod 子网
  `172.31.48.0/20` 和 `172.31.64.0/24`。扩展或迁移 Pod 子网时同步检查白名单。
- 连接主机、端口、用户名和数据库名位于 `managed-agents-config`；密码及两种
  服务端连接串位于 `managed-agents-postgres-auth` Secret，实际值不提交到仓库。
- 本地前端通过 `MANAGED_AGENTS_API_TARGET` 访问 VKE 网关，数据库访问发生在
  VKE 内。本地完整后端若直接连接 RDS，需要另行具备内网路由和对应白名单。

## 普通账号的 public schema 权限

本次通过 API 创建 `veadk` 普通账号和以该账号为 Owner 的数据库后，恢复数据仍报：

```text
ERROR: permission denied for schema public
```

数据库 Owner 不代表本实例模板已授予 `public` schema 的建表权限。使用管理账号
连接目标 `veadk` 数据库，显式授予应用账号权限后，再用普通账号执行恢复：

```sql
GRANT USAGE, CREATE ON SCHEMA public TO veadk;
```

应用继续使用普通账号；管理账号凭据只保存在本地受限迁移目录。

## 迁移与验证顺序

1. 等待实例及主备节点均为 `Running`，绑定白名单，创建应用账号和数据库，
   从 VKE 内执行真实 SQL 连接验证。
2. 保存 ConfigMap、Secret、Deployment 和旧数据库备份。
3. 将 Gateway、MA Server、Task Server 和 Agent Loop 缩容至零，确认旧数据库
   已无应用连接，再生成最终 `pg_dump -Fc --no-owner --no-acl` 备份。
4. 使用 `pg_restore --no-owner --no-acl --exit-on-error --single-transaction`
   恢复到空目标库。失败时修复明确的原因后重试，避免部分恢复。
5. 比较所有业务表的精确行数及按行内容排序后的校验和。汇总结果也按表名排序；
   不依赖 `UNION ALL` 返回顺序。本次 62 张表、1,437 行数据全部匹配。
6. 更新数据库 ConfigMap 和 Secret，恢复各 Deployment 原有副本数，验证历史
   会话读取、新会话写入和多轮记忆恢复。确认新事件及 ADK 会话写入 RDS。
7. 验证完成后删除旧 PostgreSQL StatefulSet、Service、专用 NetworkPolicy 和
   PVC，并清理已备份的本地 PostgreSQL 验证容器。部署清单不再引用旧数据库。

本次备份保存在本机 `~/.local/state/managed-agents/rds-migration-20260912/`，
包括最终数据库 dump、配置快照、校验和与本地验证库备份。目录及文件限制为当前
用户访问，不能直接提交或分享整个目录。

## 工具排查

- Dispatcher 和工具沙箱只通过 Managed Agents HTTP API 工作，不配置 PG 连接；
  PG 凭据仅供 MA Server、Task Server 和 Agent Loop 使用。
- OMA 测试 Runtime 为 `r-yetovao0sgqgapsacusi`，配套 Tool 为
  `t-yetov9c9hce5g253o7oj`。更新 `ANTHROPIC_BASE_URL`、
  `ANTHROPIC_ENVIRONMENT_ID` 和 `ANTHROPIC_ENVIRONMENT_KEY` 时一并核对
  Worker ID、协议模式和鉴权头，并保存原配置快照。
- 前端、MA Server、Agent Loop 和 Dispatcher 统一使用 VKE Task Server。
  Runtime 的 `ANTHROPIC_BASE_URL` 为
  `http://skv8hsls9otpqehqgo61o.apigateway-cn-beijing.volceapi.com/task-server`，
  网关直接转发到 `http://task-server:8787`，不经过 ma-server 的 SDK 序列化。
  配套环境密钥通过私有配置更新，不写入文档；环境 ID 保持
  `env_6eccab16cdf84db392152f9c595d63cb`。
- 不能仅凭环境 ID 相同就混用不同 Task Server：不同入口可能使用不同会话和
  Work 队列。切换入口前，先验证能读取现有 Session，再测试工具执行及最终回复。
- Task Server 新建 Session 在首次更新前 `updated_at` 可能为 null；Go Worker
  的兼容层仅在该字段缺失时使用 `created_at`，避免启动时报
  `invalid: updated_at (field required)`。
- Gateway 兼容 `Ark-Worker-ID` 和 `Anthropic-Worker-ID`；缺少 Authorization
  的外部 Work/Task Server 请求返回 401。
- Agent Loop 通过集群内 `http://ma-server:8000` 消费本地模型任务；
  Dispatcher 通过 Task Server 消费工具任务，两者不能竞争同一 Work 队列。
- `agent-loop-agentkit.yaml` 为测试环境部署独立 Agent Loop，设置
  `MANAGED_AGENT_TOOL_EXECUTION=remote`：只发布工具请求并等待匹配的
  `user.tool_result` 或 `agent.tool_result`，不在 Agent Loop Pod 内重复执行命令。
  普通部署默认仍为 local。
- 常驻轮询的 Dispatcher 需要保留运行实例；本测试 Runtime 设置 `MinInstance=1`。

- 默认 Python 与工具安装使用的解释器可能不同。本机 Playwright 位于
  `/usr/bin/python3` 环境；MA Server 的数据库驱动位于 `/app/.venv/bin/python`。
  遇到缺少模块时先检查实际运行环境。
- 控制台浏览器访问失败时，可使用已授权的云 API 查询实例状态；这不代表实例
  故障。控制台会话过期时按仓库约定优先使用本地 `LOGIN` 书签登录。
- 本次 API 验证中，`CreateDatabase.CharacterSetName` 使用小写 `utf8`；
  48 字符密码被拒绝，改用包含大小写、数字和特殊字符的 24 字符随机密码后成功。
  部分写 API 成功返回 `null`，调用端需兼容空返回值，并通过查询确认结果。

## 常驻 Agent Loop

测试环境的 Agent Loop 不设置 `--max-work-items 1`。该参数使进程每处理一个
任务就退出，在 Deployment 的重启策略下会累积重启退避，导致下一轮消息长时间
排队。常驻 Worker 应持续轮询；单任务退出仅用于独立验收场景。
