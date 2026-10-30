# Integrations and acknowledgements

Job Radar uses small original adapters where that keeps the workflow straightforward. The desktop styling, employer feeds, provider selection, supporting-source store, manual editor and term matcher are original project code built on its installed dependencies. No implementation was copied from ttkbootstrap, Pydantic AI, JobSpy, ApplyPack or JobSync, and those projects are not production dependencies.

Permissive components can be adopted later if they offer a practical benefit and their notices and dependencies are reviewed. The current release does not include a packaged Python runtime or dependency binaries. See [third-party notices](../THIRD_PARTY_NOTICES.md), the [dependency inventory](dependency-inventory.json) and the dependency licence texts in `third_party_licenses/` for the reviewed versions. Job Radar's MIT licence does not replace dependency licences.

## Employer feeds

**Find jobs** reads published vacancies for selected employer boards. Saving a preset is local; **Refresh vacancies** is an explicit network request. Results enter the application tracker only after the user chooses to save them.

| Interface | Documentation | Purpose |
| --- | --- | --- |
| Ashby | [Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) | Listed roles from a known employer board |
| Greenhouse | [Job Board API](https://docs.greenhouse.io/job-board.html) | Published jobs and descriptions from a known board |

These links credit the interface specifications used by the original adapters. Job descriptions remain third-party content. API access is not permission to redistribute a collected vacancy dataset; keep cached descriptions outside the public source repository. Source URLs remain attached to saved records.

Presets and discovery cache live under `data/`. Failed refreshes preserve old results with a stale indication. A vacancy leaving a feed does not change the user's application status. Broad job-board scraping and automatic applications are not part of this integration.

## AI adapters

| Desktop provider | Interface | Local configuration |
| --- | --- | --- |
| Codex | Installed command-line helper | Sign-in, model and reasoning choice |
| OpenAI | Responses API with structured output | Model ID and key-variable name |
| OpenAI-compatible / local Ollama | Chat completions with JSON-schema output | Model ID, API base URL, key variable if required |
| Ollama (local) | Native `/api/show` and `/api/chat` with JSON-schema output | Exact model ID, loopback `/api` URL, optional key variable, model-specific thinking choice |
| Anthropic | Messages API with structured output | Model ID and key-variable name |
| Google Gemini | Generate Content API with structured output | Model ID without the `models/` prefix and key-variable name |

The adapter name identifies an interface, not a promise that every model or compatible server supports it. Model access, limits and billing belong to the chosen provider/account. Codex sign-in and direct API billing are separate; a subscription does not establish API credit availability. A failed request does not automatically switch provider or retry at another endpoint.

Primary interface references:

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) and [structured outputs](https://platform.openai.com/docs/guides/structured-outputs).
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages) and [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
- [Gemini Generate Content API](https://ai.google.dev/api/generate-content) and [structured outputs](https://ai.google.dev/gemini-api/docs/structured-output).
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility), [native chat](https://docs.ollama.com/api/chat), and [model thinking controls](https://docs.ollama.com/capabilities/thinking).
- [Codex configuration reference](https://developers.openai.com/codex/config-reference).

No provider implementation or documentation text was copied into the application as third-party source. These links identify the APIs the code was written against.

### Settings and model choices

`data/ai_settings.json` stores the provider, model, reasoning level, Ollama thinking choice, custom-model choice, endpoint and **name** of the API-key environment variable. The secret key is read from the environment. On Windows, add the appropriate user variable through **Edit environment variables for your account**, then reopen Job Radar. Enter only its name in the application's key field. Environment variables are not an encrypted secret vault.

Codex selectors read the local client's `models_cache.json` and only model-related settings from `config.toml`. They do not read credentials to populate the model list, request a completion or prove account access. The chosen model and reasoning are passed explicitly to the helper. Custom IDs remain unverified and typos can fail without fallback. These settings do not change a separate Codex chat.

API model IDs are entered explicitly. Remote compatible endpoints require HTTPS; HTTP is permitted only for local loopback services. Custom service endpoints are accepted by the compatible adapter. The native Ollama adapter accepts loopback endpoints ending in /api. The local server must implement the expected response format.

**Check settings locally** validates the form. **Check setup** additionally checks the Codex helper/sign-in when selected, or API key-variable presence for API adapters. Neither is a live model-access test. The terminal's inherited `--backend api` option remains its OpenAI workflow and does not read desktop provider choices.

The legacy terminal Codex backend uses `JOB_RADAR_CODEX_MODEL` or the helper's built-in model default, with `medium` reasoning unless `JOB_RADAR_CODEX_REASONING` is set. It ignores the helper's user configuration during generation. The desktop instead resolves the chosen model and reasoning locally and passes them explicitly. See the [terminal guide](../GENERATE_ONE_APPLICATION.md) for those separate settings. `.env.example` is documentation; the program does not automatically read a `.env` file.

**Load thinking options** is a separate local-server request containing only the model name. It reads supported values and the model default from Ollama metadata, with a short timeout. An explicit thinking choice is rechecked before generation. No metadata means default-only; model-name guesses are not used. Native chat sends the selected `think` value, or omits it for Model default. Reasoning traces are not saved as document output.

### Information sent

Generation sends the chosen job, verified profile, selected reference text, included supporting-source excerpts/notes and writing instructions to the provider displayed in the interface. Improvement uses feedback and the draft's saved context. Private document annotations are excluded. Original reference PDF files are not sent by this workflow.

Per-job supporting sources are loaded by desktop generation. The legacy terminal generation command currently passes the job, profile and reference library selections but does not load per-job sources; use the desktop when they should inform a new draft.

Opening the window, saving settings, browsing records, reviewing prepared inputs and manual editing do not generate model requests. A remote endpoint receives the request contents; processing on a local compatible server depends on its configuration. Audit files contain private request material and belong outside public version control.

## Supporting sources and manual editing

Per-job sources provide company and research context. They are distinct from candidate facts. Saving a URL does not fetch it; the user supplies relevant text or loads a local document. Generated versions retain the actual source snapshot used, including any truncation.

The manual editor writes a new structured version and renders the selected document. It retains previous versions and the companion document. Contact fields, fixed labels and page layout are outside the text editor. No model is called for manual saves. Private annotations are not automatically included in future model requests.

## Term-grouping acknowledgement

Reviewing [ApplyPack's score grouping](https://github.com/applypack/applypack/blob/ec38fcb6c9b0cd9fb86e4ebbb263d7b4746e5517/src/resume/score.ts) informed the idea that alternative requirements should count once. No implementation or numeric weights were copied. Job Radar's matcher counts literal mentions of user-defined groups, such as `R | Python`, and shows matched and missing groups. It does not infer qualifications or hiring outcomes.

## Verification limits

Automated checks use synthetic employer feeds and mocked responses. They cover request construction, validation, failure handling and workflow behaviour without paid model calls. They do not establish live account access, current vacancy availability, generated writing quality or PDF layout. Live provider requests and full PDF rendering have not been verified for this public source release.
