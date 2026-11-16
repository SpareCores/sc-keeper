from enum import Enum, StrEnum

from sc_keeper.openapi_extras import add_enum_descriptions, enum_member_docs


class Color(str, Enum):
    """Colors."""

    RED = "red"
    """The color of
    fire."""
    GREEN = "green"
    BLUE: str = "blue"
    """The color of the sky."""

    def describe(self) -> str:
        """Not a member."""
        return self.value


class Size(Enum):
    SMALL = 1
    """Small."""
    LARGE = 2


Dynamic = StrEnum("Dynamic", {"A": "a"})


def test_enum_member_docs():
    assert enum_member_docs(Color) == {
        "RED": "The color of\nfire.",
        "BLUE": "The color of the sky.",
    }


def test_enum_member_docs_without_source():
    assert enum_member_docs(Dynamic) == {}


def test_add_enum_descriptions():
    spec = {
        "components": {
            "schemas": {
                "Color": {"type": "string", "enum": ["red", "green", "blue"]},
                "Size": {"type": "integer", "enum": [1, 2]},
                "Dynamic": {"type": "string", "enum": ["a"]},
                "Object": {"type": "object", "properties": {}},
            }
        }
    }
    schemas = add_enum_descriptions(spec)["components"]["schemas"]
    assert schemas["Color"]["x-enumDescriptions"] == {
        "red": "The color of\nfire.",
        "blue": "The color of the sky.",
    }
    # keys are strings in JSON
    assert schemas["Size"]["x-enumDescriptions"] == {"1": "Small."}
    assert "x-enumDescriptions" not in schemas["Dynamic"]
    assert "x-enumDescriptions" not in schemas["Object"]


def test_add_enum_descriptions_needs_matching_values():
    # e.g. another enum with the same name
    spec = {"components": {"schemas": {"Color": {"enum": ["red", "green"]}}}}
    schemas = add_enum_descriptions(spec)["components"]["schemas"]
    assert "x-enumDescriptions" not in schemas["Color"]
