"""Build a source-only ZIP from checked Git index content. Never publish it."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile

if __package__:
    from .check_public_release import ROOT, check_files, staged_files
else:
    from check_public_release import ROOT, check_files, staged_files


def build(destination: Path, root: Path = ROOT) -> str:
    destination = destination.resolve()
    if destination.suffix.lower() != '.zip':
        raise ValueError('Choose an output filename ending in .zip.')
    if destination.is_relative_to(root.resolve()):
        raise ValueError('Save the release archive outside the source repository.')
    changed = subprocess.run(['git', '-C', str(root), 'diff', '--quiet'], check=False)
    if changed.returncode:
        raise ValueError('Tracked files have unstaged changes. Review and stage the final source before building.')
    files = staged_files(root)
    issues = check_files(files)
    if issues:
        raise ValueError('\n'.join(issues))
    required = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'uv.lock',
                'pyproject.toml', 'scripts/initialize_workspace.py',
                'examples/master_profile.yaml', 'job_radar/gui.py'}
    if not required.issubset(files):
        raise ValueError('Required source files are missing from the Git index.')
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(f'job-radar/{name}', content)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity check failed.')
        if len(archive.namelist()) != len(files):
            raise ValueError('Archive file count differs from the reviewed source.')
        for name, content in files.items():
            if archive.read(f'job-radar/{name}') != content:
                raise ValueError('Archive content differs from the reviewed source.')
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New ZIP path outside the repository; existing files are not replaced.')
    args = parser.parse_args()
    try:
        digest = build(args.destination)
    except (ValueError, OSError) as exc:
        print(f'Release was not built: {exc}')
        return 1
    print(f'Source archive: {args.destination.absolute()}\nSHA-256: {digest}\nNothing has been uploaded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
