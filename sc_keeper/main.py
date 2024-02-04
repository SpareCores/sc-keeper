from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from sc_crawler.schemas import Server
from sc_data import Data
from sqlmodel import Session, SQLModel, create_engine, select

data = Data()
db = create_engine("sqlite:///" + str(data.db_path), echo=True)


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
    vcpus_min: Optional[int] = None, vcpus_max: Optional[int] = None
) -> List[Server]:
    with Session(db) as session:
        query = select(Server)
        if vcpus_min:
            query = query.where(Server.vcpus >= vcpus_min)
        if vcpus_max:
            query = query.where(Server.vcpus <= vcpus_max)
        servers = session.exec(query).all()
        return servers


## https://fastapi-filter.netlify.app/#examples
