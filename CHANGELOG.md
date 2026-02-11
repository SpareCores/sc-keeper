## February 2026

New feature(s):

- Monthly pricing calculations for on-demand servers with optional capping.
- Support for float GPU count values for fractional GPU allocation.
- New endpoint `/table/stats` for querying database table statistics with optional vendor and status filters.

## January 2026

New feature(s):

- OAuth2 Bearer token authentication support with configurable security schemes.
- Token introspection for OAuth2 authentication.
- Rate limiting with Redis backend and in-memory fallback.
- Credit-based rate limiting with configurable penalties for different response codes.
- Custom CEL (Common Expression Language) expressions for token validation and claim extraction.
- User authentication tracking and token source monitoring.
- Optional authentication requirements for specific endpoints.
- Server prices table dump endpoint with filters and authentication protection.

Fix(es):

- Redis connection error logging and graceful fallback handling.
- Token active status validation in all authentication flows.
- Self-reference issues in authentication middleware.
- Rate limit middleware properly applies 401 penalties.
- Currency code validation with HTTP 400 on invalid codes.
- Price currency handling in conversion operations.
- Middleware execution order for proper auth and rate limiting.
- Exclude low-frequency and potentially incorrect GPU models.

Housekeeping:

- Authentication and rate limiting middleware order optimization.
- Cache control to skip caching endpoints requiring authentication.
- Async HTTP client migration from `requests` to `httpx`.
- Logger namespace standardization across all modules.
- Currency conversion optimized to run only when needed.
- Excluded inactive server types from API responses.
- Test coverage for authentication and rate limiting features.
- Thread-safe in-memory credit consumption tracking.
- Move rate-limit logic to Redis for atomic updates.
- Log client application ID when available.
- Pin ReDoc version for stability.

## December 2025

Housekeeping:

- Bump `sparecores-crawler` and `sparecores-data` dependencies to 0.3.2.

## October 2024

New feature(s):

- Support for CPU allocation filtering (shared vs dedicated).
- Materialized views for minimum server prices for improved performance.
- Background thread for currency rate updates from ECB.
- Currency table with live exchange rates.
- Server minimum prices included in Server objects.
- Similar servers by score per price endpoint.
- Min `$Core` (score per price) filter for server searches.

Fix(es):

- Consider servers without price as inactive.
- Descending order on computed columns.
- Hide servers without values when filtering by related columns.
- Green energy definition synchronized with crawler.

Housekeeping:

- Bump version to match `sparecores-crawler` for `Disk` description support.
- Drop v1 server details endpoint.
- Drop dead `min_server_price` code.
- Index optimization for server prices.
- Round `score_per_price` to 4 digits.
- Code review with automated linting improvements using `ruff`.

## September 2024

New feature(s):

- Storage prices endpoint with comprehensive filtering options.
- Traffic prices endpoint with monthly pricing calculations.
- Tiered pricing support for traffic costs with proper tier calculation.
- Integration tests for all major API endpoints.
- Similar servers endpoint based on hardware specifications.
- Currency conversion support for storage and traffic prices.
- Parametrized test suite for comprehensive coverage.
- Server details v2 endpoints with improved data structure.

Fix(es):

- Duplicate rows from many-to-many relationships eliminated.
- Slow full-row lookups optimized.
- Active price filtering consistency across endpoints.
- Correct tiered price calculation (only apply delta per tier).
- Default to outbound traffic prices.
- Function name conflicts resolved.

Housekeeping:

- Indexed SQLite for better full-row lookup performance.
- Efficient count queries to avoid N+1 problems.
- Pre-query optimization for compliance framework lookups.
- Eager loading strategies for relationships to reduce query count.
- Optimized query performance with strategic JOINs.
- Test performance monitoring (flag queries >250ms).
- Require latest version of crawler and data packages.
- Code organization: split routers into separate files.

## August 2024

New feature(s):

- Cache control headers for client-side caching.
- Sentry integration for error tracking and monitoring.
- System resource logging (CPU, memory, I/O, iowait).
- Request/response logging with unique request IDs.
- Sentry release tracking with environment context.
- Allow Sentry headers in CORS configuration.
- Experimental DuckDB integration for faster queries.

Fix(es):

- Thread-safe database operations with proper locking.
- Proper connection handling and cleanup for database operations.
- Self-locking issues in database update operations resolved.
- Fall back to SQLite when DuckDB is unavailable.
- IO counters handling on unsupported platform.

Housekeeping:

- Async database operations with proper locking mechanisms.
- Database updates moved to background threads.
- Increased page size limits for server prices (from 50 to higher limits).
- Later reduced default page size for performance.
- Server list reloading every 10 minutes.
- Production infrastructure moved to EU.
- Background database update at initialization.
- Search support for 200+ CPUs.
- Limit page size of server price queries.
- Install Sentry SDK as optional dependency.

## June 2024

New feature(s):

- Benchmark scores endpoint with full benchmark data.
- Benchmark table dump for all available benchmarks.
- API reference queries for servers using api_reference parameter.
- Human-friendly column names with category information.
- Support for `SCore` (Spare Cores Score) metric in API responses.
- Price per `SCore` (`$Core`) calculations and filtering.
- GPU total memory search and filtering capabilities.
- `Score` and `$Core` added to individual server lookups.

Fix(es):

- Example JSON on `/benchmark_scores` endpoint corrected.
- Server ID references in subqueries fixed.
- Handling of missing `SCore` values in calculations.
- Division by zero errors in price per score calculations.
- Missing `KeyValue` errors for lowest prices.
- Price information handling when unavailable.

Housekeeping:

- Renamed Datacenter to Region across the entire API.
- Updated field descriptions for improved clarity.
- Server memory field renamed to `memory_amount`.
- Path parameters changed from generic IDs to descriptive names (e.g., `vendor_id`, `server_id`).
- Locked `sparecores-data` and `sparecores-crawler` versions for the SQLModel schema versions.

## May 2024

New feature(s):

- `/servers` endpoint for server listings without price information.
- `/server_prices` endpoint (renamed from previous `/servers`).
- AI assistant endpoints for natural language queries via OpenAI integration.
- Currency parameter for server and price queries with automatic conversion.
- Freetext search on server IDs and names.
- Unit of measurement display in API responses.
- Table metadata endpoints for data exploration.
- Endpoint categories and organization for better API navigation.
- Healthcheck endpoint with database validation.
- `X-Total-Count` header for pagination support.
- Request ID tracking in custom headers.
- Compliance framework filtering by multiple frameworks.
- Country-based filtering for regions/datacenters.
- Vendor ID enumeration for better type safety.
- Optional total count parameter to improve performance.
- Comprehensive filter options:
    - Vendor, CPU, memory, storage, GPU filters.
    - Green energy availability filtering.
    - Compliance framework filtering (HIPAA, SOC2, etc.).
    - Architecture filtering (x86_64, arm64, etc.).
    - Storage type filtering.
    - GPU manufacturer, family, and model filters.
    - Total GPU memory filtering.
- Server similarity endpoints based on hardware specifications.
- Similar servers by family endpoint.

Fix(es):

- Don't key enum values by name to avoid serialization issues.
- Filter conditions properly enforced in count queries.
- CORS header exposure for `X-Total-Count`.
- Duplicate server entries in filtered results.
- Filter enforcement in count queries.
- Distinct query optimization when joining compliance frameworks.
- Fix OpenAPI/Swagger syntax error due to dict vs array confusion.

Housekeeping:

- Separated AI endpoints to dedicated module file.
- Query parameter details moved to helpers module for DRY code.
- Standardized lookups across all endpoints.
- File restructuring for better code organization.
- API restructured into logical modules with routers.
- Moved healthcheck, tables, and metadata to separate files.
- Router-based organization with OpenAPI tags.
- Filter descriptions moved to DRY helpers.
- Restructured main API file for readability.
- Consistent examples across all endpoints.
- Load examples into a dictionary for reusability.

## April 2024

New feature(s):

- CORS support for cross-origin requests.
- Nested data structure for vendor relationships.
- Currency conversion support via ECB rates.
- Green energy filter option.
- Server listing query support with multiple filters.
- Query parameter metadata for frontend filtering.
- Architecture filter option.

Fix(es):

- Database session caching issues resolved.
- Join operations on overlapping compound foreign keys.
- Last updated date tracking fixed.
- SQL query logging disabled by default for performance.
- Coerce filter values to proper boolean type.
- Status filter properly applied.

Housekeeping:

- Response compression enabled via middleware.
- Session handling improvements to avoid caching.
- Moved `main.py` to `api.py` for clarity.
- Move source code under `src/` directory.
- Package dependencies updated (`sc` to `sparecores` naming).
- Reflect `sc-crawler` schema updates.
- Request/response logging middleware using structured JSON output.
- Logging configuration with custom formatters.
- Initialize linting and testing framework.
- Docker build configuration improvements.
- GitHub Actions workflow for automated deployment.

## March 2024

Fix(es):

- Schema name updates to match recent `sc-crawler` changes.

Housekeeping:

- Cross-platform path handling for Windows compatibility using `os.path`.
- Package distribution name updated to `sparecores`.

## February 2024

Initial release:

- FastAPI application structure and foundation.
- SQLite database file integration via `sc_data` package.
- Database hot-reload on updates.
- Response model annotations using type hints.
- Set example values for Swagger documentation.
- Annotated example inputs for API documentation.

API endpoints:

- Server instance lookup endpoint by vendor and API ID.
- Server search endpoint by CPU/RAM specifications.
- Swagger/OpenAPI documentation with examples.
