## November 2026

New features:

- `/snapshot/{hash}/...` endpoints (API key required): every table pinned to a database
  hash, tables that reference a vendor split by vendor (scores by vendor and benchmark),
  with available records only, prices in USD and every column except `observed_at`, as
  gzipped columnar JSON. `/snapshot/{hash}` lists the files with checksums. Snapshots
  are built once per hash and shared between instances through S3 (`SNAPSHOT_S3_URI`)
  (#111)
- `/ping` liveness check that answers as long as the process does, without the database
  or the thread pool shared by the other endpoints (unlike `/healthcheck`) (#111)
- `/table/currency` table dump of the `Currency` table (#117)

Improvements:

- `Server.retirement_date`, `Database.retirement_date`, the bundled disk speeds of
  servers (`storage_read_iops_max`, `storage_write_iops_max`,
  `storage_read_throughput_max`, `storage_write_throughput_max`) and the same per-disk
  speeds in `Server.storages`
  ([`sparecores-crawler` 0.12.0](https://docs.sparecores.com/crawler/news#v0120-november-21-2026))
  (#120)
- Records with the new `ARCHIVED` status (replaced or deliberately dropped records, e.g.
  the old Hetzner datacenter regions) left out of every endpoint, including `/regions`,
  the `/table/...` endpoints and `only_orderable=false` or `only_active=false` queries;
  there is no parameter to include them (#114)
- `SC-Data-Hash` and `SC-Data-Published-At` headers in every response: the hash and the
  publication time of the database it was built from (#111)
- `/healthcheck` lists the database hashes with snapshots on the instance in
  `snapshot_hashes`, and returns the publication time of the database in
  `database_published_at` (#111)
- Enum value descriptions in the OpenAPI schema (#115)

Fixes:

- The `family` method of `/server/{vendor}/{server}/similar_servers/{by}/{num}` returns
  the servers of the same series (e.g. `t2d`), and falls back to the same family (a
  broad category at most vendors, e.g. "General Purpose") only when the series is
  unknown or has fewer than 3 other servers (#113, #116)

## October 2026

Breaking changes:

- `Server.gpu_*` and `gpus` fields renamed to `accelerator_*` and `accelerators` in
  responses and `order_by`
  ([`sparecores-crawler` 0.10.0](https://docs.sparecores.com/crawler/news#v0100-october-8-2026));
  the `gpu_*` query parameters keep their names (#104)
- `/servers` `gpu_*` filters match only `accelerator_type=GPU`, except `gpu_max`, which
  also matches servers with non-GPU accelerators; `gpu_manufacturer` and `gpu_family`
  values are GPU-only on `/servers` and `/server_prices`. The new `accelerator_*`
  filters cover all accelerator types (#108)
- `regions` and `vendor_regions` filter values include only active regions; inactive
  ones are rejected with 422 (#108)

New features:

- `accelerator_type`, `accelerator_min`, `accelerator_max`, `accelerator_memory_min` and
  `accelerator_memory_total` filters on `/servers` (any accelerator type), under a new
  `accelerator` filter category (#108)
- `asic_min`, `asic_max`, `asic_memory_min`, `asic_memory_total`, `asic_manufacturer`,
  `asic_family` and `asic_model` filters on `/servers`, under a new `asic` filter
  category, matching all non-GPU accelerators (ASICs, e.g. Google TPUs); `asic_max` also
  matches servers with GPUs or no accelerators (#108)
- `/inspector/task_block_reason_codes` and
  `/inspector/server/{vendor}/{server}/task_block_reason_codes` endpoints that tell why
  inspector tasks are blocked (#99, #103)

Improvements:

- `Server.accelerator_type`, `series`, `api_reference_object` and
  `compatible_storage_ids` in `/table/server/meta` (#104)
- Benchmark `subcategory` in `/benchmark_configs`; configs and server and database
  benchmarks are ordered by the new categories, then subcategory and `benchmark_id`
  (#104)
- `memory_max`, `memory_per_vcpu_min`, `memory_per_vcpu_max`, `gpu_max`, `price_max`,
  `benchmark_score_max` and `benchmark_score_per_vcpu_min` filters on `/servers` (#101)
- `Retry-After` header in 429 responses, exposed to browsers via CORS along with the
  `X-RateLimit-*` headers (#102)
- `only_active` filter on `/server/{vendor}/{server}/prices`, to include inactive server
  prices
- Responses compressed by CloudFront instead of gzip at the origin (#100)
- Package version `<year>.<month>+g<short commit sha>` (e.g. `2026.10+g5e02c8c`), set at
  Docker build time and shown in the API docs and `/healthcheck`, instead of the
  manually bumped (and long stale) `0.3.6` (#106)
- The most recent inspector rules from the sc-crawler GitHub repo, used at Docker build
  time when its version matches the installed `sparecores-crawler`, so rule changes no
  longer need a PyPI release. `/healthcheck` shows the source of the rules in
  `inspector_rules` (#109)

Fixes:

- TPUs left out of the `gpu_model` filter values, based on the new
  `Server.accelerator_type`, as the TPU model name patterns no longer matched the
  normalized model names (e.g. `v5e`) (#104)
- Correct `X-Total-Count` header for `/servers` with a non-default
  `best_price_allocation` and no filter for orderable servers (#101)
- CORS headers in early 401 and 429 responses, so browsers can read them (#102)
- No rate-limit credits charged and no tokens verified for CORS preflight requests
  (#102)
- No caching of 401 and 429 responses and rejected preflight responses (#102)

## September 2026

New features:

- `/table/vendor_compliance_link` table dump of the vendor and compliance framework
  mapping (#97)

Improvements:

- `architecture`, `cpu_allocation`, `network_speed_baseline_min`,
  `network_speed_max_min`, `network_storage_speed_baseline_min` and
  `network_storage_speed_max_min` filters on `/databases`, matched via the identified
  `Database.server_id` (#97)
- `cpu_flags` filter on `/servers` (#97)
- `cpu_hyperthreading` filter on `/servers` (vCPUs > CPU cores) (#97)
- `storage_count_min` filter on `/servers` for the minimum number of bundled storages
  (#98)
- `Authorization` header allowed in CORS requests (#94)
- Bearer tokens verified against several JWKS (#96)

## August 2026

Breaking changes:

- `/databases` drops the `engine` and `wire_protocol` filters, and replaces the
  `engine_versions` filter with `engine_version`, which takes one required major engine
  version (#90)

New features:

- `/ai/assist_database_filters` for natural-language database filter extraction (#90)
- Bearer tokens verified as JWTs (by JWKS) or static tokens, besides token
  introspection (#93)

Improvements:

- `/databases` benchmark filters (`benchmark_id`, by default `pgbench:heavy_read_only`,
  `benchmark_config`, `benchmark_score_min`, `benchmark_score_per_price_min`) and
  `selected_benchmark_score*` ordering (#90)

Fixes:

- `/benchmark_score_stats` counts server benchmark scores only, not database scores
  (#91)

## July 2026

New features:

- `/databases`, `/database/{vendor}/{database}`, `/database/{vendor}/{database}/prices`
  and `/database_storage_prices` endpoints for the managed database (DBaaS) data of
  `sparecores-crawler` (#89)
- `/table/database`, `/table/database_price` and `/table/database_storage` table dump
  endpoints (#89)
- `/databases` filters for the new fields of
  [`sparecores-crawler` 0.8.5](https://docs.sparecores.com/crawler/news#v085-july-31-2026)
  (`wire_protocol`, `max_read_replicas`, monitoring, `connection_pool`,
  `disk_encryption`, `security_features`, `autotuning_apply`, `ha_strategy`, and related
  capabilities) (#89)
- `ha` and `ha_strategy` as `database_price` primary keys: the minimum price is the
  cheapest HA row (#89)
- `BestDatabasePriceAllocation` for `/databases`, without `SPOT_ONLY`, which the schema
  rejects (#89)

## June 2026

Breaking changes:

- `Server.network_speed` renamed to `network_speed_baseline`
  ([`sparecores-crawler` 0.6.0](https://docs.sparecores.com/crawler/news#v060-june-3-2026))
- `network_speed_min` filter on `/servers` renamed to `network_speed_baseline_min` (#84)

New features:

- `/server/{vendor}/{server}/descriptions` endpoint that returns the
  `ServerDescription` of a single server (#86)

Improvements:

- New `Vendor` fields (`stopped_server_charged`, `billing_increment_seconds`,
  `minimum_billing_seconds` and `billing_comment`) and `Server` fields (`ecpus`,
  `scalability`, `hw_virt`, `memory_amount_actual`, `network_speed_max`,
  `network_storage_speed_baseline`, `network_storage_speed_max` and
  `average_time_to_start`)
  ([`sparecores-crawler` 0.6.0](https://docs.sparecores.com/crawler/news#v060-june-3-2026))
  (#80)
- `hw_virt` filter on `/servers` and `/server_prices` (#84)
- `network_speed_max_min`, `network_storage_speed_baseline_min` and
  `network_storage_speed_max_min` filters on `/servers` (#84)
- Clearer parameter titles and descriptions

Fixes:

- `/table/server/meta` categories for the new server columns (#82)

## May 2026

Fixes:

- Explicit request error when a table is ordered by a relation instead of a column

## April 2026

New features:

- `/table/server/select` endpoint that returns only the selected `Server` `columns`
  (#74)

Improvements:

- `extra_storage_size`, `extra_storage_type`, `monthly_inbound_traffic` and
  `monthly_outbound_traffic` query parameters on `/servers`. All `min_price` fields and
  the always present `price_breakdown` object include the traffic and storage costs,
  with tiered pricing, and with the built-in server storage deducted from
  `extra_storage_size` (#76)

Fixes:

- `Benchmark` `category` from the database instead of a hardcoded value (#77)
- `order_by` fields that match more than one column no longer rejected with 400 (#75)

## March 2026

New features:

- `/benchmark_score_stats` endpoint for benchmark config and score statistics (#66)

Improvements:

- `countries` and `vendor_regions` filters on `/server/{vendor}/{server}/prices` and
  `/server/{vendor}/{server}/similar_servers/{by}/{num}` (#62)
- `benchmark_score` and `benchmark_score_per_price` ordering options on
  `/server/{vendor}/{server}/similar_servers/{by}/{num}` (#62)
- `currency`, `benchmark_id`, `benchmark_config`, `server_region` and
  `best_price_allocation` query parameters on
  `/server/{vendor}/{server}/similar_servers/{by}/{num}` (#62)
- `network_speed_min`, `cpu_speed_min`, `cpu_l1d_cache_min`, `cpu_l1d_cache_total_min`,
  `cpu_l1i_cache_min`, `cpu_l1i_cache_total_min`, `cpu_l2_cache_min`,
  `cpu_l2_cache_total_min`, `cpu_l3_cache_min` and `cpu_l3_cache_total_min` filters on
  `/servers`, with predefined ranges (#63)
- Aggressive content caching with a multi-layer CDN
- Stale cached content served while it is revalidated in the background, and on
  temporary origin errors

Fixes:

- Expired in-memory rate-limit keys cleaned up by a background thread (#65)
- No content caching for error responses (#71)
- Sorted `/server/{vendor}/{server}/benchmarks` results (#69)
- `/table/server/meta` units for the new CPU cache columns (#67)

## February 2026

Breaking changes:

- Python 3.11 or later required, up from 3.8 (#52)

New features:

- `/stats` endpoint for database table statistics, with optional `vendor` and
  `only_active` filters (#53)
- `/debug` endpoint that shows which servers have benchmark scores (#54)

Improvements:

- Monthly prices for on-demand servers, with optional capping (#51, #55)
- Float GPU counts, for fractional GPU allocation
- Monthly price caps (calculated from tiered prices) in server and server price search
  results (#55)
- Country and region filter on `/servers` (#56)
- `vendor_regions` filter on all endpoints that support the `regions` filter, for more
  precise filtering (#56)
- `best_price_allocation` parameter on `/servers`, to control how the server's "best
  price" is computed (e.g. any price, spot only or on-demand only) (#58)
- `currency` parameter on `/servers`, to convert prices to the requested currency (#58)

Fixes:

- Tiered prices converted to the requested currency in `/server_prices` results (#55)
- Correct `X-Total-Count` header for `/servers` when ordered by price-derived fields
  without filtering for active servers (#56)
- Server prices without a related server score left out when ordering by score or
  `score_per_price` (#56, #57)
- Correct `price_max` calculation and currency check on `/server_prices` (#60)

## January 2026

New features:

- OAuth2 Bearer token authentication with configurable security schemes (#50)
- Token introspection for OAuth2 authentication (#50)
- Rate limiting with a Redis backend and an in-memory fallback (#50)
- Credit-based rate limiting, with configurable penalties for different response codes
  (#50)
- CEL (Common Expression Language) expressions for token validation and claim
  extraction
- User authentication and token source tracking
- Optional authentication for specific endpoints (#50)
- `/table/server_prices` table dump with filters, which requires authentication (#49)

Improvements:

- No caching of endpoints that require authentication (#50)
- Inactive server types left out of API responses

Fixes:

- Redis connection errors logged, with a fallback when Redis is unavailable (#50)
- Token active status checked in all authentication flows (#49)
- Self-reference issues in the authentication middleware (#50)
- 401 penalties applied by the rate-limit middleware (#49, #50)
- HTTP 400 for invalid currency codes (#49)
- Correct price currency in conversions (#49)
- Middleware order for authentication and rate limiting (#49, #50)
- Low-frequency and possibly incorrect GPU models left out

## October 2024

Breaking changes:

- v1 server details endpoint removed; use the v2 server details endpoints (#47)

New features:

- Currency table with live exchange rates (#38)
- Similar servers by score per price (#47)

Improvements:

- CPU allocation filter (shared or dedicated)
- Exchange rates updated from the ECB by a background thread (#38)
- Minimum server prices in `Server` objects (#47)
- Minimum `$Core` (score per price) filter in server searches (#47)
- `score_per_price` rounded to 4 digits (#47)

Fixes:

- Servers without a price treated as inactive (#47)
- Descending order on computed columns
- Servers without values hidden when filtering by related columns (#47)
- Green energy definition in sync with the crawler

## September 2024

New features:

- Storage prices endpoint with filters (#39)
- Traffic prices endpoint with monthly prices (#42)
- Similar servers endpoint based on hardware specifications (#33, #35)
- Server details v2 endpoints (#35)

Improvements:

- Tiered traffic prices (#42)
- Storage and traffic prices converted to the requested currency (#40, #42)
- Faster full-row lookups (#35)

Fixes:

- No duplicate rows from many-to-many relationships
- Consistent active price filtering across endpoints
- Correct tiered price calculation: only the delta of each tier is applied (#42)
- Outbound traffic prices by default (#43)

## August 2024

Improvements:

- Cache control headers for client-side caching
- Sentry headers allowed in CORS requests
- Page size limit on server price queries, with a lower default page size (#33, #44)
- Server data reloaded every 10 minutes
- Search for servers with over 200 CPUs (#33)

## June 2024

Breaking changes:

- Datacenter renamed to Region across the API (#22)
- Server `memory` field renamed to `memory_amount` (#22)
- Path parameters renamed from generic IDs to descriptive names (e.g. `vendor_id`,
  `server_id`) (#22)

New features:

- Benchmark scores endpoint with full benchmark data (#22)
- Benchmark table dump of all available benchmarks (#22)
- `SCore` (Spare Cores Score) in API responses (#26)
- Price per `SCore` (`$Core`), with filtering (#26)

Improvements:

- Server queries by the `api_reference` parameter (#22)
- Human-friendly column names with categories (#22)
- GPU total memory filter (#26)
- `SCore` and `$Core` in single server lookups (#26, #27)
- Clearer field descriptions (#22)

Fixes:

- Correct example JSON on `/benchmark_scores` (#22)
- Correct server ID references in subqueries (#22)
- Missing `SCore` values handled in calculations (#26, #27)
- No division by zero in price per score calculations (#26)
- No missing `KeyValue` errors for the lowest prices (#26)
- Missing price information handled (#28)

## May 2024

Breaking changes:

- `/servers` lists servers without price information; the previous `/servers` endpoint
  is now `/server_prices` (#21)

New features:

- AI assistant endpoints for natural-language queries, via OpenAI
- Table metadata endpoints for data exploration (#13)
- Healthcheck endpoint with a database check
- Similar servers endpoints, by hardware specifications and by family

Improvements:

- Currency parameter for server and price queries, with automatic conversion
- Freetext search on server IDs and names (#23)
- Units of measurement in API responses (#24)
- Endpoint categories in the API docs (#13)
- `X-Total-Count` header for pagination (#11)
- Request IDs in a custom header (#11, #12)
- Compliance framework filter with multiple frameworks (#11)
- Country filter for regions (#14)
- Vendor IDs as an enum (#11)
- Optional total count, for faster queries (#11, #12)
- Server filters: vendor, CPU, memory, storage, GPU, green energy, compliance frameworks
  (e.g. HIPAA, SOC2), architecture (e.g. `x86_64`, `arm64`), storage type, GPU
  manufacturer, family and model, and total GPU memory
- Consistent examples across all endpoints (#18)

Fixes:

- Enum values not keyed by name, to avoid serialization issues (#20)
- Filter conditions enforced in count queries
- `X-Total-Count` header exposed via CORS (#11)
- No duplicate servers in filtered results (#11)
- Correct OpenAPI (Swagger) syntax, where a dict was used instead of an array (#18)

## April 2024

New features:

- CORS support for cross-origin requests (#5, #7)
- Currency conversion with ECB rates (#7)
- Server listing with multiple filters (#11)

Improvements:

- Nested vendor data in responses (#6, #7)
- Green energy filter (#11)
- Architecture filter (#11)
- Query parameter metadata for frontend filters (#8)
- Response compression (#3)

Fixes:

- Database session caching issues (#7)
- Joins on overlapping compound foreign keys
- Last updated date tracking
- Filter values coerced to booleans (#8)
- Status filter applied (#8)

## March 2024

Fixes:

- Schema names in line with recent `sparecores-crawler` changes

## February 2024

New features:

- First release: a FastAPI application on the SQLite database of the `sc_data` package
- Database reloaded when the data is updated (#1)
- Server lookup by vendor and API ID
- Server search by CPU and memory
- Swagger (OpenAPI) documentation with examples and response models
