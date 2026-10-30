# Job Radar

**Status: Experimental prototype — early beta**

Job Radar was built through AI-assisted development (“vibe coding”), guided by practical use and iterative testing. Automated tests cover core workflows, but bugs and unexpected behaviour remain possible.

Review imported job details and generated documents before using them. AI output may contain factual errors, unsuitable wording, incorrect language or formatting problems. Keep backups of important application data.

Windows is the primary tested platform. Installation and everyday use on macOS and Linux remain unverified. The software is provided as-is under the MIT licence, without warranty.

A desktop workspace for finding, tracking and preparing job applications. Keep your decisions and documents together, with optional AI help for writing.

Job Radar follows one principle: **people make the judgment calls, Python handles repeatable work, and AI is used where it helps.** You can import jobs, track progress, rank vacancies by terms and edit documents without making an AI request.

## What it does

- Import a vacancy URL, pasted text or a text-based file, review the extracted details and save it locally. AI extraction is optional.
- Track application status, dates, interviews, document versions, contacts and next actions, with automatic saving of job-field edits.
- Check selected employers' public Ashby and Greenhouse boards; review results before adding them to the tracker.
- Rank cached vacancies by your priority terms, with visible matching explanations.
- Keep reference CVs and cover letters, plus papers, company articles and notes for individual jobs.
- Edit your verified profile, review AI-suggested additions from a CV, and request a job-match assessment when useful.
- Generate a CV and cover letter together from a verified fact bank; revise either with written feedback.
- Abort a running document generation; incomplete attempts are kept separate from completed drafts.
- Edit saved document text directly and keep private annotations, without AI.
- Choose Codex, local Ollama with model-specific thinking controls, OpenAI, an OpenAI-compatible endpoint, Anthropic or Gemini for explicit writing requests.

Documents use LaTeX templates. The desktop interface is built with Tkinter; the same local records are also available through a command-line interface.

## Setup on Windows

This is a **source release**, not a standalone installer. Install Python 3.11 or newer with Tkinter support first. A normal Windows Python installation includes Tkinter. Download or clone this repository, then open PowerShell in the extracted repository folder.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts\initialize_workspace.py
```

Initialization copies fictional example configuration into a local workspace without replacing existing files. **Replace the fictional profile with your own checked facts in My profile or `data/master_profile.yaml` before generating a real application.** Once reviewed, open that local YAML file and set `example_only: false`. The example-only flag blocks live generation and experimental AI search; local previews remain available. Reference CVs guide style; AI-suggested facts enter your profile only after you review and select them.

If you already use uv, run `uv sync --frozen --all-extras` instead of the first two commands, then run the same initialization command. This uses the versions recorded in `uv.lock`; the pip command installs versions allowed by `pyproject.toml`, which may be newer. The dependency inventory and bundled notices describe the locked versions.

Double-click **Open Job Radar.cmd** to open the window. Keep that launcher beside the source files and `.venv`. For an error message if the window does not open, run:

```powershell
.\.venv\Scripts\python.exe job_radar_gui.py
```

Dependencies are downloaded by the installation command and retain their own licences. No Python runtime, personal database, credentials or application documents are included in this source release. The launcher and primary desktop workflow target Windows. Linux and macOS users can run the Python entry point with Python/Tk and adapted virtual-environment paths (for example, `.venv/bin/python job_radar_gui.py`). File and folder buttons use the operating system opener: Windows, macOS `open`, or Linux `xdg-open`. The non-Windows branches have mocked tests; end-to-end use on macOS/Linux is not yet verified.

## First application

1. In **Add a job**, paste a vacancy URL and use **Fetch details**, or choose **Paste job text…** and paste text or load a supported file. Correct missing fields and click **Save reviewed job**.
2. In **Jobs**, select that role and add **Supporting sources** if useful. Paste relevant excerpts or load local files; saving a URL alone does not fetch its content.
3. Add optional examples in **Reference library**, choosing CV or cover letter as the type.
4. In **AI settings**, select the provider and model you want to use. Configure that provider's sign-in or API-key environment variable separately.
5. In **Create documents**, choose the job and references. Use **Review information (local)** to inspect the prepared source material, then **Generate CV + letter** when ready.
6. In **Review & improve**, edit text yourself or ask AI to apply feedback. Edits update the active application folder and keep prior versions in its hidden `.history` folder. Check facts and layout before sending anything.

**Find jobs starts empty.** Add an employer under **Manage employers**, save its Ashby or Greenhouse board URL, then click **Refresh vacancies**. This checks your selected employer boards; it is not a general job-search engine.

Read the [desktop guide](GUI_GUIDE.md) for all controls or [prepare one application](GENERATE_ONE_APPLICATION.md) for the terminal workflow.

In **Jobs**, status changes save immediately; text fields, dates and notes save after a brief pause. **Save progress** saves immediately. Invalid dates or failed saves leave the edits visible with an unsaved notice and prevent navigation or closing from discarding them. Correct the problem and retry; dates use `YYYY-MM-DD`. To load an update while an older window is open, click **Save progress** there before closing it and reopening **Open Job Radar.cmd**.

## AI and PDF setup

AI is optional for tracking, importing, ranking and manual editing. Codex uses an installed, authenticated command-line helper. Its local model catalogue supplies the model and reasoning choices; a listed choice does not guarantee current account access. Job Radar settings do not change another open Codex conversation.

The API providers require a valid model identifier and usually an API key supplied through an environment variable. Enter the **variable name**, never its secret value, in Job Radar. Codex sign-in and API access have different account and billing arrangements; do not assume a subscription includes API credits. Requests use the selected service's allowance or billing. See [integrations](docs/INTEGRATIONS.md) for configuration and interface references.

The default output is `cv.tex` and `motivation_letter.tex`. PDF compilation is optional and needs a separate local LaTeX installation with `latexmk`. The text editor is not a page-layout preview. Always inspect the compiled PDF before using it.

The supplied `.env.example` documents optional settings; Job Radar does not load `.env` files automatically. Set needed values in your process or user environment and restart the program. The default date timezone is `Europe/Zurich`; set `JOB_RADAR_TIMEZONE` to your IANA timezone if needed.

## Data and privacy

Your database, profile, references, settings and drafts are kept in the working folder. They are not encrypted by Job Radar. Network access occurs for requested website/feed fetches, explicit Ollama model-metadata checks and selected AI actions; opening the window does not start a background search or generate documents.

Generation sends the selected job, verified profile, included source excerpts and selected reference text to your chosen provider. Improvement also sends feedback and the saved draft context. Private document annotations are excluded. A local model server's privacy depends on that server's configuration.

Runtime folders are excluded from version control by the repository's ignore rules. Review staged files before every public commit: ignore rules do not remove files already tracked by Git. Do not publish your working database, reference library, generated documents, logs containing requests, or credentials. See [SECURITY.md](SECURITY.md) for boundaries and reporting.

## Why this approach

Job Radar grew from experiments with fuller AI automation of job discovery and application writing. Manual discovery often produced better candidates, API usage added cost, and generated documents still needed substantial review. The current workflow keeps useful automation while making review and ownership explicit.

The project was developed with AI-assisted coding and human decisions about scope, workflow and review. It is a practical personal tool, with an experimental search path retained for exploration. That path is not required for everyday use.

## Development and limitations

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests use synthetic data and mocked model responses. They check workflow and validation behaviour, not current employer coverage or model output quality. Some GUI checks require a display and some filesystem checks depend on operating-system permissions. See [release validation](docs/RELEASE_VALIDATION.md) for the tested environment and remaining installation checks.

This first public source version is intended for early users willing to review imported details and generated drafts. AI writing quality varies by provider and model; a structurally valid response can still be weak or use an unintended language. These are [known follow-up items](docs/KNOWN_ISSUES.md), not guarantees made by the validation checks.

See [architecture](docs/ARCHITECTURE.md), [contributing](CONTRIBUTING.md) and [change history](CHANGELOG.md). Broad job-board scraping, automatic applications and background agents are outside the current desktop workflow.

## Author

Created by **Daniel Stähli**.

- [GitHub: daniel-staehli](https://github.com/daniel-staehli)
- [LinkedIn: Daniel Stähli](https://www.linkedin.com/in/daniel-stahli)

## Licence and acknowledgements

Job Radar's original source is available under the [MIT licence](LICENSE). Retain its copyright and permission notice when redistributing it. Dependencies have separate licences; see [third-party notices](THIRD_PARTY_NOTICES.md). Official API interface references and an acknowledgement for the term-grouping idea appear in [integrations](docs/INTEGRATIONS.md).
