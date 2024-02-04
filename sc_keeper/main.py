from contextlib import asynccontextmanager
from typing import List, Optional, Annotated

from fastapi import FastAPI, HTTPException, Query
from sc_crawler.schemas import Server, Price
from sc_data import Data
from sqlmodel import Session, SQLModel, create_engine, select

data = Data()
db = create_engine("sqlite:///" + str(data.db_path), echo=True)

## set one example for Swagger docs
with Session(db) as session:
    example_server = session.exec(select(Server).limit(1)).first()
Server.model_config["json_schema_extra"] = {"examples": [example_server.model_dump()]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: init DB
    SQLModel.metadata.create_all(db)
    yield
    # shutdown
    pass


app = FastAPI(lifespan=lifespan)


@app.get("/server/{server_id}")
def read_server(server_id: str) -> Server:
    with Session(db) as session:
        query = select(Server).where(Server.id == server_id)
        server = session.exec(query).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")
        return server


@app.get("/search")
def search_server(
    vcpus_min: Annotated[int, Query(description="Minimum number of virtual CPUs.")] = 1,
    price_max: Annotated[
        Optional[int], Query(description="Maximum price (USD/hr).")
    ] = None,
) -> List[Price]:
    with Session(db) as session:
        query = select(Price).join(Server)
        if vcpus_min:
            query = query.where(Server.vcpus >= vcpus_min)
        if price_max:
            query = query.where(Price.price <= price_max)
        servers = session.exec(query).all()
        return servers


## https://fastapi-filter.netlify.app/#examples
