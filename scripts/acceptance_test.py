#!/usr/bin/env python3
"""Run Airbyte's unchanged official acceptance suite against candidate images."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid
from urllib.parse import urlsplit, urlunsplit

import enterprise_env as enterprise

CAT_IMAGE = 'airbyte/connector-acceptance-test:2.0.1@sha256:b3aaa6be6f777cb4a2e4db52cfdca1a4dea1caaddb972373c9982f3827bfbcc3'
ENGINE_IMAGE = 'registry.dagger.io/engine:v0.6.4@sha256:691afa1f6ab7b8d7e867f8df5c293021756cefdeeee39c4450a39e403513f397'


def run(image, folder, *args):
    return subprocess.check_output(['docker', 'run', '--rm', '--network', enterprise.NAME,
        '-v', f'{folder}:/test_input', image, *args], text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-image', required=True)
    parser.add_argument('--destination-image', required=True)
    parser.add_argument('--engine', help='Use an existing disposable Dagger engine')
    args = parser.parse_args()
    enterprise.verify()
    token = uuid.uuid4().hex[:10]
    schema = 'acceptance_' + token
    folder = enterprise.DIRECTORY / 'acceptance'
    folder.mkdir(exist_ok=True)
    folder.chmod(0o755)
    config = json.loads((enterprise.DIRECTORY / 'config.json').read_text())
    config = {**config, 'database_options': {}, 'tls_ca_certificate': (enterprise.DIRECTORY / 'ca.crt').read_text(), 'schemas': [schema]}
    address = enterprise.owned_container()['NetworkSettings']['Networks'][enterprise.NAME]['IPAddress']
    uri = urlsplit(config['uri'])
    config['uri'] = urlunsplit((uri.scheme, uri.netloc.replace(uri.hostname, address), uri.path, uri.query, ''))
    (folder / 'config.json').write_text(json.dumps(config))
    (folder / 'invalid.json').write_text(json.dumps({**config, 'uri': 'rediss://invalid:incorrect@localhost:1/0?read_timeout=1s&write_timeout=1s'}))
    seed = f'''import json
from source_redis_adbc.backend import AdbcBackend
config=json.load(open('/test_input/config.json'))
with AdbcBackend(config).connection() as c, c.cursor() as cur:
 cur.execute('CREATE SCHEMA "{schema}"')
 cur.execute('CREATE TABLE "{schema}"."orders" (id INTEGER, label VARCHAR)')
 cur.execute("INSERT INTO \\"{schema}\\".\\"orders\\" VALUES (1, 'first'), (2, NULL), (3, 'third'), (4, 'fourth'), (5, 'fifth')")
'''
    (folder / 'seed.py').write_text(seed)
    subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME, '-v', f'{folder}:/test_input:ro',
        '--entrypoint', 'python', args.source_image, '/test_input/seed.py'], check=True)
    expected = [{'stream': 'orders', 'namespace': schema, 'emitted_at': 0, 'data': {'id': i, 'label': label}}
                for i, label in enumerate(('first', None, 'third', 'fourth', 'fifth'), 1)]
    (folder / 'expected.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in expected))
    engine = args.engine or enterprise.NAME + '-cat'
    if not args.engine:
        subprocess.run(['docker', 'run', '-d', '--privileged', '--network', enterprise.NAME,
            '--name', engine, '--label', f'{enterprise.LABEL}={enterprise.ROOT}', ENGINE_IMAGE], check=True, stdout=subprocess.DEVNULL)
    try:
        for kind, image in [('source', args.source_image), ('destination', args.destination_image)]:
            acceptance = json.loads((enterprise.ROOT / f'connectors/{kind}-redis-adbc/acceptance-test-config.yml').read_text())
            acceptance['connector_image'] = image
            (folder / 'metadata.yaml').write_text((enterprise.ROOT / f'connectors/{kind}-redis-adbc/metadata.yaml').read_text())
            (folder / 'spec.json').write_text((enterprise.ROOT / f'{kind}_redis_adbc/spec.json').read_text())
            if kind == 'destination':
                destination = {**config, 'schema': 'acceptance_dest_' + token}
                destination.pop('schemas')
                (folder / 'config.json').write_text(json.dumps(destination))
                (folder / 'invalid.json').write_text(json.dumps({**destination, 'uri': 'rediss://invalid:incorrect@localhost:1/0?read_timeout=1s&write_timeout=1s'}))
            (folder / 'acceptance-test-config.yml').write_text(json.dumps(acceptance))
            with (folder / 'image.tar').open('wb') as file:
                subprocess.run(['docker', 'save', image], stdout=file, check=True)
            docker_host = os.environ.get('DOCKER_HOST')
            if not docker_host:
                docker_host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
            if not docker_host.startswith('unix://'):
                raise RuntimeError('Acceptance tests require a local Unix Docker socket')
            subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME,
                '-v', f'{folder}:/test_input', '-v', docker_host.removeprefix('unix://') + ':/var/run/docker.sock',
                '-e', f'_EXPERIMENTAL_DAGGER_RUNNER_HOST=docker-container://{engine}',
                '-e', 'CONNECTOR_UNDER_TEST_IMAGE_TAR_PATH=/test_input/image.tar', CAT_IMAGE,
                '--acceptance-test-config', '/test_input', '-q', '--tb=line', '-m', 'not backward_compatibility',
                '--junitxml', f'/test_input/{kind}-acceptance.xml'], check=True)
    finally:
        try:
            if not args.engine:
                subprocess.run(['docker', 'rm', '-f', engine], check=True, stdout=subprocess.DEVNULL)
        finally:
            # Official CAT runs as root and creates private log directories. Give
            # our fixture back to its host owner so Linux cleanup can remove it.
            # -h prevents dereferencing symlinks outside the mounted fixture.
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0',
                '-v', f'{folder}:/test_input', '--entrypoint', 'chown', args.source_image,
                '-hR', f'{os.getuid()}:{os.getgid()}', '/test_input'], check=True)


if __name__ == '__main__':
    main()
