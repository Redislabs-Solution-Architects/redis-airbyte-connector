#!/usr/bin/env python3
"""Save, verify, and publish the exact Docker images tested by release CI."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

IMAGES = {'source': 'redis-airbyte/source-redis-adbc:tested', 'destination': 'redis-airbyte/destination-redis-adbc:tested'}


def image_id(image):
    return subprocess.check_output(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(folder):
    manifest = json.loads((folder / 'images.json').read_text())
    if checksum(folder / 'images.tar') != manifest['sha256']:
        raise RuntimeError('Candidate image archive checksum mismatch')
    for kind, image in IMAGES.items():
        if image_id(image) != manifest['images'][kind]:
            raise RuntimeError(f'The {kind} image differs from the tested artifact')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('save', 'load', 'verify', 'publish'))
    parser.add_argument('--directory', type=Path, default=Path('image-artifacts'))
    parser.add_argument('--namespace')
    parser.add_argument('--version')
    parser.add_argument('--arch')
    args = parser.parse_args()
    folder = args.directory
    if args.action == 'save':
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / 'images.tar').open('wb') as file:
            subprocess.run(['docker', 'save', *IMAGES.values()], stdout=file, check=True)
        manifest = {'images': {kind: image_id(image) for kind, image in IMAGES.items()}, 'sha256': checksum(folder / 'images.tar')}
        (folder / 'images.json').write_text(json.dumps(manifest, indent=2) + '\n')
    elif args.action == 'load':
        manifest = json.loads((folder / 'images.json').read_text())
        if checksum(folder / 'images.tar') != manifest['sha256']:
            raise RuntimeError('Candidate image archive checksum mismatch')
        subprocess.run(['docker', 'load', '-i', str(folder / 'images.tar')], check=True)
        verify(folder)
    else:
        manifest = verify(folder)
        if args.action == 'publish':
            if not all((args.namespace, args.version, args.arch)):
                parser.error('Publishing requires --namespace, --version and --arch')
            from release import image_config
            for kind, image in IMAGES.items():
                existing = image_config(kind + '-redis-adbc', f'{args.version}-{args.arch}', args.arch)
                if existing is not None:
                    if existing != manifest['images'][kind]:
                        raise RuntimeError('Versioned image already exists with different bytes; use a new version')
                    continue
                tag = f'{args.namespace}/{kind}-redis-adbc:{args.version}-{args.arch}'
                subprocess.run(['docker', 'tag', image, tag], check=True)
                if image_id(tag) != manifest['images'][kind]:
                    raise RuntimeError('Publishing tag no longer refers to the tested image')
                subprocess.run(['docker', 'push', tag], check=True)


if __name__ == '__main__':
    main()
