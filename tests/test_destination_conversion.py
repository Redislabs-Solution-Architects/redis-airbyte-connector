import pyarrow as pa
import pytest

from destination_redis_adbc.conversion import RecordConverter
from source_redis_adbc.backend import ConnectorError


def converter(properties, required=None):
    return RecordConverter({"type": "object", "properties": properties, "required": required or []})


def test_typed_scalars_and_lossless_strings():
    c = converter(
        {
            "id": {"type": "integer"},
            "precise": {"type": "string"},
            "binary": {"type": ["null", "string"], "contentEncoding": "base64"},
            "flag": {"type": "boolean"},
            "number": {"type": "number"},
        }
    )
    values = {
        "id": 2**63 - 1,
        "precise": "12345678901234567890.123456789",
        "binary": "AP8=",
        "flag": True,
        "number": 1.25,
    }
    converted = c.record(values)
    assert converted["binary"] == b"\x00\xff"
    batch = pa.RecordBatch.from_pylist([converted], schema=c.arrow_schema)
    assert batch.to_pylist()[0] == {**values, "binary": b"\x00\xff"}


def test_nested_objects_arrays_and_missing_optional_fields():
    c = converter(
        {
            "nested": {"type": "object", "properties": {"count": {"type": "integer"}}},
            "ids": {"type": "array", "items": {"type": ["null", "integer"]}},
            "missing": {"type": "string"},
        }
    )
    assert c.record({"nested": {"count": 7}, "ids": [1, None]}) == {
        "nested": {"count": 7},
        "ids": [1, None],
        "missing": None,
    }


@pytest.mark.parametrize(
    "fmt,airbyte_type,value,expected",
    [
        ("date", None, "2026-11-01", "2026-11-01"),
        ("time", None, "01:02:03.123456789", "01:02:03.123456789"),
        ("date-time", "timestamp_without_timezone", "2026-11-01T01:02:03.123456789", "2026-11-01 01:02:03.123456789"),
        (
            "date-time",
            "timestamp_with_timezone",
            "2026-11-01T01:02:03.123456789+00:00",
            "2026-11-01 01:02:03.123456789+00:00",
        ),
    ],
)
def test_temporal_conversion_preserves_nanoseconds(fmt, airbyte_type, value, expected):
    prop = {"type": "string", "format": fmt}
    if airbyte_type:
        prop["airbyte_type"] = airbyte_type
    c = converter({"value": prop})
    row = c.record({"value": value})
    batch = pa.RecordBatch.from_pylist([row], schema=c.arrow_schema)
    scalar = batch.column(0)[0]
    assert (scalar.cast(pa.string()).as_py() if fmt == "time" else str(scalar)) == expected


@pytest.mark.parametrize(
    "properties",
    [
        {"bad": {}},
        {"bad": {"type": ["string", "integer"]}},
        {"bad": {"type": "array"}},
        {"bad": {"type": "object"}},
        {"__key": {"type": "string"}},
    ],
)
def test_ambiguous_schema_rejected(properties):
    with pytest.raises(ConnectorError):
        converter(properties)


@pytest.mark.parametrize("data", [{"id": True}, {"id": "1"}, {"id": 2**63}, {"id": 1, "extra": "secret"}, {"id": None}])
def test_bad_integer_and_unknown_data_fail_safely(data):
    c = converter({"id": {"type": "integer"}}, ["id"])
    with pytest.raises(ConnectorError) as exc:
        c.record(data)
    assert "secret" not in str(exc.value)


def test_invalid_binary_and_missing_required_fail():
    c = converter({"value": {"type": "string", "contentEncoding": "base64"}}, ["value"])
    for data in ({}, {"value": "invalid!!"}):
        with pytest.raises(ConnectorError):
            c.record(data)


def test_large_number_cannot_be_silently_rounded():
    c = converter({"value": {"type": "number"}})
    with pytest.raises(ConnectorError, match="precision"):
        c.record({"value": 2**60 + 1})
