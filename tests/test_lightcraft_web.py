"""The manual editor installs once and shares the existing photo picker boundary."""
import hashlib
import zipfile
from unittest.mock import patch

from autoedit.web import lightcraft


def _make_bundle(path, skip=None):
    with zipfile.ZipFile(path, 'w') as archive:
        for name in (*lightcraft._ENGINE_FILES, 'NOTICE', 'LICENSE-MIT', 'OFL-Inter.txt', 'unneeded'):
            if name != skip:
                archive.writestr(lightcraft.BUNDLE + '/' + name, b'test asset')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_install_preserves_notices_and_runs_offline(tmp_path):
    archive = tmp_path / 'release.zip'
    digest = _make_bundle(archive)
    with patch.dict('os.environ', {'XDG_CACHE_HOME': str(tmp_path)}), patch.object(lightcraft, 'DIGEST', digest):
        lightcraft.install(archive)
        root = lightcraft.release_dir()
        assert lightcraft.available()
        assert (root / 'NOTICE').is_file() and (root / 'OFL-Inter.txt').is_file()
        assert not (root / 'unneeded').exists()
        with patch('urllib.request.urlopen', side_effect=AssertionError('offline')):
            lightcraft.install()


def test_bad_or_partial_install_cannot_publish_editor(tmp_path):
    import pytest
    archive = tmp_path / 'release.zip'
    digest = _make_bundle(archive, skip='worker.js')
    with patch.dict('os.environ', {'XDG_CACHE_HOME': str(tmp_path)}):
        with pytest.raises(ValueError, match='checksum'):
            lightcraft.install(archive)
        with patch.object(lightcraft, 'DIGEST', digest), pytest.raises(ValueError, match='required files'):
            lightcraft.install(archive)
        assert not lightcraft.available() and not lightcraft.release_dir().exists()


def test_manual_editor_only_reads_approved_originals(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return
    from autoedit.web import app, service
    from PIL import Image

    client = TestClient(app.create_app(), base_url='http://localhost')
    image = tmp_path / 'original.png'
    Image.new('RGB', (16, 12), '#aabbcc').save(image)
    with patch.object(service, '_ALLOWED_ROOTS', set()), patch.dict('os.environ', {'AUTOEDIT_ALLOW_ANY_PATH': ''}):
        read = lambda: client.get('/api/v1/editor/original', params={'path': str(image)})
        assert read().status_code == 403
        service.approve_root(str(image))
        response = read()
        assert response.content == image.read_bytes() and response.headers['cache-control'] == 'no-store'
        text = tmp_path / 'private.txt'
        text.write_text('private')
        assert client.get('/api/v1/editor/original', params={'path': str(text)}).status_code == 400

    with patch.dict('os.environ', {'XDG_CACHE_HOME': str(tmp_path)}):
        status = client.get('/api/v1/editor/status').json()
        # The page asks before it downloads, and names the size: one the download accepts.
        assert not status['ready'] and status['download_mb'] == round(lightcraft.BYTES / 1e6)
        assert lightcraft.BYTES < lightcraft._LIMIT
        assert client.get('/editor/release/index.html').status_code == 404
        archive = tmp_path / 'release.zip'
        with patch.object(lightcraft, 'DIGEST', _make_bundle(archive)):
            lightcraft.install(archive)
            wasm = client.get('/editor/release/lightcraft_web_bg.wasm')
            assert wasm.status_code == 200 and wasm.headers['content-type'] == 'application/wasm'
            assert wasm.headers['cross-origin-resource-policy'] == 'same-origin'
            assert client.get('/editor/release/.verified').status_code == 404
            assert client.get('/editor/release/index.html', headers={'Host': 'elsewhere.test'}).status_code == 403


def test_manual_gemini_uses_current_thumbnail_and_only_supported_sliders():
    import base64
    import io
    import json
    from PIL import Image
    from autoedit.decide import gemini
    from autoedit.web import ai_styles, assist

    image = io.BytesIO()
    Image.new('RGB', (8, 12), 'gray').save(image, 'JPEG')
    payload = {'prompt': 'Keep the sky natural.', 'controls': {'light.exposure': 0.25, 'private-label': 3},
               'thumbnail': base64.b64encode(image.getvalue()).decode(), 'gemini_api_key': 'test-only'}
    changes = [{'label': 'Lift shadows', 'target': 'photo', 'ops': {'light.exposure': 0.2},
                'why': 'Reveal foreground detail.'}]
    calls = []

    def transport(body, key):
        calls.append(json.loads(body))
        reply = json.dumps({'changes': changes, 'explanation': 'A small exposure increase.'})
        return json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': reply}]}}]}).encode()

    with patch.object(assist, '_CLIENT', gemini.Gemini(transport=transport)):
        assert lightcraft.suggest_adjustments(payload)['reason'] == 'consent' and not calls
        response = lightcraft.suggest_adjustments({**payload, 'allow_cloud': True})
        assert response['proposal']['changes'] == changes and len(calls) == 1
        sent = json.dumps(calls[0])
        assert 'light.exposure' in sent and 'private-label' not in sent and 'test-only' not in sent

        controls = {'light.exposure': 0.25, 'color.vibrance': 2, 'effects.clarity': 3,
                    'mixer.orange.sat': 0, 'mixer.blue.lum': -2, 'mixer.secret.lum': 1,
                    'wb.tempRel': 0, 'wb.tint': 0}
        style_payload = {**payload, 'controls': controls, 'allow_cloud': True, 'style': 'blue_hour', 'prompt': ''}
        changes[0]['ops'] = {'mixer.orange.sat': 8, 'mixer.blue.lum': -6, 'wb.tempRel': 5, 'wb.tint': 1}
        assert lightcraft.suggest_adjustments(style_payload)['proposal']['changes'] == changes
        part = calls[-1]['contents'][0]['parts'][0]['text']
        assert ai_styles.prompt_for('blue_hour') in part and 'mixer.secret.lum' not in part
        assert 'mixer.orange.sat' in part and 'effects.clarity' in part

        changes[0]['ops']['mixer.orange.sat'] = 10000
        assert lightcraft.suggest_adjustments(style_payload)['reason'] == 'invalid_response'


def test_raw_compatibility_copy_is_explicit_full_resolution_and_16_bit(tmp_path):
    try:
        import tifffile
        from fastapi.testclient import TestClient
    except ImportError:
        return
    import io
    import numpy as np
    from autoedit import imageio
    from autoedit.web import app, service

    raw = tmp_path / 'camera.cr2'
    raw.write_bytes(b'raw fixture')
    rgb = np.full((40, 60, 3), .125, dtype=np.float32)
    client = TestClient(app.create_app(), base_url='http://localhost')
    with patch.object(service, '_ALLOWED_ROOTS', {str(tmp_path.resolve())}), \
            patch.object(imageio, 'load_image', return_value=imageio.Image(rgb, 16)) as decoder:
        url = '/api/v1/editor/original'
        assert client.get(url, params={'path': str(raw)}).content == raw.read_bytes()
        decoder.assert_not_called()
        response = client.get(url, params={'path': str(raw), 'as_tiff': True})
        assert response.status_code == 200 and response.headers['content-type'] == 'image/tiff'
        converted = tifffile.imread(io.BytesIO(response.content))
        assert converted.dtype == np.uint16 and converted.shape == rgb.shape
        assert converted[0, 0, 0] == 8192 and raw.read_bytes() == b'raw fixture'
        assert list(tmp_path.iterdir()) == [raw]
        jpeg = tmp_path / 'sample.jpg'
        jpeg.write_bytes(b'fixture')
        assert client.get(url, params={'path': str(jpeg), 'as_tiff': True}).status_code == 400
        assert decoder.call_count == 1
