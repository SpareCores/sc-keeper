from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from sc_crawler.schemas import Server
from sqlmodel import Session, SQLModel, create_engine, select

sqlite_file_name = "sc_crawler.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, echo=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: init DB
    SQLModel.metadata.create_all(engine)
    yield
    # shutdown
    pass


app = FastAPI(lifespan=lifespan)


@app.get("/server/{server_id}")
def read_server(server_id: str) -> Server:
    with Session(engine) as session:
        query = select(Server).where(Server.id == server_id)
        server = session.exec(query).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")
        return server


@app.get("/search")
def search_server(
    vcpus_min: Optional[int] = None, vcpus_max: Optional[int] = None
) -> List[Server]:
    with Session(engine) as session:
        query = select(Server)
        if vcpus_min:
            query = query.where(Server.vcpus >= vcpus_min)
        if vcpus_max:
            query = query.where(Server.vcpus <= vcpus_max)
        servers = session.exec(query).all()
        return servers


## https://fastapi-filter.netlify.app/#examples
