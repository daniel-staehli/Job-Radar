# Architecture

Job Radar is a local Python application with a Tkinter desktop interface and a command-line interface. SQLite stores job and application records; local files hold profiles, source material, reference documents and versioned outputs. There is no hosted application backend.

The main workflow is deliberately reviewable:

```text
Manual URL / pasted text / file / employer feed
          |
          v
Review vacancy --> save in SQLite --> track application progress
                          |
Verified profile --------+
Selected CV/letter examples|
Included job sources -----+
                          v
                 Local input review
                          |
                  Explicit AI request
                          v
                Structured validation
                          |
                          v
                 LaTeX CV + letter
                          |
              Manual edit or AI feedback
                          v
          Updated files + preserved history
```

## Module responsibilities

| Area | Main modules |
| --- | --- |
| Desktop and UI-independent operations | `gui.py`, `gui_services.py`, `gui_theme.py`, `gui_*.py` |
| Import, normalization and storage | `url_import.py`, `text_import.py`, `ai_text_import.py`, `manual_import.py`, `normalize.py`, `db.py` |
| Application lifecycle and reports | `tracking.py`, `dashboard.py`, `report.py` |
| Employer presets and local ranking | `discovery.py`, `ranking.py` |
| Candidate profile, match review and schemas | `profile.py`, `job_match.py`, `models.py`, `tailoring_schema.py` |
| Reference library and per-job context | `references.py`, `job_context.py` |
| Provider adapters and local model metadata | `providers.py`, `codex_client.py`, `model_catalog.py`, `openai_client.py` |
| Generation, rendering and revisions | `cv.py`, `revisions.py`, `manual_edits.py`, `draft_history.py`, `letter_layout.py` |
| Cancellation and incomplete output | `cancellation.py`, `generation_state.py` |
| Terminal commands and experimental search | `cli.py`, `search.py` |

## Data boundaries

`examples/` contains fictional configuration suitable for public distribution. `scripts/initialize_workspace.py` copies configuration into `data/` without overwriting existing files. The example profile is marked `example_only`; a user must replace and review its facts before enabling live generation.

The working `data/`, `applications/`, `reports/` and SQLite files are private runtime data, not source fixtures. They are ignored by Git. The application does not encrypt them or control separate cloud-sync software. A source checkout is the supported installation layout; this release is not a self-contained executable.

## Identity and history

Imports normalize vacancy records and deduplicate known identities. Employer feed records retain provider posting identity, allowing separate vacancies with the same company/title to remain separate. Re-imports preserve application history. Saved document context retains the job identity so later versions remain attached to the intended vacancy.

Application status and other progress fields have dated history. A vacancy's availability is separate from application outcome: a role leaving a feed does not make the application rejected or withdrawn.

## Generation and saved context

The verified profile is the source of candidate facts. Reference CVs and letters guide style, while supporting sources supply employer/research context. External content is not authority to add candidate achievements.

Generation prepares a structured request, validates the response against schemas and project rules, and renders through Jinja2 LaTeX templates. Validation identifies known unsupported claims and invalid fact references; it cannot prove every sentence true. A user must still review content and layout.

Each saved version retains inputs, provenance, context and document hashes. Fingerprints prevent resume from reusing a response with changed inputs. A dry run writes review artifacts without calling a model. In the terminal, `--offline` prevents refetching job text; only `--dry-run` also avoids generation.

AI revisions use the selected draft's saved context, not silently updated profile or reference files. Desktop improvements and manual edits update active files in place, retaining previous states and provenance in `.history`. The companion document is preserved. Private annotations are separate from content passed to the model. Direct external edits to rendered `.tex` cannot safely be reconstructed as structured edits, so hash checks identify those changes.

## Network and provider behaviour

URL extraction and employer refresh are explicit network actions without AI. The desktop does not run background discovery. AI generation, improvement, vacancy extraction, profile suggestions and match reviews are explicit actions. Setup checks do not establish live account/model access. Native Ollama has separate model-metadata requests and a configurable context budget.

Provider adapters use bounded requests, validate configuration and structured output, and do not automatically fall back to a different provider. Credentials are supplied outside saved settings. Official provider endpoints are fixed except for the explicit compatible-server option. See [integration details](INTEGRATIONS.md).

## Tests and change strategy

Tests exercise imports, storage, tracking, generation/revision validation, request construction and desktop integration using temporary workspaces, synthetic records and mocks. GUI tests need Tk/display support. No paid model requests are needed for the test suite.

Prefer a small direct implementation when it is easier to maintain; adopt a library when it offers a clear practical advantage with suitable licensing. Keep manual import and deterministic tracking useful independently of provider availability. The older AI search path remains experimental and is not required by the everyday desktop workflow.
