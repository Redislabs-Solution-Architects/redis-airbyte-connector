#!/usr/bin/env python3
"""Run the candidate source and destination through a disposable Airbyte platform."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import enterprise_env as enterprise

NODE = "airbyte-abctl-control-plane"
NAMESPACE = "airbyte-abctl"


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], text=True, **kwargs).strip()


def platform_status():
    """Print workload states without configurations, environment values or logs."""
    try:
        pods = json.loads(docker('exec', NODE, 'kubectl', '--kubeconfig', '/etc/kubernetes/admin.conf',
            '-n', NAMESPACE, 'get', 'pods', '-o', 'json'))['items']
        for pod in pods:
            statuses = pod['status'].get('initContainerStatuses', []) + pod['status'].get('containerStatuses', [])
            states = []
            for container in statuses:
                state = container.get('state', {})
                reason = state.get('waiting', {}).get('reason') or state.get('terminated', {}).get('reason') or 'running'
                states.append(container['name'] + '=' + reason)
            print('Platform pod ' + pod['metadata']['name'] + ': ' + ', '.join(states), flush=True)
    except (subprocess.SubprocessError, ValueError, KeyError):
        print('Platform pod diagnostics unavailable', flush=True)


class API:
    def __init__(self, base):
        self.base = base.rstrip('/') + '/api/v1/'
        secret = json.loads(docker('exec', NODE, 'kubectl', '--kubeconfig', '/etc/kubernetes/admin.conf',
                                  '-n', NAMESPACE, 'get', 'secret', 'airbyte-auth-secrets', '-o', 'json'))['data']
        self.credentials = {'client_id': base64.b64decode(secret['instance-admin-client-id']).decode(),
                            'client_secret': base64.b64decode(secret['instance-admin-client-secret']).decode(),
                            'grant-type': 'client_credentials'}
        self.token = None

    def __call__(self, path, body=None, method='POST', public=False):
        if self.token is None:
            self.token = self._request('applications/token', self.credentials)['access_token']
        if path != 'jobs/get':
            print('Airbyte ' + path.split('?')[0], flush=True)
        return self._request(path, body, method, public)

    def _request(self, path, body, method='POST', public=False, refresh=True):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        base = self.base.replace('/api/v1/', '/api/public/v1/') if public else self.base
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            # Platform errors can contain the complete connector configuration.
            code = error.code
            error.close()
            if code == 401 and refresh and self.token and path != 'applications/token':
                self.token = None
                self.token = self._request('applications/token', self.credentials, refresh=False)['access_token']
                return self._request(path, body, method, public, refresh=False)
            if code >= 500:
                platform_status()
            raise RuntimeError(f'Airbyte {path} failed (HTTP {code})') from None
        except (urllib.error.URLError, TimeoutError):
            platform_status()
            raise RuntimeError(f'Airbyte {path} did not respond') from None

    def job(self, job_id, expected='succeeded', timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self('jobs/get', {'id': job_id})['job']
            if job['status'] in ('succeeded', 'failed', 'cancelled', 'canceled'):
                assert job['status'] == expected, f'Job {job_id}: expected {expected}, got {job["status"]}'
                return job
            time.sleep(3)
        raise RuntimeError(f'Timed out waiting for Airbyte job {job_id}')


def import_images(images):
    exporter = subprocess.Popen(['docker', 'save', *images], stdout=subprocess.PIPE)
    try:
        subprocess.run(['docker', 'exec', '-i', NODE, 'ctr', '--namespace', 'k8s.io', 'images', 'import', '-'],
                       stdin=exporter.stdout, check=True, stdout=subprocess.DEVNULL)
    finally:
        exporter.stdout.close()
        if exporter.wait():
            raise RuntimeError('Could not export candidate images to the test platform')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-image', required=True)
    parser.add_argument('--destination-image', required=True)
    parser.add_argument('--api-url', default='http://localhost:8123')
    parser.add_argument('--output', type=Path, default=Path('platform-evidence.json'))
    args = parser.parse_args()
    enterprise.verify()
    deployment = json.loads(docker('exec', NODE, 'kubectl', '--kubeconfig', '/etc/kubernetes/admin.conf',
        '-n', NAMESPACE, 'get', 'deployment', 'airbyte-abctl-server', '-o', 'json'))
    server_image = deployment['spec']['template']['spec']['containers'][0]['image']
    assert server_image.endswith(':2.0.1'), 'Expected the pinned Airbyte 2.0.1 platform'
    import_images([args.source_image, args.destination_image])
    networks = json.loads(docker('inspect', NODE))[0]['NetworkSettings']['Networks']
    if enterprise.NAME not in networks:
        # Keep Kubernetes' original default gateway when adding database access.
        subprocess.run(['docker', 'network', 'connect', '--gw-priority', '-1', enterprise.NAME, NODE], check=True)
    info = enterprise.owned_container()
    address = info['NetworkSettings']['Networks'][enterprise.NAME]['IPAddress']
    config = json.loads((enterprise.DIRECTORY / 'config.json').read_text())
    from urllib.parse import urlsplit, urlunsplit
    uri = urlsplit(config['uri'])
    uri = urlunsplit((uri.scheme, uri.netloc.replace(uri.hostname, address), uri.path, uri.query, ''))
    config.pop('database_options', None)
    config = {**config, 'uri': uri,
              'tls_ca_certificate': (enterprise.DIRECTORY / 'ca.crt').read_text()}
    token = uuid.uuid4().hex[:10]
    incoming, outgoing = 'platform_in_' + token, 'platform_out_' + token
    folder = enterprise.DIRECTORY / 'platform'
    folder.mkdir(exist_ok=True)
    folder.chmod(0o755)
    source_config = {**config, 'schemas': [incoming]}
    destination_config = {**config, 'schema': outgoing}
    path = folder / 'source.json'
    path.write_text(json.dumps(source_config))
    path.chmod(0o644)
    seed = f'''import json
from source_redis_adbc.backend import AdbcBackend
config=json.load(open('/platform/source.json'))
with AdbcBackend(config).connection() as c, c.cursor() as cur:
 cur.execute('CREATE SCHEMA "{incoming}"')
 cur.execute('CREATE TABLE "{incoming}"."orders" (id BIGINT, label VARCHAR, amount DECIMAL(30,9))')
 cur.execute("INSERT INTO \\"{incoming}\\".\\"orders\\" VALUES (9007199254740993, 'first', 12345678901234567890.123456789), (2, NULL, NULL), (3, 'third', 1.000000001), (4, 'fourth', 0), (5, 'fifth', -0.000000001)")
'''
    # Keep fixtures and credentials out of process arguments and logs.
    seed_path = folder / 'seed.py'
    seed_path.write_text(seed)
    subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME, '-v', f'{folder}:/platform:ro',
                    '--entrypoint', 'python', args.source_image, '/platform/seed.py'], check=True)
    api = API(args.api_url)
    workspace = api('workspaces', method='GET', public=True)['data'][0]['workspaceId']
    ids = {}
    evidence = {'platform_version': '2.0.1', 'server_image': server_image, 'jobs': [], 'images': {name: docker('image', 'inspect', image, '--format', '{{.Id}}')
                                      for name, image in [('source', args.source_image), ('destination', args.destination_image)]}}
    try:
        for kind, image, cfg in [('source', args.source_image, source_config), ('destination', args.destination_image, destination_config)]:
            repository, tag = image.rsplit(':', 1)
            definition = api(f'workspaces/{workspace}/definitions/{kind}s', {'name': 'Redis ADBC test ' + token,
                'dockerRepository': repository, 'dockerImageTag': tag,
                'documentationUrl': 'https://github.com/Redislabs-Solution-Architects/redis-airbyte-connector'}, public=True)
            ids[kind + '_definition'] = definition.get('id') or definition['definitionId']
            created = api(kind + 's/create', {'workspaceId': workspace, kind + 'DefinitionId': ids[kind + '_definition'],
                'name': 'Redis ADBC test ' + token, 'connectionConfiguration': cfg})
            ids[kind] = created[kind + 'Id']
            checked = api(kind + 's/check_connection', {kind + 'Id': ids[kind]})
            assert (checked.get('status') or checked.get('connectionStatus', {}).get('status')) == 'succeeded', f'{kind} platform check failed'
        discovered = api('sources/discover_schema', {'sourceId': ids['source'], 'disableCache': True})
        stream = next(s for s in discovered['catalog']['streams'] if s['stream']['name'] == 'orders')
        stream['config'].update(selected=True, syncMode='full_refresh', destinationSyncMode='overwrite')
        connection = api('connections/create', {'sourceId': ids['source'], 'destinationId': ids['destination'],
            'name': 'Redis ADBC test ' + token, 'namespaceDefinition': 'source', 'prefix': '',
            'scheduleType': 'manual', 'status': 'active', 'sourceCatalogId': discovered['catalogId'],
            'syncCatalog': {'streams': [stream]}})
        ids['connection'] = connection['connectionId']
        for attempt in range(2):
            job_id = api('connections/sync', {'connectionId': ids['connection']})['job']['id']
            api.job(job_id)
            evidence['jobs'].append({'id': job_id, 'status': 'succeeded'})
            print(f'Platform sync {attempt + 1} succeeded (job {job_id})', flush=True)
        verification = f'''import json
from source_redis_adbc.backend import AdbcBackend
config=json.load(open('/platform/source.json'))
with AdbcBackend(config).connection() as c, c.cursor() as cur:
 cur.execute('SELECT id, label, amount FROM "{outgoing}"."orders" ORDER BY id')
 rows=cur.fetch_arrow_table().to_pylist()
 assert len(rows)==5, len(rows)
 expected=[
  {{'id':'2','label':None,'amount':None}},
  {{'id':'3','label':'third','amount':'1.000000001'}},
  {{'id':'4','label':'fourth','amount':'0.000000000'}},
  {{'id':'5','label':'fifth','amount':'-0.000000001'}},
  {{'id':'9007199254740993','label':'first','amount':'12345678901234567890.123456789'}}]
 assert rows==expected, 'Platform destination values differ from the synthetic fixture'
 print('Platform destination verified: five rows, exact large integer/decimal strings and NULLs; overwrite replay preserved row count.')
'''
        (folder / 'verify.py').write_text(verification)
        subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME, '-v', f'{folder}:/platform:ro',
                        '--entrypoint', 'python', args.source_image, '/platform/verify.py'], check=True)
        cancel_id = api('connections/sync', {'connectionId': ids['connection']})['job']['id']
        api('jobs/cancel', {'id': cancel_id})
        api.job(cancel_id, expected='cancelled')
        evidence['jobs'].append({'id': cancel_id, 'status': 'cancelled', 'scope': 'queued job'})
        # Cancellation must leave the previous published target readable.
        subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME, '-v', f'{folder}:/platform:ro',
                        '--entrypoint', 'python', args.source_image, '/platform/verify.py'], check=True)
        retry_id = api('connections/sync', {'connectionId': ids['connection']})['job']['id']
        api.job(retry_id)
        evidence['jobs'].append({'id': retry_id, 'status': 'succeeded', 'scope': 'manual restart after cancellation'})
        subprocess.run(['docker', 'run', '--rm', '--network', enterprise.NAME, '-v', f'{folder}:/platform:ro',
                        '--entrypoint', 'python', args.source_image, '/platform/verify.py'], check=True)
        print('Platform queued-job cancellation preserved the target; manual restart succeeded.', flush=True)
        evidence['verified_rows'] = 5
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
    finally:
        if 'connection' in ids:
            api('connections/delete', {'connectionId': ids['connection']})
        for kind in ('source', 'destination'):
            if kind in ids:
                api(kind + 's/delete', {kind + 'Id': ids[kind]})


if __name__ == '__main__':
    main()
