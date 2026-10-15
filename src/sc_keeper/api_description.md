The Spare Cores Navigator API lets you programmatically explore cloud
resources across providers in a standardized format, covering pricing,
hardware specifications, benchmark performance, and cost-efficiency metrics.

It is designed for FinOps engineers, data scientists, and platform teams,
who prefer working with empirical and structured data rather than vendor
heuristics.

For more details, see <https://sparecores.com/about/navigator>.

If you prefer to explore the data visually before integrating the API, the
web interface exposes the same underlying dataset at
<https://sparecores.com/servers>.

## Getting Started

The base URL of the API is `https://keeper.sparecores.net`, and most endpoints
can be called without authentication. For example, list the 5 cheapest servers
with at least 8 vCPUs:

```shell
curl "https://keeper.sparecores.net/servers?vcpus_min=8&limit=5"
```

The search endpoints, such as `/servers` or `/server_prices`, share the
following query parameters:

- `limit` and `page` for pagination (`limit=-1` returns all results)
- `order_by` and `order_dir` for sorting, e.g. by `min_price` (the default
  of `/servers`)
- `add_total_count_header=true` to get the number of all matching results in
  the `X-Total-Count` response header

Invalid parameters return a 422 response, listing the problems in its
`detail` field. For the 429 and 503 responses, see Rate Limiting below.

The fields of most responses follow the database schema, described at
<https://docs.sparecores.com/schemas>.

## Open Source & Self-Hosting

The entire Navigator stack is open source and designed to be inspectable,
reproducible, and self-hostable:

- FastAPI implementation of this service:
  <https://github.com/SpareCores/sc-keeper>
- Database schemas and ETL tooling:
  <https://github.com/SpareCores/sc-crawler>
- Underlying data on cloud servers, regions, zones, storages and more:
  <https://github.com/SpareCores/sc-data-dumps>
- Raw hardware inspection and performance benchmarking logs:
  <https://github.com/SpareCores/sc-inspector-data>

Source code is licensed under **MPL-2.0**, raw data records under
**CC-BY-SA-4.0**, compiled SQLite database under **BSL 1.1**.

## Caching

Responses are served via reverse proxy and CDN with a 15-minute to 1-hour
cache TTL for most endpoints. For real-time access, lower-latency
requirements, or custom caching strategies, feel free to reach out -- we are
happy to discuss what works best for your use case.

## Rate Limiting

Credit-based rate limiting with a 1-minute sliding window:

- Default: 300 credits/minute
- Cost per request: 1 credit (standard) with some exceptions for heavier
  queries, e.g. `/servers` (3 credits) or `/server_prices` (5 credits)
- Tracking: per authenticated user or IP address
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Cost`, `X-RateLimit-Remaining`
- Status code returned in case of rate limit exceeded: 429, with a
  `Retry-After` header indicating the number of seconds to wait. If the cost
  of a single request is higher than the credit limit, the 429 response has
  no `Retry-After` (nor `X-RateLimit-Remaining`) header, as retrying will
  never succeed.

Furthermore, the number of concurrent heavy requests per worker might also
be limited to avoid overloading the serving cluster or database; in such
case, a 503 response will be returned.

Temporary errors (such as 429 or 503 status codes) should be retried with
exponential backoff, waiting at least the number of seconds in the
`Retry-After` header when present. A 429 without `Retry-After` should not be
retried.

The default limits are intended to support exploration and prototyping. If
you are building something larger, we are glad to help you scale access
responsibly.

## Authentication

Authentication is optional for most exploratory use cases, but required for:

- Higher rate limits
- Access to premium endpoints
- Commercial and partnership use cases

Tokens are issued on request. To authenticate, send your token in the
`Authorization` header of each request: `Authorization: Bearer <token>`. An
invalid token returns a 401 response.

## Fair Use & Commercial Use

This public API is provided under a fair use policy to ensure availability
for all users and to facilitate the evaluation of integrating the Navigator
data into your products.

Commercial use, high-volume access, open-source and integration partnerships
are welcome. If you are experimenting, building a prototype, or considering
deeper integration, we would love to hear what you are working on and help
you find the best setup.
