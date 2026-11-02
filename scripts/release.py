#!/usr/bin/env python3
"""Coordinate immutable publication and publish the announcement only after verification."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request

REPO = 'Redislabs-Solution-Architects/redis-airbyte-connector'
NAMESPACE = 'ghcr.io/redislabs-solution-architects'


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pypi(version):
    try:
        with urllib.request.urlopen(f'https://pypi.org/pypi/redis-airbyte-connector/{version}/json') as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def check_pypi(folder, version, require_all=False):
    remote = pypi(version)
    files = {item['filename']: item['digests']['sha256'] for item in remote['urls']} if remote else {}
    for path in [*folder.glob('*.whl'), *folder.glob('*.tar.gz')]:
        if path.name in files and files[path.name] != digest(path):
            raise RuntimeError(f'PyPI already contains different bytes for {path.name}; use a new version')
        if require_all and path.name not in files:
            raise RuntimeError(f'Published PyPI artifact is missing: {path.name}')


def registry(name, reference):
    query = urllib.parse.urlencode({'service': 'ghcr.io', 'scope': f'repository:redislabs-solution-architects/{name}:pull'})
    with urllib.request.urlopen('https://ghcr.io/token?' + query) as response:
        token = json.load(response)['token']
    request = urllib.request.Request(f'https://ghcr.io/v2/redislabs-solution-architects/{name}/manifests/{reference}',
        headers={'Authorization': 'Bearer ' + token, 'Accept': ','.join((
            'application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
            'application/vnd.docker.distribution.manifest.list.v2+json', 'application/vnd.docker.distribution.manifest.v2+json'))})
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            actual = 'sha256:' + hashlib.sha256(raw).hexdigest()
            if response.headers['Docker-Content-Digest'] != actual:
                raise RuntimeError('Registry manifest checksum mismatch')
            return json.loads(raw), actual
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None, None
        raise


def image_config(name, reference, arch):
    manifest, _ = registry(name, reference)
    if manifest is None:
        return None
    if 'manifests' in manifest:
        selected = [m for m in manifest['manifests'] if m.get('platform', {}).get('architecture') == arch]
        if len(selected) != 1:
            raise RuntimeError(f'Expected exactly one {arch} manifest for {name}')
        manifest, _ = registry(name, selected[0]['digest'])
    return manifest['config']['digest']


def check_image_identity(name, version, arch, expected):
    for reference in (version, f'{version}-{arch}'):
        existing = image_config(name, reference, arch)
        if existing is not None and existing != expected:
            raise RuntimeError(f'Versioned image {name}:{reference} already differs; use a new version')


def check_images(identities, version):
    for arch in ('amd64', 'arm64'):
        images = json.loads((identities / f'image-identities-{arch}/images.json').read_text())['images']
        for kind, expected in images.items():
            check_image_identity(kind + '-redis-adbc', version, arch, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'preflight', 'check-pypi', 'check-images', 'assets', 'finalize'))
    parser.add_argument('--tag', default='')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--directory', type=Path, default=Path('dist'))
    parser.add_argument('--arch')
    parser.add_argument('--identities', type=Path)
    args = parser.parse_args()
    version = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
    tag = args.tag or f'v{version}'
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag) or tag != f'v{version}':
        raise RuntimeError('Release tag must match the stable package version')
    if args.action == 'prepare':
        if args.publish:
            head = command('git', 'rev-parse', 'HEAD')
            if command('git', 'rev-parse', f'refs/tags/{tag}^{{commit}}') != head:
                raise RuntimeError('Release tag must point to the checked-out commit')
            subprocess.run(['git', 'merge-base', '--is-ancestor', head, 'origin/main'], check=True)
            release = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO, '--json', 'isDraft'], capture_output=True, text=True)
            if release.returncode == 0:
                if not json.loads(release.stdout)['isDraft']:
                    raise RuntimeError('This release is already public; use a new version')
            else:
                command('gh', 'release', 'create', tag, '--repo', REPO, '--verify-tag', '--target', head,
                        '--draft', '--title', tag, '--generate-notes')
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'version={version}\npublish={str(args.publish).lower()}\n')
    elif args.action == 'preflight':
        check_pypi(args.directory, version)
        check_images(args.identities, version)
    elif args.action == 'check-pypi':
        check_pypi(args.directory, version)
    elif args.action == 'check-images':
        manifest = json.loads((args.directory / 'images.json').read_text())
        for kind, expected in manifest['images'].items():
            check_image_identity(kind + '-redis-adbc', version, args.arch, expected)
    elif args.action == 'assets':
        release = json.loads(command('gh', 'release', 'view', tag, '--repo', REPO, '--json', 'assets'))
        existing = {asset['name']: asset['digest'] for asset in release['assets']}
        for path in args.directory.iterdir():
            if path.name in existing:
                if existing[path.name] != 'sha256:' + digest(path):
                    raise RuntimeError(f'Release asset already differs: {path.name}')
            else:
                command('gh', 'release', 'upload', tag, str(path), '--repo', REPO)
    else:
        check_pypi(args.directory, version, require_all=True)
        identities = {arch: json.loads((args.identities / f'image-identities-{arch}/images.json').read_text())['images']
                      for arch in ('amd64', 'arm64')}
        image_digests = {}
        for kind in ('source', 'destination'):
            name = kind + '-redis-adbc'
            manifest, combined = registry(name, version)
            architectures = {m['platform']['architecture'] for m in manifest['manifests'] if m.get('platform', {}).get('os') == 'linux'}
            if architectures != {'amd64', 'arm64'}:
                raise RuntimeError('Published image must contain both tested architectures')
            for arch in architectures:
                if image_config(name, version, arch) != identities[arch][kind]:
                    raise RuntimeError('Published image differs from its tested candidate')
            image_digests[name] = combined
        release = json.loads(command('gh', 'release', 'view', tag, '--repo', REPO, '--json', 'assets,body,isDraft'))
        if not release['isDraft']:
            raise RuntimeError('Release was published before final verification')
        assets = {asset['name']: asset['digest'] for asset in release['assets']}
        for path in args.directory.iterdir():
            if assets.get(path.name) != 'sha256:' + digest(path):
                raise RuntimeError('GitHub release assets do not match the tested distributions')
        notes = release['body'] + '\n\nValidated native TLS integration, official Airbyte acceptance suites and platform sync/replay checks before publication.\n\n'
        for name, image_digest in image_digests.items():
            notes += f'- `{NAMESPACE}/{name}@{image_digest}`\n'
        latest = subprocess.run(['gh', 'release', 'view', '--repo', REPO, '--json', 'tagName'], capture_output=True, text=True)
        current = json.loads(latest.stdout)['tagName'] if latest.returncode == 0 else 'v0.0.0'
        promote = tuple(map(int, version.split('.'))) >= tuple(map(int, current.removeprefix('v').split('.')))
        if promote:
            for name in image_digests:
                subprocess.run(['docker', 'buildx', 'imagetools', 'create', '-t', f'{NAMESPACE}/{name}:latest', f'{NAMESPACE}/{name}:{version}'], check=True)
        note_file = Path('release-notes.md')
        note_file.write_text(notes)
        command('gh', 'release', 'edit', tag, '--repo', REPO, '--notes-file', str(note_file), '--draft=false', '--latest=' + str(promote).lower())


if __name__ == '__main__':
    main()
