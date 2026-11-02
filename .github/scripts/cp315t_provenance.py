"""Check original wheel integrity, installed bytes, actual SDK, and natural GIL."""
from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import sysconfig
import tempfile
from typing import Callable, cast
from urllib.request import urlopen
from zipfile import ZipFile


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def string(value: object) -> str:
    assert isinstance(value, str)
    return value


def document(data: str) -> dict[str, object]:
    value: object = json.loads(data)
    return mapping(value)


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(manifest_path: Path, output: Path, local_wheel: Path | None,
        expected_commit: str | None) -> None:
    assert sys.version_info[:2] == (3, 15)
    assert sysconfig.get_config_var('Py_GIL_DISABLED') == 1
    gil = cast(Callable[[], bool], getattr(sys, '_is_gil_enabled'))
    assert getattr(sys.flags, 'gil') is None and not gil()
    for name in ('PYTHON_GIL', 'PYTHONHOME', 'PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        assert not os.environ.get(name), name
    key = 'linux-x86_64' if sys.platform == 'linux' else 'windows-x86_64'
    assert platform.machine().lower() in ('amd64', 'x86_64')
    manifest = document(manifest_path.read_text(encoding='utf8'))
    asset = mapping(mapping(manifest['artifacts'])[key])
    dist = metadata.distribution('opencv-python')
    assert dist.version == asset['version'], (dist.version, asset['version'])
    for alternative in ('opencv-contrib-python', 'opencv-python-headless', 'opencv-contrib-python-headless'):
        try:
            metadata.distribution(alternative)
        except metadata.PackageNotFoundError:
            continue
        raise AssertionError('Unexpected alternate OpenCV distribution: ' + alternative)
    direct = document(dist.read_text('direct_url.json') or '{}')
    if expected_commit is not None:
        assert len(expected_commit) == 40
        assert mapping(direct['vcs_info'])['commit_id'] == expected_commit
        assert string(direct['url']).rstrip('/') == 'https://github.com/StableKite/opencv-python.git'
    else:
        assert local_wheel is not None, 'Published default validation requires expected Git commit'
        assert string(direct['url']).startswith('file:'), direct
    info = next(p for p in dist.files or [] if str(p).endswith('.dist-info/RECORD')).as_posix()
    info_dir = str(PurePosixPath(info).parent)
    installed_record = dist.locate_file(info).read_text(encoding='utf8')
    installed_checked = 0
    for name, checksum, size in csv.reader(io.StringIO(installed_record)):
        path = Path(str(dist.locate_file(name)))
        assert path.is_file(), name
        if not checksum:
            assert name == info or name.endswith('.pyc'), name
            continue
        algorithm, encoded = checksum.split('=', 1)
        data = path.read_bytes()
        assert base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).decode().rstrip('=') == encoded, name
        assert len(data) == int(size), name
        installed_checked += 1
    with tempfile.TemporaryDirectory(prefix='opencv-original-wheel-') as temporary:
        if local_wheel is None:
            wheel = Path(temporary) / string(asset['filename'])
            with urlopen(string(asset['url']), timeout=180) as response, wheel.open('wb') as destination:
                while block := response.read(1 << 20):
                    _ = destination.write(block)
        else:
            wheel = local_wheel
        wheel_sha = sha256(wheel)
        assert wheel_sha == asset['sha256'], wheel_sha
        with ZipFile(wheel) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)) and archive.testzip() is None
            record_name = info_dir + '/RECORD'
            assert record_name in names
            recorded: set[str] = set()
            for name, checksum, size in csv.reader(io.StringIO(archive.read(record_name).decode())):
                assert name not in recorded and name in names
                relative = PurePosixPath(name)
                assert not relative.is_absolute() and '..' not in relative.parts
                recorded.add(name)
                if not checksum:
                    assert name == record_name
                    continue
                data = archive.read(name)
                algorithm, encoded = checksum.split('=', 1)
                assert base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).decode().rstrip('=') == encoded, name
                assert len(data) == int(size), name
                assert Path(str(dist.locate_file(name))).read_bytes() == data, name
            assert all(name.endswith('/') or name in recorded for name in names)
            original_checked = len(recorded) - 1
            provenance = document(archive.read(info_dir + '/STABLEKITE_NATIVE_PROVENANCE.json').decode())
    native: dict[str, object] = {}
    if sys.platform == 'linux':
        sdk = mapping(provenance['SDK_runtime'])
        providers = mapping(sdk['matched_original_providers'])
        assert len(providers) == 83 and not sdk['unresolved']
        libs = Path(str(dist.locate_file('opencv_python.libs')))
        files = {p.name: p for p in libs.iterdir() if p.is_file()}
        assert set(files) == set(providers)
        for name, provider in providers.items():
            assert sha256(files[name]) == mapping(provider)['bundled_full_sha256'], name
        assert not any(name.startswith(('libx264-', 'libx265-', 'libpostproc-')) for name in files)
        codec = ctypes.CDLL(str(next(p for n, p in files.items() if n.startswith('libavcodec-'))))
        codec.avcodec_license.restype = ctypes.c_char_p
        license_text = cast(bytes, codec.avcodec_license()).decode()
        util = ctypes.CDLL(str(next(p for n, p in files.items() if n.startswith('libavutil-'))))
        util.av_version_info.restype = ctypes.c_char_p
        ffmpeg_version = cast(bytes, util.av_version_info()).decode()
        assert license_text == 'LGPL version 3 or later' and ffmpeg_version == '6.1.6'
        extension = list(Path(str(dist.locate_file('cv2'))).glob('cv2*.so'))
        assert len(extension) == 1
        closure = subprocess.check_output(['/usr/bin/ldd', str(extension[0])], text=True)
        assert 'not found' not in closure and 'libgtk-3' in closure and 'libpango' in closure
        needed = subprocess.check_output(['/usr/bin/readelf', '-d', str(extension[0])], text=True)
        assert not any('Shared library: [libharfbuzz' in line for line in needed.splitlines())
        native = {'DSOs_verified': len(files), 'FFmpeg_version': ffmpeg_version,
                  'FFmpeg_license': license_text, 'actual_ELF_closure': closure,
                  'no_direct_system_HarfBuzz': True}
    assert not gil()
    result = {'status': 'PASS', 'python': sys.version, 'GIL': gil(), 'flags_gil': getattr(sys.flags, 'gil'),
              'version': dist.version, 'direct_url': direct, 'original_wheel_sha256': wheel_sha,
              'original_wheel_entries_verified_against_installed_bytes': original_checked,
              'installed_RECORD_entries_verified': installed_checked, 'ZIP_CRC': True,
              'expected_default_commit': expected_commit, 'local_wheel_gate_only': expected_commit is None,
              'actual_native': native}
    _ = output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k != 'actual_native'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    _ = parser.add_argument('manifest', type=Path)
    _ = parser.add_argument('output', type=Path)
    _ = parser.add_argument('--wheel', type=Path)
    _ = parser.add_argument('--expected-commit')
    args = parser.parse_args()
    run(cast(Path, args.manifest), cast(Path, args.output), cast(Path | None, args.wheel),
        cast(str | None, args.expected_commit))
