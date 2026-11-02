#!/usr/bin/env python3
"""Check that catalog metadata, packaging, and image labels describe one release."""

from pathlib import Path
import re
import tomllib
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
labels = dict(re.findall(r'LABEL io.airbyte.name=(\S+) io.airbyte.version=(\S+)', (ROOT / 'Dockerfile').read_text()))
for kind in ('source', 'destination'):
    folder = ROOT / 'connectors' / f'{kind}-redis-adbc'
    metadata = yaml.safe_load((folder / 'metadata.yaml').read_text())['data']
    uuid.UUID(metadata['definitionId'])
    assert metadata['connectorType'] == kind
    assert metadata['dockerImageTag'] == project['version']
    assert labels[metadata['dockerRepository']] == project['version']
    assert metadata['license'] == project['license']
    assert (folder / metadata['icon']).is_file()
    assert metadata['remoteRegistries']['pypi']['packageName'] == project['name']
    assert {suite['suite'] for suite in metadata['connectorTestSuitesOptions']} >= {'unitTests', 'integrationTests', 'acceptanceTests'}
print('Source and destination metadata match package version, image labels, license and test suites.')
