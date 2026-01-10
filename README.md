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
- `REDIS_URL` - Redis connection URL (required if `RATE_LIMIT_BACKEND=redis`, also used for token caching if authentication is enabled). Supports authentication via URL format: `redis://:password@host:port/db` or `redis://username:password@host:port/db`

Rate limiting is disabled by default. When enabled, it uses a credit-based system where all requests share a credit pool, with different routes consuming different amounts of credits.

- `RATE_LIMIT_ENABLED` - Enable rate limiting (set to any truthy value)
- `RATE_LIMIT_CREDITS_PER_MINUTE` - Default credits per minute (default: `60`)
- `RATE_LIMIT_DEFAULT_CREDIT_COST` - Default credit cost per request (default: `1`)
- `RATE_LIMIT_BACKEND` - Backend to use: `memory` (default) or `redis`

Custom credit costs per route can be configured in `src/sc_keeper/rate_limit.py` via the `CUSTOM_RATE_LIMIT_COSTS` dictionary. Routes not listed default to `RATE_LIMIT_DEFAULT_CREDIT_COST` credits per request.

Authentication uses Zitadel (or compatible identity provider) for token validation. Token validation is automatically enabled if `ZITADEL_URL` is set.

- `ZITADEL_URL` - Base URL of the identity provider API (required for token validation)
- `ZITADEL_TOKEN_CACHE_SALT` - Optional salt for token hashing
- `ZITADEL_TOKEN_CACHE_L1_TTL_SECONDS` - L1 (in-memory) cache TTL in seconds (default: `60`)
- `ZITADEL_TOKEN_CACHE_L1_MAX_SIZE` - Maximum size of L1 cache (default: `1000`)
- `ZITADEL_TOKEN_CACHE_TTL_SECONDS` - L2 (Redis) cache TTL in seconds (default: `300`)

When authentication is enabled, users can include a Bearer token (access token or PAT) in the `Authorization` header. Authenticated users' credit limits are determined by their `api_credits_per_minute` field from the identity provider (or `api_requests_per_minute` for backward compatibility), which takes precedence over the default credit limit.

## Useful debug links

- Swagger docs: http://localhost:8000/docs
- Server details example: http://localhost:8000/server/aws/p3.8xlarge
- Server search example: http://localhost:8000/servers?vcpus_min=2&memory_min=8&limit=5
