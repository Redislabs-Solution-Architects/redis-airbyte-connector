"""Strict JSON-schema mapping for typed ADBC bulk ingestion."""

import base64
import re
from dataclasses import dataclass
from datetime import date

import jsonschema
import pyarrow as pa

from source_redis_adbc.backend import ConnectorError


@dataclass
class Column:
    name: str
    schema: dict
    field: pa.Field
    kind: str
    children: list


def _kind(schema):
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) for k in kinds):
        raise ConnectorError("Every destination field needs an explicit, unambiguous JSON Schema type.")
    concrete = set(kinds) - {"null"}
    if len(concrete) > 1:
        raise ConnectorError("Destination fields with multiple non-null types are unsupported.")
    return next(iter(concrete), "null"), "null" in kinds


def column(name, schema, optional=False, depth=0):
    if depth > 32 or not name or name == "__key":
        raise ConnectorError("Invalid, reserved or excessively nested destination field.")
    kind, nullable = _kind(schema)
    children = []
    if kind == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise ConnectorError(
                "Destination objects require explicit, nonempty properties; dynamic objects are unsupported."
            )
        required = schema.get("required", [])
        children = [column(key, value, key not in required, depth + 1) for key, value in properties.items()]
        dtype = pa.struct([child.field for child in children])
    elif kind == "array":
        if not isinstance(schema.get("items"), dict):
            raise ConnectorError("Destination arrays require a single explicit item schema.")
        children = [column("item", schema["items"], depth=depth + 1)]
        dtype = pa.list_(children[0].field)
    elif kind == "integer":
        dtype = pa.int64()
    elif kind == "number":
        dtype = pa.float64()
    elif kind == "boolean":
        dtype = pa.bool_()
    elif kind in ("string", "null"):
        fmt = schema.get("format")
        if schema.get("contentEncoding") == "base64":
            dtype = pa.binary()
        elif fmt == "date":
            dtype = pa.date32()
        elif fmt == "time":
            dtype = pa.time64("ns")
        elif fmt == "date-time":
            tz = None if schema.get("airbyte_type") == "timestamp_without_timezone" else "UTC"
            dtype = pa.timestamp("ns", tz=tz)
        else:
            dtype = pa.string()
    else:
        raise ConnectorError("Unsupported destination JSON Schema type.")
    return Column(name, schema, pa.field(name, dtype, nullable=nullable or optional or kind == "null"), kind, children)


class RecordConverter:
    def __init__(self, schema):
        try:
            jsonschema.Draft7Validator.check_schema(schema)
            self.root = column("record", schema)
            if self.root.kind != "object":
                raise ConnectorError("Destination stream schemas must be objects with explicit properties.")
            self.validator = jsonschema.Draft7Validator(schema)
            self.arrow_schema = pa.schema([c.field for c in self.root.children])
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("Invalid destination stream schema.") from None

    def record(self, value):
        try:
            self.validator.validate(value)
            return _convert(self.root, value)
        except ConnectorError:
            raise
        except jsonschema.ValidationError as error:
            raise ConnectorError(
                "A record does not match the destination schema "
                f"({error.validator} constraint at {list(error.absolute_path)}; "
                f"received {type(error.instance).__name__})."
            ) from None
        except Exception:
            # ValidationError and Arrow exceptions can contain record values.
            raise ConnectorError(
                "A record does not match the destination schema or cannot be represented without truncation."
            ) from None


def _convert(col, value):
    if value is None:
        return None
    if col.kind == "object":
        known = {child.name for child in col.children}
        if set(value) - known:
            raise ConnectorError("A record contains fields absent from the configured schema; refresh the catalog.")
        return {child.name: _convert(child, value.get(child.name)) for child in col.children}
    if col.kind == "array":
        return [_convert(col.children[0], item) for item in value]
    if col.kind == "integer":
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise ConnectorError(
                "Destination integers must fit the signed 64-bit range; use strings for larger exact values."
            )
    if col.kind == "number":
        import math

        if not math.isfinite(value):
            raise ConnectorError("Non-finite destination numbers are unsupported.")
        if isinstance(value, int) and float(value) != value:
            raise ConnectorError("A number would lose precision as float64; use an exact string field.")
    if pa.types.is_binary(col.field.type):
        return base64.b64decode(value, validate=True)
    if pa.types.is_date(col.field.type):
        return date.fromisoformat(value)
    if pa.types.is_time(col.field.type):
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?", value)
        if not match:
            raise ConnectorError("Destination times must be ISO local times with at most nanosecond precision.")
        hour, minute, second = map(int, match.group(1, 2, 3))
        if hour >= 24 or minute >= 60 or second >= 60:
            raise ConnectorError("Destination time is outside the supported clock range.")
        nanos = int((match.group(4) or "").ljust(9, "0"))
        return pa.scalar(((hour * 60 + minute) * 60 + second) * 1_000_000_000 + nanos, type=col.field.type)
    if pa.types.is_timestamp(col.field.type):
        # Arrow parses and preserves nanoseconds; Python datetime would truncate.
        return pa.scalar(value).cast(col.field.type, safe=True)
    return value
