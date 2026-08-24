## Spare Cores Keeper API

Implements a HTTP API to search the Spare Cores data.

Main dependencies:

- `sparecores-data`
- `FastAPI`
- `SQLModel`

### Usage

Run the application in a single process:

```bash
git clone git@github.com:SpareCores/sc-keeper.git
cd sc-keeper
pip install -e .
uvicorn sc_keeper.api:app --reload
```

### Environment Variables

All environment variables are optional.

- `SENTRY_DSN` - Sentry DSN for error tracking
- `KEEPER_DEBUG` - Enable SQLAlchemy query logging (set to any truthy value)
- `OPENAI_API_KEY` - OpenAI API key for AI features
- `REDIS_URL` - Redis connection URL (required if `RATE_LIMIT_BACKEND=redis`,
  also used for token caching if authentication is enabled). Supports
  authentication via URL format: `redis://:password@host:port/db` or
  `redis://username:password@host:port/db`

Rate limiting is disabled by default. When enabled, it uses a credit-based
system where all requests share a credit pool, with different routes consuming
different amounts of credits.

- `RATE_LIMIT_ENABLED` - Enable rate limiting (set to any truthy value)
- `RATE_LIMIT_CREDITS_PER_MINUTE` - Default credits per minute (default: `60`)
- `RATE_LIMIT_DEFAULT_CREDIT_COST` - Default credit cost per request (default: `1`)
- `RATE_LIMIT_BACKEND` - Backend to use: `memory` (default) or `redis`

Custom credit costs per route can be configured in `src/sc_keeper/rate_limit.py`
via the `CUSTOM_RATE_LIMIT_COSTS` dictionary. Routes not listed default to
`RATE_LIMIT_DEFAULT_CREDIT_COST` credits per request.

Furthermore, the number of concurrent heavy jobs can be limited to avoid overloading the database:

- `HEAVY_JOBS_MAX_CONCURRENT` - Maximum number of concurrent heavy jobs (default: `2`)
- `HEAVY_JOBS_ACQUIRE_TIMEOUT_SEC` - Timeout in seconds for acquiring a heavy job permit (default: `2.0`)

Authentication supports four independent Bearer verification methods. Token
validation is enabled when any method group is fully configured.

| Method | Standard | Env vars (enable group) |
|---|---|---|
| Token introspection | [RFC 7662](https://datatracker.ietf.org/doc/html/rfc7662) | `AUTH_TOKEN_INTROSPECTION_URL` (+ `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`) |
| JWT Bearer | [RFC 7519](https://datatracker.ietf.org/doc/html/rfc7519) + JWKS | `AUTH_JWT_JWKS_URL` or `AUTH_JWT_PUBLIC_KEY` |
| API key verify | Vendor-specific (generic, configurable) | `AUTH_API_KEY_VERIFY_URL` (+ `AUTH_API_KEY_VERIFY_BEARER`) |
| Static token allowlist | Local exact-match table | `AUTH_STATIC_TOKENS` |

When several methods are enabled, optional per-method regexes route tokens
sequentially: methods whose regex matches the Bearer token are tried first
(API key, then JWT, then introspection), then methods with no regex in the same
order. The static token allowlist always runs last.

Example regexes scenario:

- API keys: `^ak_` (e.g. Clerk)
- JWTs: `^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`
- Introspection: omitted so it runs as a catchall (before the static allowlist)

### Token introspection (RFC 7662; e.g. ZITADEL)

- `AUTH_TOKEN_INTROSPECTION_URL` - Full URL of the token introspection endpoint
- `AUTH_CLIENT_ID` - Client ID for token inspection API (basic auth)
- `AUTH_CLIENT_SECRET` - Client secret for token inspection API (basic auth)
- `AUTH_TOKEN_INTROSPECTION_REGEX` - Optional Python regex (`search`) on the raw Bearer token
- `AUTH_TOKEN_VALIDATION_CEL` - Optional CEL rule to evaluate the token introspection response (passed as `{"claims": <token introspection response>}` in the CEL context) for validation (e.g. to enforce tenant-specific scopes or other claims), using [Python CEL](https://python-common-expression-language.readthedocs.io/). Introspection only.
- `AUTH_TOKEN_EXTRA_FIELDS_CEL` - Another optional CEL expression to extract additional fields from the token introspection response into a dictionary, appended to the `User` object (stored both in `request.state.user` and for logging purposes). Introspection only.

### JWT Bearer (e.g. Clerk session JWT from `session.getToken()`)

- `AUTH_JWT_JWKS_URL` - JWKS endpoint (e.g. `https://<provider-domain>/.well-known/jwks.json`)
- `AUTH_JWT_PUBLIC_KEY` - The public key in PEM format as an alternative to JWKS
- `AUTH_JWT_ISSUER` - Optional `iss` check
- `AUTH_JWT_AUDIENCE` - Optional `aud` check (comma-separated)
- `AUTH_JWT_AUTHORIZED_PARTIES` - Optional `azp` allowlist (comma-separated frontend origins)
- `AUTH_JWT_TOKEN_REGEX` - Optional Python regex (`search`) on the raw Bearer token
- `AUTH_JWT_JWKS_CACHE_TTL_SECONDS` - How long a fetched JWKS is cached before refresh (default: `300`)
- `AUTH_JWT_EXTRA_CLAIMS` - Optional comma-separated JWT claim names to copy onto the `User` object (e.g. `org_id`, or `org_id:organization_id` to rename). Missing claims are skipped.

Session JWTs use the default rate limiter (per-user credit override is not supported).

### Opaque API-key (e.g. Clerk API key)

- `AUTH_API_KEY_VERIFY_URL` - Remote API endpoint to verify the API key via `POST` request with JSON body
- `AUTH_API_KEY_VERIFY_BEARER` - Bearer token to authenticate against the verify API
- `AUTH_API_KEY_VERIFY_REQUEST_FIELD` - JSON request field that should hold the Opaque API-key to be verified (default: `secret`)
- `AUTH_API_KEY_VERIFY_SUBJECT_FIELD` - Response field that should be mapped to `user_id` (default: `subject`)
- `AUTH_API_KEY_VERIFY_CLAIMS_FIELD` - Response field holding claims (default: `claims`), from which all non-reserved keys (including e.g. the optional `api_credits_per_minute`) are copied onto the `User` object.
- `AUTH_API_KEY_TOKEN_REGEX` - Optional Python regex (`search`) on the raw Bearer token

### Static token allowlist

- `AUTH_STATIC_TOKENS` - JSON array of objects. Required keys: `token`, `subject`. Any other keys (including the optional `api_credits_per_minute`) are copied onto the `User` object.

Example: `[{"token": "foo", "subject": "FOO", "api_credits_per_minute": 42, "organization_id": "ORG"}, {"token": "bar", "subject": "BAR"}]`

### Shared cache

- `AUTH_TOKEN_CACHE_SALT` - Salt for token hashing
- `AUTH_TOKEN_CACHE_L1_TTL_SECONDS` - L1 (in-memory) cache TTL in seconds (default: `60`)
- `AUTH_TOKEN_CACHE_L1_MAX_SIZE` - Maximum size of L1 cache (default: `1000`)
- `AUTH_TOKEN_CACHE_L2_TTL_SECONDS` - L2 (Redis) cache TTL in seconds (default: `300`)

When authentication is enabled, clients can include a Bearer token in the
`Authorization` header. Authenticated users' credit limits are determined by
their `api_credits_per_minute` claim when provided (introspection, API-key,
and static-token methods). JWT session tokens use the default credit limit.

## Useful debug links

- Swagger docs: http://localhost:8000/docs
- Server details example: http://localhost:8000/server/aws/p3.8xlarge
- Server search example: http://localhost:8000/servers?vcpus_min=2&memory_min=8&limit=5
