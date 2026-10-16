# Development identity verification

Task 3's local tests use an isolated signed issuer fixture, HTTP mock transport and real MongoDB replica-set transactions. The fixture is never a runtime identity provider. The application denies MCP tokens by default; external identity routes activate when an OIDC client ID is configured. No identity container, local password store or model download is required.

The verifier pins the exact issuer and resource audience, uses only RS256/ES256 signing keys, requires issuer/subject/audience/issued-at/expiry claims, and checks the required scope. Discovery uses the configured issuer; a JWT header cannot choose a key URL. Signing keys refresh on expiry and on a new key ID, with a bounded refresh rate. Issuer/subject identifiers retain their exact text. The relevant primary references are [PyJWT validation](https://pyjwt.readthedocs.io/en/latest/api.html) and [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html).

The console uses the authorization-code flow with S256 PKCE, a browser-bound one-time state, nonce validation, and server-side sessions. Only hashes of session and CSRF tokens are stored. Cookies use HttpOnly for the session, SameSite, and Secure when the public URL is HTTPS. Cookie-authenticated writes require the exact public origin and the session CSRF token. Identity responses use `Cache-Control: no-store`. Provider tokens are not retained in sessions.

Active local membership is required after credential verification. Organization administrators admit members and service identities and manage groups. Issuer/subject collisions across providers do not establish membership. Organization policy mutations are transactional, idempotent and audited; a shared organization write serializes concurrent changes to different admins. The active-admin count cannot become zero. Services cannot perform interactive account administration; their explicit KG and scope restrictions feed the subsequent policy layer. KG roles and material permissions remain Task 4 work.

Run the small local gate:

```bash
python3 scripts/smoke.py --task 3
```

This does **not** satisfy the plan's real development IdP login gate. For that gate, supply the development issuer URL and public console client ID, register `http://127.0.0.1:8000/auth/callback` (or the exact configured public URL) as the callback, and use an external provider that issues resource-bound JWT access tokens with audience `http://127.0.0.1:8000/mcp` and scope `domain_kg:read`. HTTPS is required for a shared deployment; plain HTTP identity endpoints are accepted only on loopback. A confidential client's secret belongs in private server configuration.

Set `DOMAIN_KG_OIDC_ISSUER` and `DOMAIN_KG_OIDC_CLIENT_ID` in the ignored `.env`. Operator provisioning creates the first administrator invitation without accepting credentials:

```bash
docker compose up -d --wait mongo
uv run python scripts/bootstrap_identity.py --name Team --subject EXACT_IDP_SUBJECT
uv run python -m domain_kg
```

The provisioning command validates provider discovery, refuses to bootstrap over an existing organization, and prints only organization and invited-member UUIDs. It does not log in as that subject. Open `/auth/login?org_id=ORGANIZATION_UUID`, complete login at the external provider, and verify `/api/v1/me`. `/admin` is the redirect target reserved for the later console task; its UI is not implemented yet. Verify a valid resource token against `/mcp` and confirm an expired, wrong-audience or suspended identity is refused. Record the real provider/login result before checking Task 3 complete.

For a principal belonging to multiple organizations, HTTP bearer requests select an existing membership with `X-Domain-KG-Organization`. The selector cannot establish identity or grant access. List cursors bind the actor, organization, query, limit and current policy revision; policy changes expire them. Authentication and local checks fail closed when the authority or provider cannot be verified.
