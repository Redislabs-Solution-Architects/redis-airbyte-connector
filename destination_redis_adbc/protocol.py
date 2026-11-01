"""Preserve state blobs exactly, including NULL-valued cursor entries."""

import json

from airbyte_cdk.models import AirbyteMessageSerializer, Type


def _blob(value):
    return value if isinstance(value, dict) else vars(value)


def validate_state(state):
    from source_redis_adbc.backend import ConnectorError

    if state is None or (state.data is None and state.stream is None and state.global_ is None):
        raise ConnectorError("Received a state without a checkpoint payload.")
    kind = state.type.value if state.type is not None else "LEGACY"
    if (
        (kind == "STREAM" and state.stream is None)
        or (kind == "GLOBAL" and state.global_ is None)
        or (kind == "LEGACY" and state.data is None)
    ):
        raise ConnectorError("Received an inconsistent checkpoint type and payload.")


def message_to_json(message):
    wire = AirbyteMessageSerializer.dump(message)
    if message.type == Type.STATE:
        state = message.state
        validate_state(state)
        if state.data is not None:
            wire["state"]["data"] = state.data
        if state.stream is not None:
            wire["state"]["stream"]["stream_state"] = _blob(state.stream.stream_state)
        if state.global_ is not None:
            if state.global_.shared_state is not None:
                wire["state"]["global"]["shared_state"] = _blob(state.global_.shared_state)
            for original, encoded in zip(state.global_.stream_states, wire["state"]["global"]["stream_states"]):
                encoded["stream_state"] = _blob(original.stream_state)
    return json.dumps(wire, allow_nan=False, ensure_ascii=False)
