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

To use Sentry, set the `SENTRY_DSN` environment variable.

## Useful debug links

- Swagger docs: http://localhost:8000/docs
- Server details example: http://localhost:8000/server/aws/p3.8xlarge
- Server search example: http://localhost:8000/servers?vcpus_min=2&memory_min=8&limit=5
