#!/usr/bin/env python3
"""Create and remove an owned disposable abctl installation for release tests."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'secrets/platform-test/state.json'
ABCTL_VERSION = '0.30.4'
CHART_VERSION = '1.8.5'
NODE = 'airbyte-abctl-control-plane'


def node_id():
    result = subprocess.run(['docker', 'inspect', NODE, '--format', '{{.Id}}'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def tool():
    system = {'Darwin': 'darwin', 'Linux': 'linux'}[platform.system()]
    arch = 'arm64' if platform.machine() in ('arm64', 'aarch64') else 'amd64'
    directory = ROOT / '.release-tools'
    binary = directory / f'abctl-v{ABCTL_VERSION}-{system}-{arch}' / 'abctl'
    if not binary.exists():
        directory.mkdir(exist_ok=True)
        filename = f'abctl-v{ABCTL_VERSION}-{system}-{arch}.tar.gz'
        archive = directory / filename
        url = f'https://github.com/airbytehq/abctl/releases/download/v{ABCTL_VERSION}/{filename}'
        with urllib.request.urlopen(url, timeout=120) as response:
            archive.write_bytes(response.read())
        with tarfile.open(archive) as package:
            package.extractall(directory, filter='data')
    return str(binary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('up', 'down'))
    args = parser.parse_args()
    environment = {**os.environ, 'DO_NOT_TRACK': '1'}
    if args.action == 'up':
        if node_id() or STATE.exists() or Path.home().joinpath('.airbyte/abctl/abctl.kubeconfig').exists():
            raise RuntimeError('Refusing to replace an existing Airbyte installation or test state')
        STATE.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            subprocess.run([tool(), 'local', 'install', '--chart-version', CHART_VERSION,
                '--host', 'localhost', '--port', '8123', '--low-resource-mode', '--no-browser'],
                env=environment, check=True)
        finally:
            current = node_id()
            if current:
                STATE.write_text(json.dumps({'node_id': current, 'chart_version': CHART_VERSION}) + '\n')
                STATE.chmod(0o600)
    else:
        state = json.loads(STATE.read_text())
        if node_id() != state['node_id']:
            raise RuntimeError('Refusing to remove an Airbyte installation not created by this test')
        subprocess.run([tool(), 'local', 'uninstall', '--persisted'], env=environment, check=True)
        STATE.unlink()


if __name__ == '__main__':
    main()
