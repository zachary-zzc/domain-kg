# 身份接入验收记录

验证日期：2026-10-31。此记录针对仓库实现与隔离开发环境，不表示某个外部业务平台已经获准接入。

## 已验证的行为

| 边界 | 证据 |
| --- | --- |
| 并发首次建档不重复，断言与请求 key 不可换意图重放；IdP 故障后可恢复 | `tests/integration/test_identity_bridge.py` |
| HTTP/MCP 只读连接不能借管理员角色扩权；待批准身份不能走旧 token 路径 | `tests/integration/test_identity_bridge_boundaries.py` |
| issuer/client/audience/tenant、组织与 KG 限制；固定 canonical 映射不能漂移 | 同上与 `tests/unit/test_identity_bridge_broker.py` |
| 停用接入、撤销连接、移除/修改信任配置不会在重新启用后复活旧连接 | `test_identity_bridge_boundaries.py` |
| 有序用户事件、独立平台/人工组成员支持、单个关联审批不覆盖其他关联 | `test_identity_bridge_boundaries.py` |
| 最后管理员被上游停用仍立即阻断；返回 `recovery_required`，缓存的管理员对象不可绕过 | `test_identity_bridge_boundaries.py` |
| 普通角色/授权修改不会把停用或待批准成员算作有效 admin/owner；有效的同步组 owner 仍可保留所有权 | `tests/integration/test_identity_bridge_invariants.py` |
| 已受理任务不依赖原 JWT 有效期；发布前检查原始连接；管理员重试保留原 actor | `tests/integration/test_identity_bridge_jobs.py` |
| 控制台审批、本人/管理员撤销、CSRF/Origin、组织隔离和秘密不出现在 HTML | `tests/integration/test_console_identity_bridge.py` |
| 实际 Keycloak → HTTP bootstrap/token → MCP 添加文本 → worker 发布 → 检索 → revoke | `tests/integration/test_identity_bridge_keycloak.py` |
| OpenAPI 与路由一致，示例客户端保留请求 key 且不回传原始错误秘密 | `tests/unit/test_identity_bridge_contract.py` |
| 超限 IdP 响应在下载过程中被截断，身份错误不包含原始提供商响应 | `tests/unit/test_identity_bridge_broker.py` |

存储集成测试连接真实 MongoDB replica set、Neo4j、Qdrant 与对象存储，每次使用独立合成命名空间。模型调用使用 mock；真实 IdP 测试只创建临时合成 realm/container，不修改本地浏览器 realm。

## 可重复的验证

项目 `.env` 中的开发存储必须已就绪；原生文档子进程/RSS 检查需要操作系统允许读取进程状态。开发存储测试通过文件锁串行运行，不要同时启动多个 pytest 存储进程。

```bash
.venv/bin/pytest tests/unit tests/protocol -q
.venv/bin/pytest tests/integration/test_identity_bridge.py \
  tests/integration/test_identity_bridge_boundaries.py \
  tests/integration/test_identity_bridge_invariants.py \
  tests/integration/test_identity_bridge_jobs.py \
  tests/integration/test_console_identity_bridge.py -q
.venv/bin/pytest tests/integration/test_identity_bridge_keycloak.py -q
.venv/bin/python scripts/verify_identity_exchange.py
.venv/bin/mypy src/domain_kg
.venv/bin/ruff check src/domain_kg
.venv/bin/python scripts/export_identity_openapi.py --check
uv build --offline --out-dir /tmp/domain-kg-release
.venv/bin/python scripts/check_distribution.py /tmp/domain-kg-release
```

真实 IdP 检查需要 Docker 中已缓存的固定 digest Keycloak 26.8.1 镜像。临时容器限制为 512 MiB、无额外 swap、1 CPU；测试退出时清理。不得把该测试专用 password grant 配置用于生产。

## 支持与限制

Keycloak 26.8.1 的同 realm standard token exchange V2 已实测。签名用户证明、平台机器身份、目标 MCP audience、exchange client、scopes 以及错误 JWT token 类型拒绝均通过；完整 HTTP/MCP 流程也通过。最终 broker 的有界流读取再次经真实交换脚本验证。

其他 IdP、跨 issuer brokering、实际业务平台与独立交互式 MCP OAuth 客户端需要各自联调；不能仅凭 OAuth/OIDC 名称推断兼容。功能在没有 operator 私有配置时保持关闭，不自动注册真实平台、迁移用户或授予现有语料权限。

接入端点与运行步骤见 [身份接入指南](identity-bridge.md)、[IdP 配置](identity-bridge-idp.md) 和 [OpenAPI](api/identity-bridge.openapi.json)。

## 本次结果

- 单元/协议测试：783 项通过。发布包 fixture 已随新增必需页面更新，完整重跑通过。
- 最终身份存储验收（含原有登录流程、真实 Keycloak、审批、同步组与不变量）：35 项通过。
- 另有原生 PDF 无 OCR 路径与新身份集中验收：18 项通过（与上项有重叠，不相加计算）。
- 既有身份/权限/任务/发布扩展回归：初次 70 项通过、2 项失败；失败来自管理员 fixture 缺少 admin scope、原生 PDF fixture 没有显式关闭新的默认 OCR。两处测试前提已修正，对应定向重跑通过。
- 严格 mypy：289 个源码/示例/脚本文件通过；Ruff、OpenAPI 同步、diff 空白检查通过。
- 离线 wheel/sdist 构建及内容检查通过；包中包含新身份接口与两类控制台页面，不含秘密和原始材料。
- 独立安全复核没有发现需修复的权限或数据暴露问题。紧急信任撤销可暂时阻断最后 admin/owner，界面与指南已明确独立恢复渠道要求。

本地运行服务在确认活跃任务为 0 后重载，健康检查为 200，新身份接口已出现在运行中的 OpenAPI。真实平台接入仍未配置，默认关闭；原有浏览器 realm 与材料保留。
