import json
import logging
import os

import requests
from fastapi.openapi.utils import get_openapi


def get_swagger():
    """Generate OpenAPI/Swagger JSON for current FastAPI app."""
    from .api import app

    return get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )


def build_json_schema(d: dict) -> dict:
    """Build JSON schema from an OpenAPI/Swagger parameter."""
    root_keys = {k: d[k] for k in ["description"] if k in d}
    schema_keys = {
        k: d["schema"][k]
        for k in ["type", "minimum", "maximum", "unit", "enum"]
        if k in d["schema"]
    }
    # extract expected type of optionals
    if "anyOf" in d["schema"] and len(d["schema"]["anyOf"]) == 2:
        if d["schema"]["anyOf"][1]["type"] == "null":
            if "type" in d["schema"]["anyOf"][0]:
                schema_keys["type"] = d["schema"]["anyOf"][0]["type"]
            else:
                # custom object reference
                schema_keys["type"] = "string"
    return root_keys | schema_keys


def convert_swagger_to_json_schema(swagger: dict) -> dict:
    """Filter Swagger JSON for the parameters and format as JSON schema."""
    return {
        item["name"]: build_json_schema(item)
        for item in swagger["paths"]["/servers"]["get"]["parameters"]
        if (
            item["in"] == "query"
            and item["name"]
            not in ["limit", "page", "order_by", "order_dir", "add_total_count_header"]
        )
    }


def openai_extract_filters(prompt):
    """Ask ChatGPT to generate filter JSON based on freetext input."""

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
        }
    except KeyError as exc:
        raise RuntimeError(
            "No OpenAI key found, which is required for this task."
        ) from exc

    print(convert_swagger_to_json_schema(get_swagger()))

    json_data = {
        "model": "gpt-3.5-turbo",
        "response_format": {"type": "json_object"},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_servers",
                    "description": "Search server instances accross cloud vendors using the provided filters.",
                    "parameters": {
                        "type": "object",
                        "properties": convert_swagger_to_json_schema(get_swagger()),
                        "required": [],
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "search_servers"}},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a cloud server search assistant, "
                    "helping users to find the optimal instances accross cloud providers. "
                    "The user describes their needs in plain English (or anoother natural language), "
                    "and you need to understand what kind of server is required to accomplish the task, "
                    "and generate a JSON describing the filters (e.g. number of CPUs or memory). "
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.7,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions", headers=headers, json=json_data
    )
    logging.debug(response.json())
    try:
        message = response.json()["choices"][0]["message"]
        args = message["tool_calls"][0]["function"]["arguments"]
        return json.loads(args)
    except Exception as exc:
        logging.exception(exc)
        raise exc
