from fastapi import HTTPException
from sc_crawler.table_bases import ServerBase
from sc_crawler.tables import Server
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, select


def get_server_base(vendor: str, server: str, db: Session) -> ServerBase:
    try:
        return db.exec(
            select(Server)
            .where(Server.vendor_id == vendor)
            .where((Server.server_id == server) | (Server.api_reference == server))
            .join(Server.vendor)
            .options(contains_eager(Server.vendor))
        ).one()
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Server not found")
