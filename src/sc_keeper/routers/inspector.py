from collections import defaultdict
from typing import Dict, List

from fastapi import (
    APIRouter,
    Depends,
)
from pydantic import RootModel
from sc_crawler.table_fields import Status
from sc_crawler.tables import Database, Server
from sqlmodel import Session, select

from .. import parameters as options
from ..database import get_db
from ..helpers import get_database_base, get_server_base

router = APIRouter()


class InspectorTaskBlockReasonCodesResponse(RootModel[Dict[str, List[str]]]):
    """Inspector task name mapped to its block reason codes."""


@router.get("/server/{vendor}/{server}/task_block_reason_codes")
def server_task_block_reason_codes(
    server_args: options.server_args,
    tasks: options.inspector_tasks,
    db: Session = Depends(get_db),
) -> InspectorTaskBlockReasonCodesResponse:
    """Return block reason codes for the requested Server's inspector tasks."""
    server = get_server_base(server_args[0], server_args[1], db)
    return server.check_inspector_task_block_reasons(tasks)


@router.get("/database/{vendor}/{database}/task_block_reason_codes")
def database_task_block_reason_codes(
    database_args: options.database_args,
    tasks: options.inspector_tasks,
    db: Session = Depends(get_db),
) -> InspectorTaskBlockReasonCodesResponse:
    """Return block reason codes for the requested Database's inspector tasks."""
    database = get_database_base(database_args[0], database_args[1], db)
    return database.check_inspector_task_block_reasons(tasks)


@router.get("/servers/task_block_reason_codes")
def servers_task_block_reason_codes(
    tasks: options.inspector_tasks,
    db: Session = Depends(get_db),
) -> Dict[str, Dict[str, InspectorTaskBlockReasonCodesResponse]]:
    """Return block reason codes for every active Server, nested by vendor ID then server API reference."""
    server_rows = db.exec(select(Server).where(Server.status == Status.ACTIVE)).all()
    by_vendor: Dict[str, Dict[str, InspectorTaskBlockReasonCodesResponse]] = (
        defaultdict(dict)
    )
    for server in server_rows:
        by_vendor[server.vendor_id][server.api_reference] = (
            server.check_inspector_task_block_reasons(tasks)
        )
    return by_vendor


@router.get("/databases/task_block_reason_codes")
def databases_task_block_reason_codes(
    tasks: options.inspector_tasks,
    db: Session = Depends(get_db),
) -> Dict[str, Dict[str, InspectorTaskBlockReasonCodesResponse]]:
    """Return block reason codes for every active Database, nested by vendor ID then database API reference."""
    database_rows = db.exec(
        select(Database).where(Database.status == Status.ACTIVE)
    ).all()
    by_vendor: Dict[str, Dict[str, InspectorTaskBlockReasonCodesResponse]] = (
        defaultdict(dict)
    )
    for database in database_rows:
        by_vendor[database.vendor_id][database.api_reference] = (
            database.check_inspector_task_block_reasons(tasks)
        )
    return by_vendor
