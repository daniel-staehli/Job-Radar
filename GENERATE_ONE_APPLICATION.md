# Prepare one CV and cover letter

Complete [setup](README.md#setup-on-windows) and replace the fictional profile before real generation. The [desktop guide](GUI_GUIDE.md) covers provider selection, supporting sources and direct editing. This guide gives a terminal alternative that processes one saved vacancy at a time.

Run these commands in PowerShell from the repository folder. Define this shortcut once per terminal:

```powershell
function radar { & ".\.venv\Scripts\python.exe" -X utf8 ".\job_radar.py" @args }
```

## 1. Import and review a vacancy

```powershell
radar import-url "PASTE_THE_JOB_URL_HERE"
```

This visits the webpage without AI and writes a YAML draft under `data/imported_jobs`. It does not yet save a database record. Open the printed YAML file and check the company, title, location, URL and description. If extraction fails, fill the missing fields from the original vacancy. For a multiline description, use `description: |` and indent every following text line by two spaces.

```powershell
radar import-job --file "PASTE_THE_PRINTED_YAML_PATH_HERE" --dry-run
radar import-job --file "PASTE_THE_PRINTED_YAML_PATH_HERE"
```

The second command prints a job ID. Keep it for the following steps:

```powershell
$jobId = "PASTE_THE_PRINTED_JOB_ID_HERE"
```

Use `radar jobs` to find an already saved ID. To save a complete extraction immediately, `import-url "URL" --save` is also available, but review the resulting fields. `--select` additionally appends it to a selection file; it does not replace that list. The examples below use `--job`, so they ignore selection-file contents and process only the named job.

## 2. Add optional reference documents

```powershell
radar references add "C:\path\to\CV.pdf" --type cv --label "Concise CV example"
radar references add "C:\path\to\Letter.pdf" --type cover_letter --label "Motivation letter example"
radar references list
```

Replace those paths with actual files. PDF, DOCX, TXT, MD and TEX are supported. The library copies and extracts them locally, without altering the originals or calling AI. Its folder is named `data/reference_cvs` for compatibility, including cover letters. PDFs must contain readable text; no OCR is performed. Files are limited to 10 MiB and PDFs to 20 pages.

```powershell
$referenceId = "PASTE_THE_PRINTED_REFERENCE_ID_HERE"
radar references show $referenceId
```

References guide writing style and emphasis. Candidate facts come from `data/master_profile.yaml`, not automatically from the reference files. To draft with per-job papers, articles or notes, use the desktop **Supporting sources** and **Create documents** actions. This terminal workflow does not load those per-job sources. A saved source URL alone is not fetched in either workflow.

## 3. Review your profile and prepare local inputs

Check the profile's dates, claims, skills and contact details. Replace all fictional content and set `example_only: false` only after review. With the example flag still enabled you can inspect local previews, but live generation is blocked.

Create a fresh output folder and prepare the request:

```powershell
$runFolder = "applications\run-" + (Get-Date -Format "yyyyMMdd-HHmmss")
radar generate-applications --job $jobId --reference $referenceId --offline --dry-run --no-compile --output-root $runFolder
```

Omit `--reference $referenceId` to use no examples. Repeat `--reference` for other IDs, up to three in total. The combination of **`--offline --dry-run` stays local**: it uses saved vacancy text, prepares inputs and makes no model request.

Review `job_description.md`, `prompt.json` and `generation_preview.json` in the printed vacancy folder. These contain private application inputs. They are previews of source material, not completed documents. If a completed run already exists, the dry-run preview is written separately to preserve its inputs.

## 4. Generate both drafts

The terminal `--backend` options are separate from the desktop's saved **AI settings**. The default is `codex`, using the installed helper and sign-in. For this legacy terminal path, `JOB_RADAR_CODEX_MODEL` sets an exact model ID; if unset, the helper chooses its built-in default. Reasoning defaults to `medium` and can be changed with `JOB_RADAR_CODEX_REASONING`. The helper ignores user configuration during generation, so this path does not inherit the model shown in another Codex window or its configuration file. Inspect `generation_preview.json` before running. Choose the desktop for its model/reasoning selectors.

`--backend api` uses the OpenAI API workflow and API billing. Set `OPENAI_API_KEY` outside Job Radar and set `JOB_RADAR_MODEL` to a model available to that account. The other provider adapters are available through the desktop.

```powershell
radar status
radar generate-applications --job $jobId --reference $referenceId --offline --no-compile --output-root $runFolder
```

Removing `--dry-run` makes a live writing request using the selected backend. **`--offline` prevents fetching the vacancy again; it does not prevent the AI request.** This terminal request includes the saved job text, profile and selected reference text. It uses the backend's allowance or billing and does not submit an application.

Successful output includes `cv.tex`, `motivation_letter.tex`, `fit_analysis.md` and audit/context files. If `VALIDATION_FAILED.txt` appears, inspect its findings; the run does not produce new validated drafts. A fresh folder avoids confusing older outputs with the current attempt. Automated checks still require human review of factual claims.

The examples omit compilation. If a local LaTeX installation with `latexmk` is ready, omit `--no-compile` to request PDFs. Inspect the compiler logs and PDF layout. An external online LaTeX editor is a separate transfer of the documents to that service.

## 5. Edit, revise and track

In **Review & improve**, select the active application. Use **Edit text / notes** for direct local edits, or enter feedback and choose **Improve selected document** for another AI request. Both update the active application folder, keep previous states in `.history`, and preserve the companion document. Private annotations stay outside the documents and AI inputs. Review the comparison and compile the new source if you need a revised PDF.

Editing `.tex` outside Job Radar does not update its saved structured draft. The supported editor detects external changes; do not assume a subsequent AI request includes them. Changes to the main profile or reference selection require a new initial draft to become part of its saved context.

Terminal `--resume` reuses model output only when its saved fingerprint matches the inputs and settings. Changed inputs are refused. If no cached response exists, `--resume` may make a model request; it is not an offline-only flag.

After submitting the application yourself, record what you actually sent:

```powershell
radar track $jobId --status applied --cv-version "CV_sent_v1.pdf" --cover-letter "Letter_sent_v1.pdf" --next-action "Follow up"
radar dashboard
```

The document fields are labels or paths, not uploads. Use `radar --help` or a command's `--help` for other tracking and report options. The experimental `search` command is separate and may incur API charges; it is not needed for this workflow.
