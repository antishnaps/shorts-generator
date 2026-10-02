"""Package an already checked PyInstaller output with licenses and source links.

Usage: python tools/package_portable.py SOURCE_DIR DIST_DIR OUTPUT_ZIP
The ZIP is created exclusively and existing releases are never replaced.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile
from packaging.requirements import Requirement


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def release_files(dist: Path):
    # Compatibility with older PyInstaller imageio hooks that reclassify the
    # fallback EXE as a binary after our data exclusions. Runtime always uses
    # the full audited FFmpeg beside ffprobe, so do not ship a second copy.
    return sorted(path for path in dist.rglob('*') if path.is_file() and not (
        '/imageio_ffmpeg/binaries/' in '/' + path.relative_to(dist).as_posix()
        and path.suffix.lower() == '.exe'))


def dependencies(source: Path):
    pending = []
    for line in (source / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        text = line.split('#', 1)[0].strip()
        if text:
            pending.append(Requirement(text))
    found = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not any(requirement.marker.evaluate({'extra': extra})
                for extra in ('', *requirement.extras)):
            continue
        dist = metadata.distribution(requirement.name)
        key = dist.metadata['Name'].lower().replace('_', '-')
        if key in found:
            continue
        found[key] = dist
        pending.extend(Requirement(item) for item in (dist.requires or []))
    return found


def package(source: Path, dist: Path, archive: Path) -> dict:
    if archive.exists():
        raise FileExistsError('Refusing to replace an existing release archive')
    assert (source / 'RELEASE_MANIFEST.json').is_file()
    assert (dist / 'ContentBotPro.exe').is_file()
    check = json.loads((dist.parent / 'portable-check/result.json').read_text())
    if not check.get('ok'):
        raise RuntimeError('Portable checks must pass before packaging')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'SOURCE_ACCESS.md'):
        shutil.copy2(source / name, dist / name)
    licenses = dist / 'licenses'
    licenses.mkdir(exist_ok=False)
    inventory = []
    for name, dependency in sorted(dependencies(source).items()):
        version = dependency.version
        inventory.append({'name': dependency.metadata['Name'], 'version': version,
            'source': f'https://pypi.org/project/{dependency.metadata["Name"]}/{version}/#files'})
        for member in dependency.files or []:
            path = Path(str(member))
            if any(re.match(r'(?i)^(license|licence|copying|notice|copyright)(\W|$)',
                            part) for part in path.parts):
                original = Path(dependency.locate_file(member))
                if original.is_file():
                    target = licenses / name / Path(*[p for p in path.parts if p != '..'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    shutil.copy2(python_license, licenses / 'PYTHON_LICENSE.txt')
    shutil.copy2(source / 'docs/third_party/DENO_LICENSE.txt', licenses / 'DENO_LICENSE.txt')
    # Media runtime origin and configure flags are retained alongside GPL text.
    runtime_info = source / 'portable-media-notices'
    if not runtime_info.is_dir():
        raise RuntimeError('FFmpeg provenance and license notices are required')
    shutil.copytree(runtime_info, licenses / 'ffmpeg')
    (dist / 'DEPENDENCIES.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    (dist / 'README.txt').write_text(
        'ContentBot Pro Free | Windows 10/11 x64\n\n'
        '1. Extract the whole archive into a writable folder.\n'
        '2. Open ContentBotPro.exe. Keep the _internal folder next to it.\n'
        '3. Add your own API keys in Settings where required.\n'
        'Python, FFmpeg/ffprobe and Deno are included. No activation.\n'
        'Internet is required for online AI, voice and media services.\n'
        'Fonts and optional Vosk recognition models depend on your system.\n'
        'This build is not code-signed. Do not disable antivirus protection.\n\n'
        'Распакуйте ВСЮ папку и запустите ContentBotPro.exe.\n'
        'Python устанавливать не нужно. Папку _internal не удаляйте.\n'
        'Для онлайн-сервисов нужны интернет и собственные API-ключи.\n\n'
        'Source, build scripts and receiving details:\n'
        'https://github.com/antishnaps/shorts-generator\n'
        'GPL-3.0-only. Third-party licenses: licenses/, DEPENDENCIES.json.\n'
        'Matching source and build instructions: see SOURCE_ACCESS.md.\n', encoding='utf-8')
    files = release_files(dist)
    forbidden = {'config.json', 'config.json.backup', '.env', 'license.key', 'youtube_cookies.txt',
                 'activation_server.json', 'youtube_oauth_client.json'}
    for path in files:
        if path.name in forbidden or path.relative_to(dist).parts[0] in (
                'logs', 'data', '.git', 'generated', 'output', 'youtube_cache', 'cache'):
            raise RuntimeError('Private or generated data in portable release: ' + path.name)
    manifest = {'edition': 'free-portable', 'activation_required': False,
        'platform': 'Windows x64', 'code_signed': False, 'checks': check['checks'],
        'files': [{'path': p.relative_to(dist).as_posix(),
                   'bytes': p.stat().st_size,
                   'sha256': file_hash(p)}
                  for p in files]}
    (dist / 'PORTABLE_MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in release_files(dist):
            bundle.write(path, dist.name + '/' + path.relative_to(dist).as_posix())
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'archive': archive.name, 'bytes': archive.stat().st_size,
            'sha256': digest, 'files': len(manifest['files']) + 1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('dist', type=Path)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.dist, args.archive), indent=2))
