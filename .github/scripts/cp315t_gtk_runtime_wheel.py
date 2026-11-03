"""Retain public SONAMEs for GTK libraries which share system GObject modules.

This producer step follows auditwheel repair. GIO and GdkPixbuf load ordinary
system modules which depend on the original SONAMEs. Hashing those SONAMEs
loads a second runtime into the same GType registry. Preserve the complete
GTK/GObject family, while retaining the existing vendored codec SDK.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from typing import cast
import zipfile


SHARED_SONAMES = {
    'libgio-2.0': 'libgio-2.0.so.0',
    'libgtk-3': 'libgtk-3.so.0',
    'libgdk-3': 'libgdk-3.so.0',
    'libgdk_pixbuf-2.0': 'libgdk_pixbuf-2.0.so.0',
    'libpango-1.0': 'libpango-1.0.so.0',
    'libpangocairo-1.0': 'libpangocairo-1.0.so.0',
    'libpangoft2-1.0': 'libpangoft2-1.0.so.0',
    'libatk-1.0': 'libatk-1.0.so.0',
    'libcairo-gobject': 'libcairo-gobject.so.2',
    'libcairo': 'libcairo.so.2',
}


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def string(value: object) -> str:
    assert isinstance(value, str)
    return value


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, check=False)
    assert result.returncode == 0, (argv, result.returncode, result.stderr.decode(errors='replace'))
    return result.stdout.decode()


def abi(path: Path, directory: Path) -> dict[str, object]:
    text = directory/'section-text.bin'
    run(['/usr/bin/objcopy', '--dump-section', '.text='+str(text), str(path),
         str(directory/'abi-inspection-copy.elf')])
    names = sorted(line.split()[0] for line in run(
        ['/usr/bin/nm', '-D', '--defined-only', '--format=posix', str(path)]).splitlines())
    return {'text_SHA256': digest(text), 'text_bytes': text.stat().st_size,
            'exports_SHA256': hashlib.sha256(('\n'.join(names)+'\n').encode()).hexdigest(),
            'exports': len(names)}


def repair(input_wheel: Path, output_wheel: Path, report: Path, patchelf: Path,
           source_manifest: Path) -> None:
    assert input_wheel.resolve() != output_wheel.resolve() and not output_wheel.exists()
    assert not report.exists()
    assert digest(input_wheel) == 'afa9195d54a22b4cbe1311022ed194c2e87cca46cc68ad76ddb9536ff29dba60'
    source_value: object = json.loads(source_manifest.read_text())
    sources = mapping(source_value)
    assert len(string(sources['sha256'])) == 64 and string(sources['url']).startswith('https://')
    version = '5.1.0+b304bc2.stablekite.2'
    old_version = '5.1.0+b304bc2.stablekite.1'
    new_info = 'opencv_python-'+version+'.dist-info'
    commands: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix='opencv-gtk3-producer-') as temporary:
        directory = Path(temporary)
        unpacked = directory/'wheel'
        unpacked.mkdir()
        with zipfile.ZipFile(input_wheel) as archive:
            assert archive.testzip() is None
            for member in archive.infolist():
                assert (unpacked/member.filename).resolve().is_relative_to(unpacked)
            archive.extractall(unpacked)
        old_info = unpacked/('opencv_python-'+old_version+'.dist-info')
        provenance_value: object = json.loads((old_info/'STABLEKITE_NATIVE_PROVENANCE.json').read_text())
        provenance = mapping(provenance_value)
        sdk = mapping(provenance['SDK_runtime'])
        providers = mapping(sdk['matched_original_providers'])
        libraries = unpacked/'opencv_python.libs'
        assert set(providers) == {path.name for path in libraries.iterdir()}
        renamed: dict[str, str] = {}
        for name, provider_value in providers.items():
            assert Path(name).name == name and not (libraries/name).is_symlink()
            provider = mapping(provider_value)
            assert digest(libraries/name) == provider['bundled_full_sha256']
            original = Path(string(provider['original_file']))
            stem = original.name.split('.so.')[0]
            if stem not in SHARED_SONAMES:
                continue
            assert original.is_file() and digest(original) == provider['original_full_sha256']
            soname = run([str(patchelf), '--print-soname', str(original)]).strip()
            assert soname == SHARED_SONAMES[stem]
            assert not (libraries/soname).exists()
            renamed[name] = soname
        assert set(renamed.values()) == set(SHARED_SONAMES.values())
        paths = [*sorted(libraries.iterdir()), *sorted((unpacked/'cv2').glob('*.so'))]
        abi_proof: list[dict[str, object]] = []
        for path in paths:
            needed = run([str(patchelf), '--print-needed', str(path)]).splitlines()
            rewrites = [(old, new) for old, new in renamed.items() if old in needed]
            if not rewrites and path.name not in renamed:
                continue
            before, before_sha = abi(path, directory), digest(path)
            for old, new in rewrites:
                argv = [str(patchelf), '--replace-needed', old, new, str(path)]
                run(argv)
                commands.append({'argv': argv, 'exit_code': 0})
            if path.name in renamed:
                argv = [str(patchelf), '--set-soname', renamed[path.name], str(path)]
                run(argv)
                commands.append({'argv': argv, 'exit_code': 0})
            after = abi(path, directory)
            assert before == after, path.name
            abi_proof.append({'file': path.relative_to(unpacked).as_posix(),
                              'before_SHA256': before_sha, 'after_SHA256': digest(path),
                              'text_and_exports_unchanged': before})
        for old, new in renamed.items():
            (libraries/old).rename(libraries/new)
            providers[new] = providers.pop(old)
        for name, provider_value in providers.items():
            provider = mapping(provider_value)
            provider['bundled_full_sha256'] = digest(libraries/name)
        for path in [*libraries.iterdir(), *list((unpacked/'cv2').glob('*.so'))]:
            assert not (set(run([str(patchelf), '--print-needed', str(path)]).splitlines()) & set(renamed))
        provenance.update(version=version, corresponding_sources=sources,
            corresponding_sources_url=sources['url'],
            changes='GTK/GObject libraries retain their public SONAMEs so system GIO/GdkPixbuf modules share one runtime. Native executable sections and exported symbols preserved; wheel metadata and RECORD regenerated.',
            GTK3_shared_runtime={'sonames': renamed, 'ABI': abi_proof,
                'input_wheel_SHA256': digest(input_wheel),
                'producer_SHA256': digest(Path(__file__))})
        (old_info/'STABLEKITE_NATIVE_PROVENANCE.json').write_text(json.dumps(provenance, indent=2)+'\n')
        metadata = (old_info/'METADATA').read_bytes()
        assert metadata.count(('Version: '+old_version+'\n').encode()) == 1
        (old_info/'METADATA').write_bytes(metadata.replace(('Version: '+old_version+'\n').encode(), ('Version: '+version+'\n').encode()))
        (old_info/'RECORD').unlink()
        old_info.rename(unpacked/new_info)
        payload = {path.relative_to(unpacked).as_posix(): path.read_bytes()
                   for path in unpacked.rglob('*') if path.is_file()}
        rows = [(name, 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data)))
                for name, data in sorted(payload.items())]
        record_name = new_info+'/RECORD'
        rows.append((record_name, '', ''))
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator='\n').writerows(rows)
        payload[record_name] = buffer.getvalue().encode()
        with zipfile.ZipFile(output_wheel, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(name, data)
    report.write_text(json.dumps({'status': 'LOCAL_WHEEL_CANDIDATE_RUNTIME_PENDING',
        'input_wheel': str(input_wheel), 'input_SHA256': digest(input_wheel),
        'wheel': str(output_wheel), 'version': version, 'bytes': output_wheel.stat().st_size,
        'SHA256': digest(output_wheel), 'renamed': renamed, 'ABI': abi_proof,
        'commands': commands, 'sources': sources}, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_wheel', type=Path)
    parser.add_argument('output_wheel', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--patchelf', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    args = parser.parse_args()
    repair(args.input_wheel, args.output_wheel, args.report, args.patchelf, args.source_manifest)
