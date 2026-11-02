import hashlib
import json

import pytest

from scripts import image_artifacts, release


def test_archive_tampering_fails_before_loading_or_inspecting_images(tmp_path, monkeypatch):
    archive = tmp_path / 'images.tar'
    archive.write_bytes(b'validated archive')
    manifest = {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'images': {}}
    (tmp_path / 'images.json').write_text(json.dumps(manifest))
    archive.write_bytes(b'different archive')
    monkeypatch.setattr(image_artifacts, 'image_id', lambda image: pytest.fail('Must reject the archive first'))
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        image_artifacts.verify(tmp_path)


def test_loaded_image_replacement_is_rejected(tmp_path, monkeypatch):
    archive = tmp_path / 'images.tar'
    archive.write_bytes(b'archive')
    (tmp_path / 'images.json').write_text(json.dumps({
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'images': {'source': 'sha256:tested', 'destination': 'sha256:tested-destination'},
    }))
    monkeypatch.setattr(image_artifacts, 'image_id', lambda image: 'sha256:replacement')
    with pytest.raises(RuntimeError, match='differs from the tested artifact'):
        image_artifacts.verify(tmp_path)


def test_pypi_publication_refuses_existing_different_bytes(tmp_path, monkeypatch):
    wheel = tmp_path / 'connector.whl'
    wheel.write_bytes(b'candidate')
    monkeypatch.setattr(release, 'pypi', lambda version: {'urls': [
        {'filename': wheel.name, 'digests': {'sha256': 'different'}}
    ]})
    with pytest.raises(RuntimeError, match='use a new version'):
        release.check_pypi(tmp_path, '0.1.2')


def test_final_verification_requires_every_distribution_on_pypi(tmp_path, monkeypatch):
    (tmp_path / 'connector.whl').write_bytes(b'candidate')
    monkeypatch.setattr(release, 'pypi', lambda version: None)
    with pytest.raises(RuntimeError, match='missing'):
        release.check_pypi(tmp_path, '0.1.2', require_all=True)
