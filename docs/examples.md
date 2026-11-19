# Examples

Example requests to the Spare Cores Navigator API, from searching servers to
ranking them by a benchmark and looking up one server's prices. Most endpoints
need no authentication, so you can run these as they are. The
[API reference](https://docs.sparecores.com/api) describes every endpoint and
parameter, and its built-in API client sends the requests from your browser.

## Search servers

The cheapest server types with at least 8 vCPUs and 32 GB of memory, by their
on-demand price:

```shell
curl -G https://keeper.sparecores.net/servers \
  -d vcpus_min=8 \
  -d memory_min=32 \
  -d best_price_allocation=ONDEMAND_ONLY \
  -d limit=3
```

Each server type comes with its specs, its lowest prices (`min_price`), and its
benchmark scores, e.g. its SCore (`score`), the multi-core CPU speed, and its
SCore per price (`score_per_price`). The results are sorted by `min_price` by
default; set `order_by` and `order_dir` for another order.

[`/servers`](https://docs.sparecores.com/api/reference/search-servers-servers-get)
has filters for almost every column, such as `vendor`, `regions`, `gpu_min`
or `price_max`.

## Page through the results

`limit` sets the number of results (25 by default, -1 for all), and `page` the
page. `add_total_count_header=true` returns the number of all matching results
in the `X-Total-Count` header:

```shell
curl -G -D - https://keeper.sparecores.net/servers \
  -d vcpus_min=8 \
  -d memory_min=32 \
  -d limit=3 \
  -d page=2 \
  -d add_total_count_header=true
```

## Prices: spot or on-demand, and currency

`best_price_allocation` sets which prices `min_price` uses: `ANY` (the
default), `SPOT_ONLY`, `ONDEMAND_ONLY`, or `MONTHLY` for the monthly on-demand
price. Prices are hourly, in USD by default; `currency` converts them, e.g.
the cheapest spot prices in EUR:

```shell
curl -G https://keeper.sparecores.net/servers \
  -d vcpus_min=8 \
  -d memory_min=32 \
  -d best_price_allocation=SPOT_ONLY \
  -d currency=EUR \
  -d limit=3
```

The API converts the prices of all vendors to the same currency, so you can
compare vendors that price in USD and in EUR, which the
[SQLite database](https://docs.sparecores.com/sqlite) alone can't.

## Rank servers by a benchmark

`benchmark_id` selects a benchmark to rank the servers by, instead of the
SCore. For example, to look for a fast single core, with PassMark's
[CPU Single Threaded Test](https://docs.sparecores.com/benchmarks/passmark#passmark-cpu_single_threaded_test),
highest score first:

```shell
curl -G https://keeper.sparecores.net/servers \
  -d vcpus_min=8 \
  -d memory_min=32 \
  -d best_price_allocation=ONDEMAND_ONLY \
  -d benchmark_id=passmark:cpu_single_threaded_test \
  -d order_by=selected_benchmark_score \
  -d order_dir=desc \
  -d limit=3
```

Each server then has the score of that benchmark in
`selected_benchmark_score`, and its score per price in
`selected_benchmark_score_per_price`. The
[Benchmarks](https://docs.sparecores.com/benchmarks) list the IDs of all
benchmarks.

## Rank servers by cost-efficiency

Some benchmarks have a configuration, e.g. the model of the LLM inference
speed benchmark. `benchmark_config` selects one, as JSON. For example, the
servers that generate text with Llama 3.3 70B at the lowest cost, by the
tokens generated per second per hourly price:

```shell
curl -G https://keeper.sparecores.net/servers \
  -d benchmark_id=llm_speed:text_generation \
  --data-urlencode 'benchmark_config={"model": "Llama-3.3-70B-Instruct-Q4_K_M.gguf", "tokens": 128}' \
  -d best_price_allocation=ONDEMAND_ONLY \
  -d order_by=selected_benchmark_score_per_price \
  -d order_dir=desc \
  -d limit=10
```

The [LLM speed](https://docs.sparecores.com/benchmarks/llm_speed) benchmark
lists the models and token counts measured.

## Look up a server

The details of one server type, by its vendor and server ID, e.g. the AWS
t4g.2xlarge:

```shell
curl https://keeper.sparecores.net/v2/server/aws/t4g.2xlarge
```

Its prices in each region and zone, e.g. only in Germany and in EUR:

```shell
curl -G https://keeper.sparecores.net/server/aws/t4g.2xlarge/prices \
  -d countries=DE \
  -d currency=EUR
```

Its benchmark scores:

```shell
curl https://keeper.sparecores.net/server/aws/t4g.2xlarge/benchmarks
```

And the 5 server types most similar to it, here by their SCore per price, also
at other vendors:

```shell
curl https://keeper.sparecores.net/server/aws/t4g.2xlarge/similar_servers/score_per_price/5
```

The other methods are `family` (the other sizes of the same series),
`specs`, `score`, and `benchmark_score` or `benchmark_score_per_price` with
`benchmark_id`. See
[Get Similar Servers](https://docs.sparecores.com/api/reference/get-similar-servers-server-vendor-server-similar-servers-by-num-get).

## In Python

The same search with [Requests](https://requests.readthedocs.io/):

```python repl
import requests

response = requests.get(
    "https://keeper.sparecores.net/servers",
    params={
        "vcpus_min": 8,
        "memory_min": 32,
        "best_price_allocation": "ONDEMAND_ONLY",
        "limit": 5,
    },
    timeout=30,
)
response.raise_for_status()
for server in response.json():
    print(server["vendor_id"], server["api_reference"], server["min_price"])
```

## Limits

The API is free to use under a fair use policy, with rate limits per IP
address or user. Retry a 429 or 503 response after the seconds in its
`Retry-After` header, when it has one. See
[Rate Limiting](https://docs.sparecores.com/api#rate-limiting) and
[Fair Use & Commercial Use](https://docs.sparecores.com/api#fair-use--commercial-use).
