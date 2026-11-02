#!/usr/bin/env python3
"""Redis Enterprise over TLS -> source CLI -> Airbyte destination -> PostgreSQL.

Requires REDIS_ENTERPRISE_CONFIG, Python 3.11+, Docker, and a built source image.
The Enterprise cluster must be disposable and reachable from Docker containers.
PostgreSQL and the private network are removed, including after failure.
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from integration_config import enterprise_config


POSTGRES = "postgres:16.10"
DESTINATION = "airbyte/destination-postgres:3.0.23@sha256:1a122ed76f97b45fcdf7a8183bed93330a8f8eedad365e883d738774a35d69ea"


def docker(*args, stdin=None, check=True):
    result = subprocess.run(["docker", *args], input=stdin, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"Docker command {args[:2]} failed:\n{result.stdout[-6000:]}\n{result.stderr[-2000:]}")
    return result


def wait_for(description, predicate, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (TimeoutError, ValueError, KeyError):
            pass
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {description}")


def messages(output):
    result = []
    for line in output.splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            continue  # Destination JVM startup diagnostics are not protocol data.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", default="source-redis-adbc:0.1.2")
    parser.add_argument("--network", default=os.environ.get("REDIS_ENTERPRISE_NETWORK"),
                        help="Existing Docker network containing the Enterprise cluster")
    parser.add_argument("--tls-directory", type=Path, help="Mount test CA files at /enterprise-test")
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:10]
    network = args.network or "airbyte-e2e-" + suffix
    postgres = network + "-postgres"
    source = args.source_image
    config = enterprise_config()
    table = "airbyte_e2e_" + suffix
    empty = table + "_empty"
    if not args.network:
        docker("network", "create", network)
    try:
        docker("run", "-d", "--name", postgres, "--network", network,
               "-e", "POSTGRES_PASSWORD=airbyte-test", "-e", "POSTGRES_DB=airbyte", POSTGRES)
        wait_for("PostgreSQL", lambda: docker("exec", postgres, "pg_isready", "-U", "postgres", check=False).returncode == 0)
        with tempfile.TemporaryDirectory(prefix="airbyte-e2e-") as folder:
            config_dir = Path(folder)
            # Test-only credentials/configs, removed when the test finishes.
            config_dir.chmod(0o755)  # Source runs as an unprivileged container user.
            (config_dir / "source.json").write_text(json.dumps(config))
            (config_dir / "destination.json").write_text(json.dumps({"host": postgres, "port": 5432,
                "database": "airbyte", "schema": "public", "username": "postgres", "password": "airbyte-test",
                "ssl_mode": {"mode": "disable"}, "tunnel_method": {"tunnel_method": "NO_TUNNEL"}}))
            base = ["run", "--rm", "--network", network, "-v", f"{folder}:/config:ro"]

            if args.tls_directory:
                base.extend(["-v", f"{args.tls_directory.resolve()}:/enterprise-test:ro"])

            def run_source(*command, check=True):
                return docker(*base, source, *command, check=check)

            assert any(m.get("type") == "SPEC" for m in messages(run_source("spec").stdout))
            seed = f'''import json
from contextlib import closing
from source_redis_adbc.backend import connect
with closing(connect(json.load(open('/config/source.json')))) as c, closing(c.cursor()) as cur:
    cur.execute('CREATE TABLE {table} (id BIGINT, label VARCHAR, amount DECIMAL(30,9), active BOOLEAN, payload BYTEA, created TIMESTAMP)')
    cur.execute("INSERT INTO {table} VALUES (9007199254740993, 'first', 12345678901234567890.123456789, TRUE, X'00FF', TIMESTAMP '2026-11-01 01:02:03.123456'), (2, NULL, NULL, FALSE, NULL, NULL), (3, 'third', 1.000000001, TRUE, X'AB', TIMESTAMP '2026-11-01 00:00:00'), (4, 'fourth', 0, NULL, NULL, NULL), (5, 'fifth', -0.000000001, FALSE, NULL, NULL)")
    cur.execute('CREATE TABLE {empty} (id BIGINT)')
'''
            cleanup = f'''import json
from contextlib import closing
from source_redis_adbc.backend import connect
with closing(connect(json.load(open('/config/source.json')))) as c, closing(c.cursor()) as cur:
    cur.execute('DROP TABLE IF EXISTS {table}')
    cur.execute('DROP TABLE IF EXISTS {empty}')
'''
            try:
                docker(*base, "-i", "--entrypoint", "python", source, "-", stdin=seed)
                status = messages(run_source("check", "--config", "/config/source.json").stdout)
                assert any(m.get("connectionStatus", {}).get("status") == "SUCCEEDED" for m in status), status
                discovered = next(m["catalog"]["streams"] for m in messages(run_source("discover", "--config", "/config/source.json").stdout) if m.get("type") == "CATALOG")
                configured = {"streams": [{"stream": stream, "sync_mode": "full_refresh", "destination_sync_mode": "overwrite",
                    "generation_id": 1, "minimum_generation_id": 1, "sync_id": 1}
                    for stream in discovered if stream["name"] in (table, empty)]}
                assert len(configured["streams"]) == 2
                (config_dir / "catalog.json").write_text(json.dumps(configured))

                # Invalid selected stream must fail; retry uses a fresh full refresh.
                invalid = json.loads(json.dumps(configured))
                invalid["streams"][0]["stream"]["name"] = "missing_airbyte_table"
                (config_dir / "invalid.json").write_text(json.dumps(invalid))
                failed = run_source("read", "--config", "/config/source.json", "--catalog", "/config/invalid.json", check=False)
                assert failed.returncode != 0
                assert not any(m.get("type") == "RECORD" for m in messages(failed.stdout))

                # A failed native connection must not leak URI credentials to stdout/stderr.
                secret = "must-not-appear-in-diagnostics"
                (config_dir / "bad.json").write_text(json.dumps({**config, "uri": f"rediss://integration:{secret}@" + config["uri"].rsplit("@", 1)[1]}))
                bad = run_source("check", "--config", "/config/bad.json", check=False)
                assert secret not in bad.stdout + bad.stderr
                assert any(m.get("connectionStatus", {}).get("status") == "FAILED" for m in messages(bad.stdout))

                checked = docker(*base, DESTINATION, "check", "--config", "/config/destination.json")
                assert any(m.get("connectionStatus", {}).get("status") == "SUCCEEDED" for m in messages(checked.stdout)), checked.stdout[-2000:]

                for attempt in range(2):
                    # The Airbyte platform supplies these fields in real jobs.
                    # Advance the generation for a new overwrite to discard old rows.
                    for stream in configured["streams"]:
                        stream.update(generation_id=attempt + 1, minimum_generation_id=attempt + 1, sync_id=attempt + 1)
                    (config_dir / "catalog.json").write_text(json.dumps(configured))
                    output = run_source("read", "--config", "/config/source.json", "--catalog", "/config/catalog.json").stdout
                    records = [m for m in messages(output) if m.get("type") == "RECORD"]
                    assert len(records) == 5
                    data = {m["record"]["data"]["id"]: m["record"]["data"] for m in records}
                    assert data["9007199254740993"]["amount"] == "12345678901234567890.123456789"
                    assert data["9007199254740993"]["payload"] == "AP8="
                    assert data["9007199254740993"]["created"] == "2026-11-01T01:02:03.123456"
                    assert data["2"]["label"] is None
                    wire = "".join(json.dumps(record) + "\n" for record in records)
                    write = docker(*base, "-i", DESTINATION, "write", "--config", "/config/destination.json", "--catalog", "/config/catalog.json", stdin=wire)
                    assert not any(m.get("type") == "TRACE" and m.get("trace", {}).get("type") == "ERROR" for m in messages(write.stdout)), write.stdout[-6000:]
                    # Verify final typed destination data, not merely a zero exit code.
                    query = f"""SELECT COALESCE(json_agg(t ORDER BY id), '[]'::json)
                        FROM (SELECT id, label, amount, active, payload,
                        to_char(created, 'YYYY-MM-DD\"T\"HH24:MI:SS.US') AS created FROM public.{table}) t"""
                    loaded = json.loads(docker("exec", postgres, "psql", "-U", "postgres", "-d", "airbyte", "-Atc", query).stdout)
                    expected = [{**row, "created": datetime.fromisoformat(row["created"]).isoformat(timespec="microseconds") if row["created"] else None} for row in data.values()]
                    assert sorted(loaded, key=lambda row: row["id"]) == sorted(expected, key=lambda row: row["id"]), loaded
                    assert docker("exec", postgres, "psql", "-U", "postgres", "-d", "airbyte", "-Atc", f"SELECT count(*) FROM public.{empty}").stdout.strip() == "0"
                    print(f"Sync {attempt + 1}: all 5 rows verified in PostgreSQL; empty stream verified.", flush=True)
                print("PASS: native source, failed-read retry, credential sanitization, destination sync and overwrite replay.", flush=True)
            finally:
                docker(*base, "-i", "--entrypoint", "python", source, "-", stdin=cleanup, check=False)
    finally:
        docker("rm", "-f", postgres, check=False)
        if not args.network:
            docker("network", "rm", network, check=False)


if __name__ == "__main__":
    main()
