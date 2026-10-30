"""Create an ignored local workspace from fictional examples; never overwrite."""
from __future__ import annotations

import argparse
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
FILES = ('master_profile.yaml', 'search_strategies.yaml', 'selected_jobs.yaml')


def _check_local(path: Path) -> None:
    for part in (*reversed(path.absolute().parents), path.absolute()):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError('Choose a local workspace without linked folders.')


def initialize(root: Path = ROOT) -> dict[str, list[str]]:
    root = Path(root).absolute()
    _check_local(root)
    sources = {name: root / 'examples' / name for name in FILES}
    contents = {}
    for name, source in sources.items():
        _check_local(source)
        contents[name] = source.read_bytes()
    destination = root / 'data'
    _check_local(destination)
    for name in FILES:
        _check_local(destination / name)
    destination.mkdir(exist_ok=True)
    result = {'created': [], 'kept': []}
    for name, data in contents.items():
        target = destination / name
        try:
            with target.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            result['kept'].append(name)
        else:
            result['created'].append(name)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = initialize()
    for name in result['created']:
        print(f'Created data/{name}')
    for name in result['kept']:
        print(f'Kept existing data/{name}')
    print('The example profile is fictional. Replace it with your own verified facts, then set example_only: false before AI generation.')
    print('No jobs were imported, no model was called, and no existing files were overwritten.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
