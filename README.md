## POC API using FastAPI/SQLModel

```bash
git clone git@github.com:SpareCores/sc-keeper.git
cd sc-keeper
pip install -e .
uvicorn sc_keeper.api:app --reload
```

To see the SQL queries run in the background, start the service with
the `KEEPER_DEBUG` env var set to any value.

## Useful debug links

- Swagger docs: http://localhost:8000/docs
- Server details example: http://localhost:8000/server/aws/p3.8xlarge
- Server search example: http://localhost:8000/search?vcpus_min=200&price_max=50&limit=5
