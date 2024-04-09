from contextlib import asynccontextmanager
from typing import List, Optional, Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from sc_crawler.tables import Server, ServerPrice
from sqlmodel import Session, select
from .database import session


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # set one example for Swagger docs
    db = next(get_db())
    example_server = db.exec(select(Server).limit(1)).one()
    Server.model_config["json_schema_extra"] = {
        "examples": [example_server.model_dump()]
    }
    yield
    # shutdown
    pass


app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)


@app.get("/server/{server_id}")
def read_server(server_id: str, db: Session = Depends(get_db)) -> Server:
    server = db.query(Server).filter(Server.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@app.get("/search")
def search_server(
    vcpus_min: Annotated[int, Query(description="Minimum number of virtual CPUs.")] = 1,
    price_max: Annotated[
        Optional[float], Query(description="Maximum price (USD/hr).")
    ] = None,
    db: Session = Depends(get_db),
) -> List[ServerPrice]:
    query = (
        select(ServerPrice)
        .join(ServerPrice.vendor)
        .join(ServerPrice.datacenter)
        .join(ServerPrice.zone)
        .join(ServerPrice.server)
    )
    if vcpus_min:
        query = query.where(Server.vcpus >= vcpus_min)
    if price_max:
        query = query.where(ServerPrice.price <= price_max)
    servers = db.exec(query).all()
    return servers


## https://fastapi-filter.netlify.app/#examples
