# Contributing

Job Radar aims to stay useful for daily job applications. Prefer changes that make review, tracking and document ownership clearer. Keep optional model usage explicit and retain a useful workflow when no provider is configured.

## Set up and test

Follow [README setup](README.md#setup-on-windows). The editable `.[dev]` install includes the test dependencies. The repository's `uv.lock` is available for a pinned development install with uv:

```powershell
uv sync --frozen --all-extras
.\.venv\Scripts\python.exe scripts\initialize_workspace.py
.\.venv\Scripts\python.exe -m pytest
```

Use fictional example data or new synthetic fixtures. Tests should use temporary folders and mocked network/model responses, without credentials, paid requests or a real user's files. Tk tests need a display; some operating-system permission checks may skip where unavailable. A skipped check should not be described as verified.

## Make a focused change

Explain the problem, resulting behaviour and how you checked it. Add tests for meaningful failure cases, especially saved-data preservation, job identity, request boundaries and versioning. Update the guide when a visible control or workflow changes.

Preserve these design choices:

- Manual import and application tracking work without AI.
- Preview/review steps are distinct from model requests.
- Candidate facts, reference style, external context and private annotations have different roles.
- Earlier document versions and application history remain recoverable.
- Network requests, provider selection and local file writes have clear ownership.

The code was developed with AI assistance; AI-assisted contributions are welcome when their author understands and reviews the result. Describe relevant limitations rather than implying that automated checks verified live provider access or writing quality.

## Third-party material

Do not copy code, assets or substantial documentation without checking the source's licence and applicable notices. If a component offers a clear benefit, record its origin, version and licence; retain required notices and review added dependencies. A project being public on GitHub does not by itself permit copying.

Original contributions are submitted under this repository's [MIT licence](LICENSE). Dependencies retain their own terms. Update [third-party notices](THIRD_PARTY_NOTICES.md) and the inventory when adopted components change.

## Keep the public tree clean

Never add real profiles, reference CVs, applications, employer correspondence, credentials or copied private logs. Use `example.com` addresses and fictional organisations in examples. Do not add runtime dependencies or compiled installers unless their distribution and notices have been reviewed.

Before proposing a public commit, inspect the staged diff and run:

```powershell
.\.venv\Scripts\python.exe scripts\check_public_release.py --tracked
```

The checker supplements human review; it cannot recognise every possible personal detail. Use the [security reporting route](SECURITY.md) for sensitive findings rather than posting them in an ordinary issue.

## Prepare a source archive

Use a separate Git repository containing only the public source. Review and stage the intended files, inspect `git diff --cached`, and run the tests. Then create a new archive outside the repository:

```powershell
.\.venv\Scripts\python.exe scripts\check_public_release.py --tracked
.\.venv\Scripts\python.exe scripts\build_source_release.py ..\Job_Radar_Source.zip
```

The builder packages the exact Git index, checks the source allowlist and credential patterns, and verifies the resulting archive. It refuses unstaged changes to tracked files and an existing output filename. Untracked files are not included. The printed SHA-256 identifies that archive; nothing is uploaded. Do not replace this with a ZIP of a working folder containing personal data or an installed environment.
