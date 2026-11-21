"""Check source files or Git's index for accidental private release content.

This is a conservative release check, not a guarantee that all sensitive data
can be detected. Review the staged diff as well. No network requests are made.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.env.example', '.gitattributes', '.gitignore', '.python-version',
    'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'SECURITY.md',
    'CONTRIBUTING.md', 'CHANGELOG.md', 'GUI_GUIDE.md',
    'GENERATE_ONE_APPLICATION.md', 'pyproject.toml', 'uv.lock',
    'job_radar.py', 'job_radar_gui.py', 'Open Job Radar.cmd',
}
SOURCE_DIRS = {'job_radar', 'scripts', 'tests', 'templates', 'examples',
               'docs', 'third_party_licenses', '.github'}
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
             '.pytest-tracking-temp', 'build', 'dist', 'htmlcov',
             'test-results', 'tmp', 'work', 'data', 'applications', 'reports'}
PRIVATE_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.bak', '.zip', '.pdf',
                    '.doc', '.docx', '.png', '.jpg', '.jpeg', '.pem', '.key',
                    '.log', '.pyc', '.pyo'}
SECRET_PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
    'OpenAI-style credential': re.compile(r'\bsk-(?:proj-|svcacct-|ant-)?[A-Za-z0-9_-]{32,}'),
    'GitHub credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'Google API credential': re.compile(r'\bAIza[A-Za-z0-9_-]{30,}'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'credential in URL': re.compile(r'https?://[^\s/<>:@]+:[^\s/<>@]{16,}@'),
}


def path_problem(name: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or '\\' in name or ':' in name or '..' in path.parts:
        return 'unsafe source path'
    if not path.parts:
        return 'empty source path'
    if path.name.startswith('.env') and name != '.env.example':
        return 'environment/credential file'
    if path.name.casefold() == 'connections.csv':
        return 'private LinkedIn connections export'
    notice_build = path.parts[:2] == ('third_party_licenses', 'build')
    if any((part in SKIP_DIRS and not (notice_build and part == 'build'))
           or part.endswith('.egg-info') or part.startswith('.venv')
           for part in path.parts):
        return 'private workspace or generated file'
    if path.suffix.casefold() in PRIVATE_SUFFIXES or '.sqlite' in path.name.casefold():
        return 'private document, database, archive or generated file'
    if len(path.parts) == 1 and name not in ROOT_FILES:
        return 'unreviewed root file; update the source allowlist after review'
    if len(path.parts) > 1 and path.parts[0] not in SOURCE_DIRS:
        return 'unreviewed source directory'
    return None


def check_content(name: str, content: bytes) -> list[str]:
    problems = []
    issue = path_problem(name)
    if issue:
        problems.append(f'{name}: {issue}')
    if len(content) > 2_000_000:
        return problems + [f'{name}: unexpectedly large source file']
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return problems + [f'{name}: non-text file needs explicit review']
    if '\x00' in text:
        problems.append(f'{name}: binary content')
    if name.casefold().endswith('.csv'):
        for line in text.splitlines():
            cells = next(csv.reader([line]), [])
            if {'first name', 'last name', 'company', 'position'} <= {c.strip().casefold() for c in cells}:
                problems.append(f'{name}: possible private connections export')
                break
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            problems.append(f'{name}: possible {label} (value omitted)')
    if name == '.env.example':
        for line in text.splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            if any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and value.strip():
                problems.append(f'{name}: credential example must be empty')
    return problems


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError('Git could not read this repository; initialize and stage the reviewed source first.')
    return result.stdout


def staged_files(root: Path = ROOT) -> dict[str, bytes]:
    top = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != root.resolve():
        raise ValueError('Run this check in a separate Job Radar repository, not a parent workspace repository.')
    result = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, encoded_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        name = encoded_name.decode('utf-8')
        if mode not in {'100644', '100755'} or stage != '0':
            raise ValueError(f'{name}: linked file, submodule or unresolved merge is not releasable')
        result[name] = git(root, 'cat-file', 'blob', oid)
    if not result:
        raise ValueError('No staged source files to check.')
    return result


def working_files(root: Path = ROOT) -> dict[str, bytes]:
    result = {}

    def visit(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            name = path.relative_to(root).as_posix()
            notice_build = name == 'third_party_licenses/build'
            if (path.name in SKIP_DIRS and not notice_build) or path.name.startswith('.venv') or path.name.endswith('.egg-info'):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
                raise ValueError(f'{name}: linked files/folders cannot enter the release')
            if path.is_dir():
                visit(path)
            else:
                result[name] = path.read_bytes()
    visit(root)
    return result


def check_files(files: dict[str, bytes]) -> list[str]:
    return [issue for name, content in files.items() for issue in check_content(name, content)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracked', action='store_true', help='Read exact staged/index content, including already tracked private files.')
    args = parser.parse_args()
    try:
        files = staged_files() if args.tracked else working_files()
        issues = check_files(files)
    except (ValueError, OSError) as exc:
        print(f'Release check failed: {exc}')
        return 1
    if issues:
        print('\n'.join(issues))
        print('Release check failed. Review the files before sharing.')
        return 1
    print(f'Checked {len(files)} source files: no flagged paths or credential patterns. Review the content and staged diff before publishing.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
