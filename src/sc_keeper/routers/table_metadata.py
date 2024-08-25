from fastapi import (
    APIRouter,
    Depends,
)
from sc_crawler.tables import Server
from sqlmodel import Session

from ..database import get_db
from ..references import ServerTableMetaData

router = APIRouter()


def _get_category(server_column_name: str) -> str:
    if server_column_name not in Server.get_columns()["all"]:
        raise KeyError("Unknown Server column name.")
    if server_column_name in [
        "vendor_id",
        "server_id",
        "name",
        "api_reference",
        "display_name",
        "description",
        "family",
        "status",
        "observed_at",
    ]:
        return "meta"
    if server_column_name in ["vcpus", "hypervisor"] or server_column_name.startswith(
        "cpu"
    ):
        return "cpu"
    if server_column_name.startswith("memory"):
        return "memory"
    if server_column_name.startswith("gpu"):
        return "gpu"
    if server_column_name.startswith("storage"):
        return "storage"
    if (
        server_column_name.endswith("_traffic")
        or server_column_name.startswith("network")
        or server_column_name == "ipv4"
    ):
        return "network"


def _get_name(server_column_name: str) -> str:
    # special cases
    mapping = {
        "vcpus": "vCPUs",
        "cpus": "CPUs",
        "gpus": "GPUs",
        "ipv4": "IPv4",
    }
    if server_column_name in mapping:
        return mapping[server_column_name]
    name = server_column_name.replace("_", " ").title()
    name = name.replace(" Id", " ID")
    name = name.replace("Api ", "API ")
    name = name.replace("Cpu ", "CPU ")
    name = name.replace("Gpu ", "GPU ")
    name = name.replace(" Ecc", " ECC")
    return name


def _get_unit(server_column_name: str) -> str:
    mapping = {
        "cpu_speed": "GHz",
        "cpu_l1_cache": "byte",
        "cpu_l2_cache": "byte",
        "cpu_l3_cache": "byte",
        "memory_amount": "MiB",
        "memory_speed": "Mhz",
        "gpu_memory_min": "MiB",
        "gpu_memory_total": "MiB",
        "storage_size": "GB",
        "network_speed": "Gbps",
        "inbound_traffic": "GB/month",
        "outbound_traffic": "GB/month",
    }
    if server_column_name in mapping:
        return mapping[server_column_name]
    return None


@router.get("/table/server/meta", tags=["Table metadata"])
def table_metadata_server(db: Session = Depends(get_db)) -> ServerTableMetaData:
    """Server table and column names and comments."""
    table = {
        "name": Server.get_table_name(),
        "description": Server.__doc__.splitlines()[0],
    }
    fields = [
        {
            "id": k,
            "name": _get_name(k),
            "description": v.description,
            "category": _get_category(k),
            "unit": _get_unit(k),
        }
        for k, v in Server.model_fields.items()
    ]
    return {"table": table, "fields": fields}
