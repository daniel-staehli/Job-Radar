# Change history

## 0.9.0 — 17 November 2026 (beta)

- Export completed applications to editable Word documents, with separate CV and letter layouts, supported custom templates and no AI request.
- Review selected employer-feed vacancies against the saved profile using local Ollama models; cache evidence-based assessments, sort matches and stop a running batch.
- Preserve the public release's fictional example data, privacy guards, generic templates and tests.
- No new dependencies. This remains an experimental source release; Word visual layout, live Ollama reviews and installation on another Windows/macOS machine need manual verification.


This file describes the public source package. It is not a record of private application data or a claim that a GitHub release has already been published.

## Unreleased

The first public source candidate includes the local workflow fixes below. It has not yet been published to GitHub.

- Editable verified profile, reviewed suggestions from a reference CV, and explicit AI job-match reviews.
- Import from pasted vacancy text and text-based files, with optional evidence-backed AI extraction for missing fields. Imports can run independently of document generation.
- Abort for CV/letter generation, cancellation-aware provider/process handling, and separate records for incomplete attempts.
- Better extraction from labelled vacancy pages, retaining the stated company, title, location and description when unambiguous.
- Initial document schemas restrict role keys and fact identifiers to the supplied profile.
- Native Ollama context selection (32,768 tokens by default), a ten-minute local generation timeout and bounded failure diagnostics without raw response/reasoning text.
- Desktop improvements and manual edits update the active application folder with prior states retained in `.history`; cover-letter layout controls preserve body wording.
- Public source synchronization, synthetic regression checks, setup guidance and documented writing/language limitations. No new dependencies were added.

- New desktop applications use one compact `company_YYYY-MM-DD_id` folder, removing the extra dated run level. Names adapt to the full path length, collisions cannot overwrite existing runs, and saved historical folders retain their paths.

- Added a native local Ollama provider with model-specific thinking choices, explicit metadata lookup, revalidation before generation, and unchanged legacy compatible-provider behavior. Existing AI settings are preserved.
- Made AI settings scrollable with accessible Save/Cancel controls.
- Fixed file/folder opening on macOS and Linux with the platform opener. Mocked launch tests cover all three platforms; full non-Windows validation remains outstanding.

- Replaced ambiguous reference highlighting with explicit available/selected lists, Add/Remove controls and a three-document limit.
- Corrected cover-letter layout ordering and typography with explicit contact, subject, body and signature blocks. New generations use the corrected template; saved versions retain their original templates.
- Refined letter-writing guidance to follow selected examples without copying unsupported claims, and made the writing request self-contained.
- Added elapsed time to background-action status. Model and reasoning settings are unchanged.

- Fixed empty fields when importing BambooHR vacancy URLs: read the public job detail response and the page’s employer branding, verify the vacancy identity, and retain the full description without AI.

- Fixed lost edits in **Jobs**: text fields, dates and notes now save automatically after a brief pause, and pending edits are saved before switching jobs, refreshing, filtering or closing. **Save progress** still saves immediately.
- Invalid dates or save failures retain the form and show an unsaved notice instead of allowing pending edits to be discarded.

## 0.8.0 — prepared for public distribution

- Per-job supporting sources: papers, company articles, URLs, excerpts and notes, with explicit selection for document generation.
- Direct editing of saved CV and cover-letter text, private annotations and preserved document versions.
- Codex model and supported reasoning selectors based on local client metadata, with a separate unverified custom-model option.
- Immediate saving and display refresh when application status changes.
- Clearer empty-state guidance and employer suggestions for **Find jobs**.
- Source-only setup, fictional configuration, an initialization script, MIT licensing and third-party notices for the public package.
- A guard prevents live generation and experimental AI search while the example profile is still marked `example_only`.

## Earlier workflow foundations

- SQLite persistence, manual job import, deduplication and application lifecycle history.
- Local dashboard, follow-ups, reference CV/letter library and structured fact bank.
- Paired CV/letter generation, local input review, saved context and separate AI revisions.
- Employer presets for official Ashby and Greenhouse feeds, cached review and local term ranking.
- Desktop provider selection for Codex, OpenAI, compatible endpoints, Anthropic and Gemini.

Automated checks use synthetic records and mocked model responses. Live provider generation and full PDF rendering remain unverified for this source release.
