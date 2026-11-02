#!/usr/bin/env python3
"""Airbyte records -> Redis destination -> Redis source -> Redis destination.

Requires REDIS_ENTERPRISE_CONFIG for a disposable TLS Enterprise database.
"""

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from e2e import docker, messages
from integration_config import enterprise_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", default="source-redis-adbc:0.1.0")
    parser.add_argument("--destination-image", default="destination-redis-adbc:0.1.0")
    parser.add_argument("--network", default=os.environ.get("REDIS_ENTERPRISE_NETWORK"))
    parser.add_argument("--tls-directory", type=Path)
    args = parser.parse_args()
    config = enterprise_config()
    token = uuid.uuid4().hex[:10]
    network = args.network or "airbyte-rw-" + token
    if not args.network:
        docker("network", "create", network)
    try:
        with tempfile.TemporaryDirectory(prefix="airbyte-rw-") as directory:
            folder = Path(directory)
            folder.chmod(0o755)
            dest = {**config, "schema": "airbyte_ingest_" + token}
            (folder / "destination.json").write_text(json.dumps(dest))
            props = {
                "id": {"type": "integer"},
                "amount": {"type": "string"},
                "note": {"type": ["null", "string"]},
                "binary": {"type": ["null", "string"], "contentEncoding": "base64"},
                "created": {
                    "type": ["null", "string"],
                    "format": "date-time",
                    "airbyte_type": "timestamp_without_timezone",
                },
                "items": {"type": ["null", "array"], "items": {"type": ["null", "integer"]}},
            }
            catalog = {
                "streams": [
                    {
                        "stream": {
                            "name": "orders",
                            "namespace": "upstream",
                            "json_schema": {"type": "object", "properties": props},
                            "supported_sync_modes": ["full_refresh"],
                        },
                        "sync_mode": "full_refresh",
                        "destination_sync_mode": "append",
                    }
                ]
            }
            (folder / "catalog.json").write_text(json.dumps(catalog))
            base = ["run", "--rm", "--network", network, "-v", f"{directory}:/config:ro"]

            if args.tls_directory:
                base.extend(["-v", f"{args.tls_directory.resolve()}:/enterprise-test:ro"])

            def write(wire, cat="catalog.json", config="destination.json", check=True):
                return docker(
                    *base,
                    "-i",
                    args.destination_image,
                    "write",
                    "--config",
                    "/config/" + config,
                    "--catalog",
                    "/config/" + cat,
                    stdin="".join(json.dumps(m) + "\n" for m in wire),
                    check=check,
                )

            def read(schema):
                (folder / "source.json").write_text(
                    json.dumps({**config, "schemas": [schema]})
                )
                discovery = messages(
                    docker(*base, args.source_image, "discover", "--config", "/config/source.json").stdout
                )
                streams = next(m["catalog"]["streams"] for m in discovery if m.get("type") == "CATALOG")
                selected = next(stream for stream in streams if stream["name"] == "orders")
                configured = {
                    "streams": [{"stream": selected, "sync_mode": "full_refresh", "destination_sync_mode": "overwrite"}]
                }
                (folder / "source_catalog.json").write_text(json.dumps(configured))
                output = docker(
                    *base,
                    args.source_image,
                    "read",
                    "--config",
                    "/config/source.json",
                    "--catalog",
                    "/config/source_catalog.json",
                )
                return configured, [m for m in messages(output.stdout) if m.get("type") == "RECORD"]

            status = messages(
                docker(*base, args.destination_image, "check", "--config", "/config/destination.json").stdout
            )
            assert any(m.get("connectionStatus", {}).get("status") == "SUCCEEDED" for m in status), status
            records = [
                {
                    "type": "RECORD",
                    "record": {
                        "stream": "orders",
                        "namespace": "upstream",
                        "emitted_at": 1,
                        "data": {
                            "id": n,
                            "amount": "12345678901234567890.123456789",
                            "note": None,
                            "binary": "AP8=",
                            "created": "2026-11-01T01:02:03.123456789",
                            "items": [1, None, 3],
                        },
                    },
                }
                for n in (9007199254740993, 2, 3, 4, 5)
            ]
            state = {
                "type": "STATE",
                "state": {
                    "type": "STREAM",
                    "stream": {
                        "stream_descriptor": {"name": "orders", "namespace": "upstream"},
                        "stream_state": {"cursor": "done", "null_cursor": None},
                    },
                },
            }
            output = write([*records, state])
            assert [m for m in messages(output.stdout) if m.get("type") == "STATE"] == [state]
            source_catalog, extracted = read(dest["schema"])
            by_id = {m["record"]["data"]["id"]: m["record"]["data"] for m in extracted}
            assert len(by_id) == 5
            assert by_id["9007199254740993"]["amount"] == "12345678901234567890.123456789"
            assert by_id["2"]["created"] == "2026-11-01T01:02:03.123456789"
            assert by_id["2"]["note"] is None
            assert by_id["2"]["items"] == ["1", None, "3"]

            # Feed the source's actual wire records and discovered schema to a second destination schema.
            copied = {**dest, "schema": "airbyte_copy_" + token}
            (folder / "copy.json").write_text(json.dumps(copied))
            (folder / "copy_catalog.json").write_text(json.dumps(source_catalog))
            global_state = {
                "type": "STATE",
                "state": {"type": "GLOBAL", "global": {"shared_state": {"nullable": None}, "stream_states": []}},
            }
            result = write([*extracted, global_state], cat="copy_catalog.json", config="copy.json")
            assert [m for m in messages(result.stdout) if m.get("type") == "STATE"] == [global_state]
            _, copied_records = read(copied["schema"])
            assert sorted((m["record"]["data"] for m in copied_records), key=lambda row: row["id"]) == sorted(
                by_id.values(), key=lambda row: row["id"]
            )
            print("PASS: destination -> source -> destination preserves all values and state blobs.", flush=True)

            # Failed staged Overwrite must not publish, acknowledge state or destroy existing data.
            catalog["streams"][0]["destination_sync_mode"] = "overwrite"
            (folder / "overwrite.json").write_text(json.dumps(catalog))
            broken = json.loads(json.dumps(records[0]))
            broken["record"]["data"]["id"] = "invalid-id"
            failed = write([*records[:2], state, broken], cat="overwrite.json", check=False)
            assert failed.returncode != 0
            assert not any(m.get("type") == "STATE" for m in messages(failed.stdout))
            _, after_failure = read(dest["schema"])
            assert sorted(m["record"]["data"]["id"] for m in after_failure) == sorted(by_id)
            retry = write([records[1], state], cat="overwrite.json")
            assert [m for m in messages(retry.stdout) if m.get("type") == "STATE"] == [state]
            assert len(read(dest["schema"])[1]) == 1
            write([state], cat="overwrite.json")
            assert read(dest["schema"])[1] == []
            print("PASS: failed Overwrite preserves old data; retry and empty Overwrite succeed.", flush=True)
    finally:
        cleanup = f'''import json
from contextlib import closing
from source_redis_adbc.backend import connect
with closing(connect(json.load(open('/enterprise-test/config.json')))) as c, closing(c.cursor()) as cur:
    cur.execute('DROP SCHEMA IF EXISTS airbyte_ingest_{token} CASCADE')
    cur.execute('DROP SCHEMA IF EXISTS airbyte_copy_{token} CASCADE')
'''
        if args.tls_directory:
            docker("run", "--rm", "--network", network, "-v", f"{args.tls_directory.resolve()}:/enterprise-test:ro",
                   "-i", "--entrypoint", "python", args.source_image, "-", stdin=cleanup, check=False)
        if not args.network:
            docker("network", "rm", network, check=False)


if __name__ == "__main__":
    main()
