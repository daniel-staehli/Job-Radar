# Release validation

## 0.10.0 beta · 22 November 2026

- On Windows with Python 3.12, the source ZIP was extracted into a new directory. A fresh virtual environment installed the locked dependencies from the package index. Initialization created the three fictional example files, CLI help opened, and the desktop app opened all eight tabs with **Recommended** selected in Find jobs. The app closed successfully.
- The source and history privacy check covered 194 source files and 202 earlier file versions. It flagged no private paths, contact exports, known personal contact details or credentials.
- The [Tests workflow](https://github.com/daniel-staehli/Job-Radar/actions/workflows/tests.yml) downloads the revision under test on Windows and macOS, follows the editable pip installation instructions, initializes the example workspace and opens the app. Its run history records the platform results for each revision. Interactive checks with your own documents and models remain a separate step.

## 0.9.0 beta · 17 November 2026

- Prepared from the previously sanitized public source, with Word export and local Ollama discovery reviews merged from the working application. Personal configuration, reference documents, databases, generated applications and credentials are excluded.
- Automated allowlist and credential-pattern scan: 180 source files checked. Targeted private-contact, personal-path and career-fact scans found no matches. The two existing commits use GitHub noreply identities; 173 distinct historical blobs passed the same checks. Scanning cannot guarantee the absence of all private information.
- Windows/Python 3.12 regression checks: **1,153 passed, 2 skipped** across the full run and targeted rerun. The full run identified one stale table-column test assertion; the new AI-match column moved the old positional lookup. The test now addresses the Terms column by name, and all seven discovery integration checks pass, including the newly added Ollama sorting check. The two skips require Windows symlink privileges. A short temporary test directory avoids the host's unusually long default path.
- No paid AI calls or live Ollama requests were made by this release check. Word files have structural tests; their visual appearance still needs review in Word or another document editor.

## Previous 0.8.0 baseline checks (29 October)


- **Public source regression suite:** 1,121 passed, 2 skipped, 0 failures/errors on Windows with Python 3.12. The two skipped checks require permission to create directory symlinks. GUI, storage, import, schema, cancellation, history and provider request behavior were tested with synthetic inputs and mocked model responses.
- **Fresh installation:** created a separate source folder and new virtual environment, installed the locked dependencies with `uv sync --offline --frozen --all-extras`, initialized the fictional workspace and opened command-line help successfully. Dependencies came from the local cache; this was not a GitHub download test. The restricted test environment required its temporary build directory inside the writable workspace.
- **Installed workflow checks:** 61 checks passed in that fresh environment, including native Tk startup/save/close, initialization/privacy guards, profile and text-import controls, editing in the same folder, generation abort and Ollama context controls.
- **Source privacy checks:** the source allowlist/credential-pattern check passed. A targeted review found none of the known personal names, private project paths or application identifiers. These checks supplement manual review; they cannot detect every possible sensitive value.
- **Dependency notices:** the 27 locked dependency/version pairs match the inventory; all 36 retained notice-file hashes match. No dependencies or third-party implementation were added by the recent workflow fixes. The original MIT licence and separate dependency notices remain included.

The local application previously completed a real native Ollama CV/letter request with the updated context handling. That is evidence for that specific local configuration, not a validation of every model/provider or of the prose quality. The public suite makes no billed model requests.

## Outstanding at the 0.9.0 check

- GitHub-hosted CI after the repository exists. The supplied workflow targets Windows with Python 3.11 and 3.12; this document does not claim those remote runs have happened.
- A real download and installation from GitHub on another Windows setup and on macOS. Linux also lacks end-to-end installation verification. Non-Windows file-opening behavior has mocked tests only.
- The user's manual checks of imported details, tracking, profile editing, reference selection, model settings, generation, abort, direct editing and improvements with their chosen providers.
- Full PDF layout inspection on the user's LaTeX installation, especially page breaks and longer contact/recipient fields.
- Writing-quality and output-language investigation described in [known issues](KNOWN_ISSUES.md).

## Distribution boundary

Publish only the reviewed source checkout or the archive made by `scripts/build_source_release.py`. That builder reads the reviewed Git index, checks each path, and verifies every archived file against the staged content. It does not include the virtual environment, personal database, profile, references, drafts, logs or local test artifacts, and it does not upload anything.

The initial release is suitable as an early **source release**, with the remaining checks disclosed. It is not a standalone installer or a claim of full Windows/macOS/Linux support.
