"""Privacy and first-run boundaries for the public source distribution."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile

import pytest

from scripts import check_public_release as release
from scripts.build_source_release import build
from scripts.initialize_workspace import FILES, initialize
from job_radar.config import ROOT
from job_radar.cv import generate_cvs
from job_radar.db import JobDatabase
from job_radar.search import run_search


def test_initializer_never_overwrites_local_profile(tmp_path):
    shutil.copytree(ROOT / 'examples', tmp_path / 'examples')
    (tmp_path / 'data').mkdir()
    profile = tmp_path / 'data' / 'master_profile.yaml'
    profile.write_text('local: already reviewed\n', encoding='utf-8')
    result = initialize(tmp_path)
    assert result['kept'] == ['master_profile.yaml']
    assert len(result['created']) == 2
    assert profile.read_text() == 'local: already reviewed\n'
    assert initialize(tmp_path) == {'created': [], 'kept': list(FILES)}
    assert not list(tmp_path.rglob('*.sqlite3'))


def test_initializer_missing_examples_has_no_partial_writes(tmp_path):
    (tmp_path / 'examples').mkdir()
    (tmp_path / 'examples' / FILES[0]).write_text('example_only: true\n')
    with pytest.raises(FileNotFoundError):
        initialize(tmp_path)
    assert not (tmp_path / 'data').exists()


def test_example_profile_stops_live_generation_before_side_effects(tmp_path):
    with pytest.raises(ValueError, match='fictional example profile'):
        generate_cvs(db=JobDatabase(tmp_path / 'missing.sqlite3'),
                     selection_path=ROOT / 'examples' / 'selected_jobs.yaml',
                     profile_path=ROOT / 'examples' / 'master_profile.yaml',
                     template_path=ROOT / 'templates' / 'cv_default.tex.j2',
                     output_root=tmp_path / 'drafts', dry_run=False, compile_pdf=False)
    assert not list(tmp_path.iterdir())


def test_example_profile_stops_live_search_before_side_effects(tmp_path):
    with pytest.raises(ValueError, match='fictional example profile'):
        run_search(db=JobDatabase(tmp_path / 'missing.sqlite3'),
                   profile_path=ROOT / 'examples' / 'master_profile.yaml',
                   strategies_path=ROOT / 'examples' / 'search_strategies.yaml',
                   mode='initial', passes=1, since_days=7, max_results=2,
                   dry_run=False, prompt_dir=tmp_path / 'prompts')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', ['data/profile.yaml', 'applications/draft.tex',
                                 'docs/cv.pdf', '.env', 'jobs.sqlite3',
                                 '../outside.py', 'tests/__pycache__/test.pyc',
                                 'secrets/password.txt'])
def test_private_release_paths_rejected_even_if_git_tracked(name):
    assert release.check_content(name, b'innocent looking content')


def test_scanner_omits_detected_secret_value():
    # Assembled synthetic credential, never valid and never sent anywhere.
    fake = 'sk-' + 'a' * 40
    issues = release.check_content('README.md', fake.encode())
    assert issues and 'credential' in issues[0]
    assert fake not in repr(issues)
    assert release.check_content('.env.example', b'OPENAI_API_KEY=not-empty\n')
    assert not release.check_content('.env.example', b'OPENAI_API_KEY=\n')


def test_release_rejects_private_contacts_even_when_export_renamed():
    assert release.check_content('docs/Connections.csv', b'')
    assert release.check_content('data/network.sqlite3', b'')
    sample = b'Notes:\r\n\r\n"First Name","Last Name",URL,Company,Position\r\n'
    assert release.check_content('examples/team.csv', sample)
    assert not release.check_content('examples/jobs.csv', b'Company,Position\n')


def test_original_build_notices_are_not_excluded_as_generated_build_output():
    files = release.working_files(ROOT)
    inventory = json.loads((ROOT / 'docs' / 'dependency-inventory.json').read_text(encoding='utf-8'))
    def notices(value):
        if isinstance(value, dict):
            if 'path' in value and str(value['path']).startswith('third_party_licenses/'):
                yield value
            for child in value.values():
                yield from notices(child)
        elif isinstance(value, list):
            for child in value:
                yield from notices(child)
    records = list(notices(inventory))
    assert any('/build/' in item['path'] for item in records)
    for item in records:
        assert item['path'] in files
        assert not release.path_problem(item['path'])
        assert hashlib.sha256(files[item['path']]).hexdigest() == item['sha256']


def git_repo(tmp_path):
    if not shutil.which('git'):
        pytest.skip('Git is required for index/archive integration checks')
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    return root


def stage(root):
    subprocess.run(['git', '-C', str(root), 'add', '--all'], check=True, capture_output=True)


def test_index_check_reads_staged_blob_even_after_working_file_is_cleaned(tmp_path):
    root = git_repo(tmp_path)
    (root / 'README.md').write_text('sk-' + 'b' * 40, encoding='utf-8')
    (root / 'data').mkdir()
    (root / 'data' / 'profile.yaml').write_text('local: private\n')
    stage(root)
    (root / 'README.md').write_text('clean working copy\n')
    issues = release.check_files(release.staged_files(root))
    assert any('credential' in item for item in issues)
    assert any('data/profile.yaml' in item for item in issues)
    assert not release.check_files(release.working_files(root))


def test_archive_only_contains_reviewed_index_and_preserves_existing_file(tmp_path, monkeypatch):
    root = git_repo(tmp_path)
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'uv.lock',
                 'pyproject.toml', 'scripts/initialize_workspace.py',
                 'examples/master_profile.yaml', 'job_radar/gui.py'):
        path = root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text('Synthetic source content\n', encoding='utf-8')
    stage(root)
    (root / 'data').mkdir()
    (root / 'data' / 'private.txt').write_text('excluded local data')
    archive_path = tmp_path / 'release.zip'
    monkeypatch.chdir(root)
    digest = build(Path('../release.zip'), root)
    assert len(digest) == 64
    with zipfile.ZipFile(archive_path) as archive:
        assert len(archive.namelist()) == 8
        assert not any('/data/' in name for name in archive.namelist())
    original = archive_path.read_bytes()
    with pytest.raises(FileExistsError):
        build(archive_path, root)
    assert archive_path.read_bytes() == original
    (root / 'README.md').write_text('Changed after staging\n')
    with pytest.raises(ValueError, match='unstaged changes'):
        build(tmp_path / 'second.zip', root)
    assert not (tmp_path / 'second.zip').exists()
