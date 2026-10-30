# 身份接入：授权服务配置与部署

本文面向部署 KG 的 operator 和实现 Agent 平台后端的开发者。API、审批和同步事件请结合[身份接入指南](identity-bridge.md)阅读。KG 仍是资源服务；用户登录、签名密钥、canonical 用户标识和 access token 签发由外部 OIDC/OAuth 授权服务承担。

## 支持范围

本实现验证签名 JWT，不接受 opaque token，也没有 introspection、密码注册或 refresh-token 接口。平台用户证明、平台机器凭证和调用 `/mcp` 的 access token 是三种不同用途的凭证。

| 凭证 | 必须满足的条件 |
| --- | --- |
| 平台机器凭证 | 固定的 caller issuer、audience、client 和 service subject；对应接口的 `identity:provision`、`identity:exchange` 或 `identity:sync` scope |
| 用户证明 | 固定 subject issuer、audience、client；稳定 `sub`、唯一 `jti`；`exp - iat` 为 1–120 秒；如配置 tenant，则必须匹配 |
| 交换结果 | 当前 KG canonical issuer 验签；audience 为当前部署的 MCP 资源；交换 client 匹配；具有 `jti` 和本次要求的 scope；`exp - iat` 不超过 600 秒 |

OIDC verifier 仅接受 RS256/ES256，检查固定 issuer、audience、有效期和签名。它不会把 JWT 自带的 URL 当作新的可信身份来源。issuer 通过部署配置批准；除开发 loopback 外要求 HTTPS。

用户证明在 API 中以 `urn:ietf:params:oauth:token-type:jwt` 声明，表示 KG 要验证签名 JWT。发送到授权服务的 RFC 8693 `subject_token_type` 由 operator 配置，因为同一个 JWT 可以是 OAuth access token。两者不能混淆。

通用适配器提供 RFC 8693 的请求形式，并不保证任意 OAuth 服务或任意外部平台 token 可交换。接入前必须证明该授权服务能把已批准上游身份映射为稳定 canonical subject，并满足上表。没有这项能力时，应先配置外部身份 broker，不能放宽 KG 的验签、audience 或连接检查。

## Operator 配置

默认不启用身份桥接。设置 `DOMAIN_KG_IDENTITY_BRIDGE_CONFIG` 指向 operator 创建的 JSON 文件。该文件必须是普通文件、不能是符号链接、不能有 group/world 权限，大小不得超过 1 MiB；建议目录权限 0700、文件 0600。把它放在 Git 之外，通过部署秘密管理注入；不要放进网页、MCP 参数、公开镜像或应用日志。

```sh
export DOMAIN_KG_IDENTITY_BRIDGE_CONFIG=/run/secrets/domain-kg/identity-bridge.json
```

示例结构如下。所有尖括号内容、UUID 和端点都必须替换；这不是可直接运行的配置。`exchange_client_secret` 是授权服务的 confidential-client secret，不是用户密码。

```json
{
  "response_key": "<BASE64_ENCODED_32_RANDOM_BYTES>",
  "profiles": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "name": "Approved Agent platform",
      "allowed_org_ids": ["22222222-2222-4222-8222-222222222222"],
      "caller_issuer": "https://login.example.com/realms/team",
      "caller_audience": "kg-identity-bridge",
      "caller_client_id": "agent-provisioner",
      "caller_subject": "<FIXED_SERVICE_ACCOUNT_SUBJECT>",
      "subject_issuer": "https://login.example.com/realms/team",
      "subject_audience": "kg-user-assertion",
      "subject_client_id": "agent-user-app",
      "exchange_client_id": "kg-exchange",
      "exchange_client_secret": "<CONFIDENTIAL_CLIENT_SECRET>",
      "exchange_subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
      "exchange_parameter": "audience",
      "exchange_audience": "https://kg.example.com/mcp"
    }
  ]
}
```

使用密码学随机数生成 32 字节 response key，并经标准或 URL-safe base64 编码；例如在受控终端执行以下命令，将输出直接写入秘密管理，不要贴入工单。

```sh
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

`response_key` 用于加密短期、可幂等重取的 token 响应；数据库连接记录只绑定 access-token SHA-256。签名验证仍由 OIDC 完成。各实例必须使用同一配置与 response key。

| Profile 字段 | 含义与约束 |
| --- | --- |
| `id` | 固定 UUID；已注册接入通过此 ID 引用 profile |
| `allowed_org_ids` | operator 允许使用此 profile 的组织集合；不授予任何 KG 权限 |
| `caller_*` | 平台后端机器身份，四项同时检查；普通用户 token 或已有 KG service identity 不会自动获得开通权 |
| `subject_*` | 用户证明的固定身份边界；不按邮箱或昵称合并用户 |
| `tenant_claim` / `tenant_id` | 可选，必须同时配置；claim 仅允许 `tid` 或 `tenant_id` |
| `exchange_client_id` / `exchange_client_secret` | 在 canonical 授权服务注册的 confidential client；不能与其他 profile 重复使用 client ID |
| `exchange_subject_token_type` | `jwt` URI 为默认值；Keycloak 标准 V2 必须明确设为 `access_token` URI |
| `exchange_parameter` | `resource` 为默认值，传当前 MCP 资源；`audience` 用于经验证的厂商适配 |
| `exchange_audience` | 仅当 parameter 为 `audience` 时设置，必须是固定值；它不会替代交换结果的 canonical audience 校验 |
| `subject_issuer_alias` | 可选的授权服务扩展参数；仅在该服务已明确配置和验证此别名时使用，标准 Keycloak 同 realm 路径无需此字段 |

Operator 安装 profile 后，当前组织管理员仍须建立 integration，并由目标 KG owner 认可允许 KG 和默认授权。Profile 不是启用所有组织/KG 的开关。不要给不可信 Agent 自助写 profile 的能力。

## Keycloak 26.8.1 配置

已选用受支持的 **standard token exchange V2**。不要为了接入启用旧版 token exchange V1、preview impersonation 或任意用户冒充。

1. 平台用户已在同一个 canonical realm 建立稳定身份。不同平台或 realm 的身份，应先通过 Keycloak 支持的身份 brokering/授权链路接入；本实现不证明任意外部 JWT 可直接进行 standard V2 exchange。
2. 在该 realm 注册 confidential requester client，例如 `kg-exchange`，启用 **Standard token exchange**。realm import 中对应 client attribute 为 `standard.token.exchange.enabled=true`。
3. 平台用户 token 由已批准的 `subject_client_id` 签发，生命周期不超过 120 秒，含稳定 `sub` 和 `jti`，并包含 profile 的 subject audience。`aud` 还必须含 requester client ID；只有 requester 自己交换自己原先取得的 token 可免除后者要求。显式导入自定义 `clientScopes` 时，保留默认 `basic` scope 中的 subject mapper，或在相关 client 明确配置 `oidc-sub-mapper`；缺少 `sub` 的 token 会被 KG 拒绝。
4. 在 requester 可用的 scopes 中配置 `domain_kg:read`，以及需要写入时的 `domain_kg:write`。配置 audience mapper，让交换结果含目标 MCP audience；不要依赖 `audience` 请求参数凭空添加 audience。
5. 把 target client ID 配置为目标 audience。例如将 Keycloak target client ID 设为 `https://kg.example.com/mcp`，使用 mapper 的 `included.client.audience` 指向该 client，再将 profile 的 `exchange_audience` 设为同一值。也可使用其他已验证映射，但最终 JWT 必须包含 KG 实际校验的 MCP resource audience。
6. 配置交换输出生命周期不超过 600 秒。平台调用的机器 client 与 token-exchange client 分离；前者只拥有桥接 API scopes，不提供通用 KG 管理能力。

标准 V2 的 `subject_token_type` 必须是 `urn:ietf:params:oauth:token-type:access_token`。虽然 token 的编码是 JWT，传 `...:jwt` 仍会被 Keycloak 拒绝。标准 V2 使用 `audience` 参数选取目标 client，不直接支持以 RFC `resource` 参数完成这项适配。

Keycloak 默认允许 exchange 使用 requester 可用的额外 scopes。应在 IdP 端限制 requester scope、必要时配置 downscope policy。KG 仍独立把请求限制在 integration、连接和当前用户权限的交集；不能把 IdP 的默认行为视为 KG 授权。

## 可重复的真实授权服务验证

仓库提供：

```sh
.venv/bin/python scripts/verify_identity_exchange.py
```

脚本只使用已缓存、固定 digest 的 Keycloak 26.8.1 镜像，以 loopback 临时端口启动独立容器，内存及 swap 总上限 512 MiB、CPU 上限 1。它建立一次性的合成 realm 和用户，通过真实发现、JWKS、签名用户 token 和 standard V2 exchange 验证现有 broker，并验证 `...:jwt` 类型会被拒绝。正常退出或验证失败都会删除合成容器及临时文件；不挂载持久卷，不接触现有浏览器 realm，也不调用模型 API。

脚本中的 password grant 仅用于取得合成测试用户的 token，不能复制到正式部署。正式平台使用已有登录和用户证明机制。脚本不输出密码、client secret 或 JWT。

此脚本只证明授权服务适配层。它不代替 MongoDB 并发/恢复测试、完整 HTTP/MCP 连接撤销测试、浏览器审批测试或具体外部平台的上线验收。不同 IdP 版本、realm 配置、跨 issuer brokering 和 OAuth 客户端都必须独立验证。

2026-10-31 已实测该脚本通过：平台机器凭证、短期签名用户证明、稳定 subject、canonical MCP audience、交换 client 和 scopes 均通过验证；generic JWT token type 的负向测试通过。该记录只覆盖上述固定镜像与合成配置，不等同于已接入某个外部平台。

完整 HTTP/MCP/后台发布链路的真实 IdP 验收测试为：

```sh
.venv/bin/pytest tests/integration/test_identity_bridge_keycloak.py -q
```

2026-10-31 该完整验收也已通过：实际 HTTP 开通/交换、MCP 罗列/添加文本、worker 发布、检索及撤销后拒绝均完成。

该测试另外需要仓库的开发 MongoDB replica set、Neo4j、Qdrant 和对象存储。测试使用独立命名空间、真实存储和合成身份，模型接口为 mock；不会调用已配置的付费模型。测试命令是否通过，应以当次输出为准，不能用上面的 broker 验证记录替代。

## 变更、撤销与回退

- 禁用 integration 会立即阻断其连接；不以 access token 尚未过期为由继续访问。不要仅在 IdP 删除 client 后等旧 JWT 过期。
- 已登记 exchange client 的 token 必须具有 KG 服务端绑定；即使签名和 audience 正确，未通过本接入 API 取得的 token 也不能回退为普通用户 token。撤销时保留绑定历史，避免撤销后绕过。
- 先禁用受影响 integration、撤销连接，再变更 profile/client 身份边界。不要复用旧 profile/client ID 指向另一平台或组织。重新启用不恢复旧的撤销连接。
- 从配置移除 profile 后重启，会停用无法再匹配批准 profile 的 integration；不会自动删除其成员、来源、图谱、历史 actor 或审计记录。回退不需要重建 KG。
- response key 当前为单密钥配置。轮换会使旧加密 token 响应无法重取，但不会替代连接撤销；应在短期响应到期后轮换，或让平台以新证明和新幂等 key 重新申请。多实例需协调切换，不能混用密钥。
- 配置文件和秘密管理的备份应与 Mongo 身份权威的恢复流程协调。恢复后先核对 profiles、接入启停与撤销状态，再开放入口；不要仅恢复 Mongo 的历史快照而复活已撤销接入。

## 上线验收

对每个实际平台使用合成测试账号验证：正确开通、并发/重试不重复建用户、待审批无数据权限、错误 issuer/audience/client/tenant 均拒绝、断言换 key 重放被拒绝、只读连接不能写入、越界 KG 被拒绝、撤销后 HTTP/MCP 及后台发布均拒绝、上游停用及旧事件顺序正确、IdP 故障可安全重试。确认现有 OIDC 浏览器登录与普通 service identity 未被扩权或错误停用。

通过这些检查后，才能将该平台标为已兼容。仅有机器 token 或客户端注册成功，不足以证明用户身份桥接或授权安全。

## 参考

- [RFC 8693: OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
- [Keycloak Token Exchange](https://www.keycloak.org/securing-apps/token-exchange)：standard V2 的 token type、requester audience、scope 和兼容范围
- [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [MCP security best practices](https://modelcontextprotocol.io/specification/2026-07-28/basic/security_best_practices)

`Mcp-Session-Id` 始终只是传输状态，不能代替 `Authorization: Bearer`；浏览器 cookie 不复制给 Agent。
