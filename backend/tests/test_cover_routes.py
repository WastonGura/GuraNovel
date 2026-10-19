import io
import os

import pytest
from PIL import Image

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Production asset storage uses POSIX descriptor-relative operations')
stream = io.BytesIO()
Image.new('RGB', (4, 6), 'white').save(stream, format='PNG')
PNG = stream.getvalue()


def test_upload_survives_app_restart_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'workspace_base_dir', tmp_path / 'workspaces')
    with TestClient(create_app()) as client:
        response = client.post('/api/v1/covers', content=PNG, headers={'Content-Type': 'image/png'})
        assert response.status_code == 201
        cover = response.json()['cover']
        assert cover.startswith('cover-upload:')
        assert client.post('/api/v1/covers', content=PNG).json()['cover'] == cover
    with TestClient(create_app()) as client:
        image = client.get('/api/v1/covers/' + cover.split(':')[1])
        assert image.status_code == 200
        assert image.headers['content-type'] == 'image/webp'
        assert image.headers['x-content-type-options'] == 'nosniff'
        assert image.content.startswith(b'RIFF')


def test_rejects_bad_images_and_oversized_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'workspace_base_dir', tmp_path / 'workspaces')
    with TestClient(create_app()) as client:
        assert client.post('/api/v1/covers', content=b'<svg/>', headers={'Content-Type': 'image/png'}).status_code == 422
        assert client.post('/api/v1/covers', content=b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
        assert client.get('/api/v1/covers/' + '0' * 64).status_code == 404
        assert client.get('/api/v1/covers/not-an-asset').status_code == 404


def test_asset_symlinks_are_not_served(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'workspace_base_dir', tmp_path / 'workspaces')
    with TestClient(create_app()) as client:
        cover = client.post('/api/v1/covers', content=PNG).json()['cover']
        identity = cover.split(':')[1]
        path = settings.workspace_base_dir / '.assets' / (identity + '.webp')
        path.unlink()
        outside = tmp_path / 'private.txt'
        outside.write_text('private')
        path.symlink_to(outside)
        assert client.get('/api/v1/covers/' + identity).status_code == 404


def test_width_variants_reduce_bytes_keep_aspect_and_revalidate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'workspace_base_dir', tmp_path / 'workspaces')
    large = io.BytesIO()
    Image.effect_noise((900, 1200), 70).convert('RGB').save(large, format='PNG')
    with TestClient(create_app()) as client:
        uploaded = client.post('/api/v1/covers', content=large.getvalue())
        identity = uploaded.json()['cover'].split(':')[1]
        master = client.get('/api/v1/covers/' + identity)
        small = client.get('/api/v1/covers/' + identity + '?w=160')
        with Image.open(io.BytesIO(small.content)) as image:
            assert image.width == 160
            assert abs(image.width / image.height - 0.75) < 0.01
        assert len(small.content) < len(master.content) / 3
        cached = client.get('/api/v1/covers/' + identity + '?w=160', headers={'If-None-Match': small.headers['etag']})
        assert cached.status_code == 304
        assert not cached.content
        assert client.get('/api/v1/covers/' + identity + '?w=999999').status_code == 422


def test_legacy_upload_derivatives_are_generated_once_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'workspace_base_dir', tmp_path / 'workspaces')
    with TestClient(create_app()) as client:
        cover = client.post('/api/v1/covers', content=PNG).json()['cover']
        identity = cover.split(':')[1]
        derivative = settings.workspace_base_dir / '.assets' / (identity + '-160.webp')
        derivative.unlink()
        assert client.get('/api/v1/covers/' + identity + '?w=160').status_code == 200
        timestamp = derivative.stat().st_mtime_ns
        assert client.get('/api/v1/covers/' + identity + '?w=160').status_code == 200
        assert derivative.stat().st_mtime_ns == timestamp
