#!/usr/bin/env python3
"""Create, verify, test, and remove this checkout's disposable Enterprise cluster."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "secrets" / "enterprise-test"
IDENTITY = hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
NAME = "airbyte-enterprise-" + IDENTITY
LABEL = "io.airbyte.enterprise-test"
IMAGE = "redislabs/redis:8.2.0-78.18"
TEST_IMAGE = "source-redis-adbc-enterprise-test:0.1.1"


def command(*args, stdin=None):
    result = subprocess.run(args, input=stdin, capture_output=True, text=True)
    if result.returncode:
        # API payloads and connection strings can contain generated credentials.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout.strip()


def docker(*args):
    return command("docker", *args)


def save(name, value, private=True):
    path = DIRECTORY / name
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600 if private else 0o644)


def wait(description, predicate, seconds=360):
    print("Waiting for " + description + "...", flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2)
    raise RuntimeError("Timed out waiting for " + description)


class API:
    def __init__(self, state, verify=True):
        self.base = f"https://localhost:{state['api_port']}/v1/"
        self.auth = "Basic " + base64.b64encode(
            ("admin@example.com:" + state["admin_password"]).encode()).decode()
        # Only the initial localhost bootstrap uses the image's self-signed cert.
        self.context = ssl.create_default_context(cafile=str(DIRECTORY / "ca.crt")) if verify else ssl._create_unverified_context()

    def __call__(self, method, path, body=None):
        request = urllib.request.Request(self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=15) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise RuntimeError(f"Enterprise API {method} {path} failed (HTTP {status})") from None


def certificates(database_ip=None):
    command("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
        "-keyout", str(DIRECTORY / "ca.key"), "-out", str(DIRECTORY / "ca.crt"),
        "-subj", "/CN=Airbyte Enterprise test CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    command("openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(DIRECTORY / "server.key"), "-out", str(DIRECTORY / "server.csr"), "-subj", "/CN=localhost")
    (DIRECTORY / "server.ext").write_text(
        f"subjectAltName=DNS:localhost,DNS:host.docker.internal,DNS:{NAME},IP:127.0.0.1"
        + (f",IP:{database_ip}" if database_ip else "") + "\n"
        "extendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
    command("openssl", "x509", "-req", "-in", str(DIRECTORY / "server.csr"),
        "-CA", str(DIRECTORY / "ca.crt"), "-CAkey", str(DIRECTORY / "ca.key"),
        "-CAcreateserial", "-out", str(DIRECTORY / "server.crt"), "-days", "30",
        "-extfile", str(DIRECTORY / "server.ext"))
    for name in ("ca.key", "server.key"):
        (DIRECTORY / name).chmod(0o600)


def owned_container():
    info = json.loads(docker("inspect", NAME))[0]
    if info["Config"].get("Labels", {}).get(LABEL) != str(ROOT):
        raise RuntimeError("Refusing to use a container not owned by this checkout")
    return info


def up():
    # Docker rejects a name collision before any existing resources are changed.
    ROOT.joinpath("secrets").mkdir(mode=0o700, exist_ok=True)
    ROOT.joinpath("secrets").chmod(0o700)
    DIRECTORY.mkdir(mode=0o755, exist_ok=False)
    docker("network", "create", "--label", f"{LABEL}={ROOT}", NAME)
    docker("run", "-d", "--name", NAME, "--network", NAME, "--label", f"{LABEL}={ROOT}",
        "--cap-add", "SYS_RESOURCE", "-p", "127.0.0.1::9443", "-p", "127.0.0.1::12000", IMAGE)
    info = owned_container()
    certificates(info["NetworkSettings"]["Networks"][NAME]["IPAddress"])
    state = {"container": NAME, "network": NAME, "image": IMAGE,
        "image_id": info["Image"],
        "api_port": int(info["NetworkSettings"]["Ports"]["9443/tcp"][0]["HostPort"]),
        "db_port": int(info["NetworkSettings"]["Ports"]["12000/tcp"][0]["HostPort"]),
        "admin_password": secrets.token_urlsafe(32), "password": secrets.token_urlsafe(32),
        "username": "airbyte-test@example.com"}
    save("state.json", state)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for key in ("admin_password", "password"):
            print("::add-mask::" + state[key], flush=True)
    api = API(state, verify=False)
    wait("Enterprise management API", lambda: api("GET", "bootstrap"))
    api("POST", "bootstrap/create_cluster", {"action": "create_cluster", "cluster": {"name": "airbyte-test.local"},
        "node": {}, "credentials": {"username": "admin@example.com", "password": state["admin_password"]}})

    def bootstrapped():
        status = api("GET", "bootstrap")["bootstrap_status"]["state"]
        if status == "failed":
            raise RuntimeError("Enterprise bootstrap failed")
        return status == "completed"

    wait("Enterprise cluster bootstrap", bootstrapped)
    api("PUT", "cluster/certificates", {"certificates": [
        {"name": name, "certificate": (DIRECTORY / "server.crt").read_text(),
         "key": (DIRECTORY / "server.key").read_text()} for name in ("api", "proxy")]})
    api = API(state)
    wait("CA-verified management API", lambda: api("GET", "cluster"))
    role = api("POST", "roles", {"name": "airbyte-test", "management": "none"})["uid"]
    acl = api("POST", "redis_acls", {"name": "airbyte-test", "acl": "+@all ~* &*"})["uid"]
    body = {"name": "airbyte-test", "type": "redis", "redis_version": "8.6", "port": 12000,
        "memory_size": 2147483648, "replication": False, "data_persistence": "disabled",
        "eviction_policy": "noeviction", "shards_count": 2, "sharding": True,
        "shard_key_regex": [{"regex": r".*\{(?<tag>.*)\}.*"}, {"regex": r"(?<tag>.*)"}],
        "tls_mode": "enabled", "enforce_client_authentication": "disabled", "default_user": False,
        "roles_permissions": [{"role_uid": role, "redis_acl_uid": acl}],
        "query_performance_factor": {"active": True, "scaling_factor": 4}}
    uid = api("POST", "bdbs", body)["uid"]
    state["bdb_uid"] = uid
    save("state.json", state)
    wait("TLS database and QPF 4x", lambda: api("GET", f"bdbs/{uid}")["status"] == "active")
    api("POST", "users", {"email": state["username"], "name": "Airbyte test",
        "password": state["password"], "role_uids": [role]})
    credentials = quote(state["username"], safe="") + ":" + quote(state["password"], safe="")
    for filename, host, port, ca in (("config.json", NAME, 12000, "/enterprise-test/ca.crt"),
            ("host.json", "localhost", state["db_port"], str(DIRECTORY / "ca.crt"))):
        save(filename, {"uri": f"rediss://{credentials}@{host}:{port}/0?read_timeout=30s&write_timeout=30s",
            "batch_size": 2, "database_options": {"adbc.redis.tls_ca_file": ca}}, private=False)
    wait("authenticated TLS database", lambda: ping(state), seconds=120)
    verify()
    print(f"Enterprise environment ready. Configuration: {DIRECTORY / 'config.json'}", flush=True)


def redis_request(state, *parts, authenticate=True):
    context = ssl.create_default_context(cafile=str(DIRECTORY / "ca.crt"))
    with socket.create_connection(("localhost", state["db_port"]), timeout=3) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as connection:
            stream = connection.makefile("rb")

            def send(values):
                encoded = [str(value).encode() for value in values]
                connection.sendall(b"*%d\r\n" % len(encoded) + b"".join(
                    b"$%d\r\n" % len(value) + value + b"\r\n" for value in encoded))
                return stream.readline()

            if authenticate:
                response = send(("AUTH", state["username"], state["password"]))
                if response != b"+OK\r\n":
                    raise ConnectionError("Enterprise authentication not ready")
            return send(parts)


def ping(state):
    return redis_request(state, "PING") == b"+PONG\r\n"


def verify():
    owned_container()
    state = json.loads((DIRECTORY / "state.json").read_text())
    database = API(state)("GET", f"bdbs/{state['bdb_uid']}")
    assert database["tls_mode"] == "enabled" and database["default_user"] is False
    assert database["shards_count"] == 2 and database["eviction_policy"] == "noeviction"
    assert database["query_performance_factor"]["active"] is True
    assert database["query_performance_factor"]["scaling_factor"] == 4
    assert ping(state)
    assert redis_request(state, "FT._LIST").startswith(b"*")
    assert redis_request(state, "PING", authenticate=False).startswith(b"-NOAUTH")
    assert redis_request(state, "AUTH", state["username"], "incorrect", authenticate=False).startswith(b"-WRONGPASS")
    with socket.create_connection(("localhost", state["db_port"]), timeout=2) as raw:
        raw.sendall(b"*1\r\n$4\r\nPING\r\n")
        try:
            reply = raw.recv(1024)
        except (TimeoutError, ConnectionError):
            reply = b""
        assert not reply, "Enterprise accepted a plaintext connection"
    try:
        with socket.create_connection(("localhost", state["db_port"]), timeout=3) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname="localhost"):
                pass
    except ssl.SSLCertVerificationError:
        pass
    else:
        raise RuntimeError("Untrusted database certificate was accepted")
    save("verification.json", {"image": IMAGE, "shards": 2, "tls": True, "certificate_verified": True,
        "username_password": True, "default_user": False, "search": True, "qpf": 4,
        "plaintext_rejected": True, "missing_auth_rejected": True, "wrong_password_rejected": True,
        "untrusted_ca_rejected": True}, private=False)
    print("Verified: two Enterprise shards, Search, QPF 4x, trusted TLS, named-user authentication; plaintext and invalid credentials rejected.", flush=True)


def gateway(image):
    verify()
    state = json.loads((DIRECTORY / "state.json").read_text())
    name = NAME + "-flight"
    directory = DIRECTORY / "gateway"
    directory.mkdir(mode=0o755, exist_ok=True)
    (directory / "server.ext").write_text(
        f"subjectAltName=DNS:localhost,DNS:{name}\n"
        "extendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
    command("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(directory / "server.key"),
        "-out", str(directory / "server.csr"), "-subj", "/CN=localhost")
    command("openssl", "x509", "-req", "-in", str(directory / "server.csr"), "-CA", str(DIRECTORY / "ca.crt"),
        "-CAkey", str(DIRECTORY / "ca.key"), "-CAcreateserial", "-out", str(directory / "server.crt"),
        "-days", "30", "-extfile", str(directory / "server.ext"))
    # The nonroot gateway user reads this key through its dedicated read-only
    # mount. The enclosing host secrets directory is private to the owner.
    (directory / "server.key").chmod(0o644)
    shutil.copyfile(DIRECTORY / "ca.crt", directory / "ca.crt")
    image_id = docker("image", "inspect", image, "--format", "{{.Id}}")
    docker("run", "-d", "--name", name, "--network", NAME, "--label", f"{LABEL}={ROOT}",
        "-v", f"{directory}:/tls:ro", "-e", "SSL_CERT_FILE=/tls/ca.crt", image_id,
        "--redis-uri", f"rediss://{NAME}:12000/0", "--auth", "redis-acl",
        "--tls-cert", "/tls/server.crt", "--tls-key", "/tls/server.key")
    state["flight_image_id"] = image_id
    state["flight_container"] = name
    save("state.json", state)
    save("flight.json", {"uri": f"grpc+tls://{name}:8815", "connection_type": "flight_sql",
        "database_options": {"username": state["username"], "password": state["password"],
            "adbc.flight.sql.client_option.tls_root_certs": (DIRECTORY / "ca.crt").read_text()}}, private=False)
    print("Redis-backed TLS Flight SQL gateway started; run the test action to verify it.", flush=True)


def test(source_image=None):
    verify()
    if not source_image:
        subprocess.run(["docker", "build", "--target", "test", "-t", TEST_IMAGE, str(ROOT)], check=True)
    args = ["docker", "run", "--rm", "--network", NAME, "-v", f"{DIRECTORY}:/enterprise-test:ro",
        "-e", "REDIS_ENTERPRISE_CONFIG=/enterprise-test/config.json"]
    if source_image:
        args.extend(["-v", f"{ROOT / 'tests'}:/validation/tests:ro",
                     "-v", f"{ROOT / 'scripts'}:/validation/scripts:ro",
                     "--entrypoint", "sh", source_image, "-c",
                     "python -m pip install --quiet --target /tmp/test-tools pytest==9.2.0 && "
                     "PYTHONPATH=/tmp/test-tools:/validation python -m pytest -q --tb=line "
                     "/validation/tests/test_integration.py /validation/tests/test_destination_integration.py"])
    elif (DIRECTORY / "flight.json").exists():
        args.extend(["-e", "FLIGHT_SQL_CONFIG=/enterprise-test/flight.json", TEST_IMAGE, "-q", "--tb=line", "-m", "integration"])
    else:
        args.extend([TEST_IMAGE, "-q", "--tb=line", "tests/test_integration.py", "tests/test_destination_integration.py"])
    subprocess.run(args, check=True)


def e2e(source_image=None, destination_image=None):
    verify()
    image = source_image or "source-redis-adbc-enterprise:0.1.1"
    if not source_image:
        subprocess.run(["docker", "build", "--target", "runtime", "-t", image, str(ROOT)], check=True)
    environment = {**os.environ, "REDIS_ENTERPRISE_CONFIG": str(DIRECTORY / "config.json")}
    subprocess.run([os.sys.executable, str(ROOT / "scripts/e2e.py"), "--source-image", image,
        "--network", NAME, "--tls-directory", str(DIRECTORY)], env=environment, check=True)
    destination = destination_image or "destination-redis-adbc-enterprise:0.1.1"
    if not destination_image:
        subprocess.run(["docker", "build", "--target", "destination", "-t", destination, str(ROOT)], check=True)
    subprocess.run([os.sys.executable, str(ROOT / "scripts/e2e_redis.py"), "--source-image", image,
        "--destination-image", destination, "--network", NAME, "--tls-directory", str(DIRECTORY)],
        env=environment, check=True)


def down():
    owned_container()
    state = json.loads((DIRECTORY / "state.json").read_text())
    if state.get("flight_container"):
        info = json.loads(docker("inspect", state["flight_container"]))[0]
        if info["Config"].get("Labels", {}).get(LABEL) != str(ROOT):
            raise RuntimeError("Refusing to remove an unowned gateway")
        docker("rm", "-f", state["flight_container"])
    docker("rm", "-f", NAME)
    info = json.loads(docker("network", "inspect", NAME))[0]
    if info.get("Labels", {}).get(LABEL) != str(ROOT):
        raise RuntimeError("Refusing to remove a network not owned by this checkout")
    docker("network", "rm", NAME)
    shutil.rmtree(DIRECTORY)
    print("Removed this checkout's Enterprise containers, network, and generated secrets.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "verify", "gateway", "test", "e2e", "down"))
    parser.add_argument("--flight-image", help="Existing Enterprise-capable Redis Flight SQL image")
    parser.add_argument("--source-image", help="Use this existing image without rebuilding")
    parser.add_argument("--destination-image", help="Use this existing image without rebuilding")
    args = parser.parse_args()
    if args.action == "gateway":
        if not args.flight_image:
            parser.error("gateway requires --flight-image with TLS and Redis ACL support")
        gateway(args.flight_image)
    elif args.action == "test":
        test(args.source_image)
    elif args.action == "e2e":
        e2e(args.source_image, args.destination_image)
    else:
        globals()[args.action]()
