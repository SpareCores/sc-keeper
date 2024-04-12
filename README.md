## POC API using FastAPI/SQLModel

```bash
git clone git@github.com:SpareCores/sc-keeper.git
cd sc-keeper
pip install -e .
uvicorn sc_keeper.api:app --reload
```

## Useful debug links

- http://localhost:8000/docs
- http://localhost:8000/server/aws/p3.8xlarge
- http://localhost:8000/search?vcpus_min=200&price_max=50&limit=5
