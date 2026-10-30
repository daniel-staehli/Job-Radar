# Job Radar desktop guide

Complete [setup](README.md#setup-on-windows), then double-click **Open Job Radar.cmd**. The seven tabs share the same local database and application folders as the terminal commands. No model connection is needed to browse records or track progress.

## My profile and job-match reviews

**My profile** supplies verified facts for new applications and job-match reviews. Edit contact details, summary, experience and achievements, education, skills, publications, languages, interests and claims to avoid. **Use in profile form** accepts an entry into the form; **Save profile** saves the complete profile locally. Previous profile files are kept in `data/profile_history`.

Replace all fictional example facts before using the tool for an application. Once reviewed, open `data/master_profile.yaml` and change `example_only: true` to `example_only: false`. Saving the profile form does not clear that flag. Live document generation and experimental AI search remain blocked while it is true; local previews remain available.

To suggest facts from a CV, add it to **Reference library**, select it in **My profile**, then click **Review CV with AI**. This sends the saved profile and selected CV text to the chosen provider. Review the proposed additions and source evidence, select the ones you approve, then **Add selected to profile**. Suggestions are never accepted automatically.

For a saved job, open its match-review window and choose **Run AI review**. The review explains strengths, gaps and uncertainties against your saved profile; it is guidance, not a hiring prediction. It uses the selected provider's allowance. Opening a saved review is local. Changes to the profile or vacancy are flagged so you can decide whether another review is useful.

## Add a job

1. Paste a vacancy URL in **Add a job** and click **Fetch details**.
2. Review the company, title, location and description. Complete missing information from the vacancy page.
3. Click **Save reviewed job**. A new job appears in **Jobs** with status `interested`.

Fetching visits the website and creates a YAML draft in `data/imported_jobs`; it does not use AI. Some websites block extraction or require a browser, so manual entry is supported. Saving the reviewed form writes the database record. It does not rewrite the original extraction YAML. A job entered entirely by hand does not produce a YAML file.

Direct BambooHR vacancy links (for example, `https://EMPLOYER.bamboohr.com/careers/123`) also fetch the public vacancy details when the initial page requires JavaScript. This needs no AI or API key. Restart Job Radar after installing this update, then use **Fetch details** again for an earlier incomplete draft.

**Open YAML file** loads an existing draft for review. Re-importing a known job preserves its application history; conflicting job identities require review. Job Radar does not watch a file of links automatically.

**Paste job text…** lets you paste a description or load a text-based PDF, DOCX, TXT, MD or TEX file. Review the extracted fields before saving. A vacancy URL is optional for these jobs. No OCR is performed on scanned documents. Local extraction uses structured vacancy data and recognised headings/labels, without AI; missing or ambiguous values may remain blank.

**Extract missing details with AI** is a separate optional action. It sends the vacancy text and filled form fields to the selected provider, without your profile or reference library. Review suggested values and quoted evidence in **View extraction evidence** before saving. Unknown fields remain blank. Importing and saving jobs can run while documents are generating; neither action starts document generation.

## Find jobs at selected employers

An empty list is normal on first use. No employers are configured and nothing is fetched automatically.

1. Open **Find jobs → Manage employers**.
2. Enter the employer name and an actual Ashby or Greenhouse board URL, such as `https://jobs.ashbyhq.com/BOARD_NAME` or `https://job-boards.greenhouse.io/BOARD_NAME`. Replace the placeholder with the employer's real board name. A general company homepage will not work.
3. Click **Save employer**, then **Done**.
4. Choose an employer or **All saved employers** and click **Refresh vacancies**.
5. Select and read a result. Use **Save selected job to tracker**, or **Review in manual form** to correct details before saving.

Saving an employer is local. Refreshing reads the employer's public feed without sending your profile or reference documents. Supported board URLs already present in saved jobs may be offered as suggestions in **Manage employers**.

The employer picker and **Filter results** narrow cached results. The last fetched list remains available after reopening. A failed refresh keeps prior results with a stale indication; check the source page before applying. Removing a preset or a vacancy disappearing from the feed does not remove tracked applications.

For local ranking, enter **Priority terms** and click **Rank by terms (local)**. Separate groups with semicolons and alternatives with `|`, for example `microscopy; R | Python; Lausanne`. Either or both alternatives count once for that group. The **Terms** column and result details explain mentions and missing groups. This counts literal text mentions; it does not assess qualifications or predict selection.

## Track progress

In **Jobs**, select a vacancy. Changing **Status** saves immediately and updates the list and summary. Text fields, dates, document versions and notes save automatically after a brief pause in typing. **Save progress** saves immediately. Pending edits are also saved before switching jobs, refreshing or filtering the list, or closing the window.

If a date is invalid or a save fails, the form keeps your edits and shows an unsaved notice. Switching jobs, refreshing, filtering or closing is blocked until those edits can be saved. Correct the date or resolve the reported problem, then click **Save progress** to retry. Use `YYYY-MM-DD` for dates.

If Job Radar was already open when this fix was installed, use **Save progress** in that old window before closing it, then reopen **Open Job Radar.cmd** to load the update.

Statuses are `interested`, `preparing`, `applied`, `interview`, `rejected`, `withdrawn` and `offer`. Dates use `YYYY-MM-DD`. Enter multiple interview dates separated by commas. Changing to `applied` for the first time records a submission date if none exists; check it for historical applications. Record the names or versions of the CV and letter you actually sent. These fields do not upload or copy those files.

Use **Dashboard** for follow-ups and activity, **History** for recorded changes and **View vacancy details** for the saved description. Contact and outcomes are entered manually. The program does not read email, contact employers or submit applications.

## Reference CVs and cover letters

In **Reference library**, choose the document type and click **Add reference files**. Supported files are PDF, DOCX, TXT, MD and TEX. The library copies the file and extracts text locally; your original stays unchanged. Removing the library copy also leaves the original alone.

Files are limited to 10 MiB; PDFs to 20 pages. Use readable text exports rather than scans because no OCR is performed. Re-adding identical content with the same type reuses its entry. To correct a type, select the entry and use **Set selected reference type**.

CV references guide CV wording and cover-letter references guide letter wording. They inform tone, structure and emphasis. They do not establish candidate facts or reproduce a PDF's design. The verified profile supplies facts and the LaTeX templates control appearance.

## Supporting sources for a job

Select a vacancy in **Jobs → Supporting sources**, or use **Supporting sources for this job** in **Create documents**. Add a title and URL, paste relevant text, and write notes about its relevance. **Load local file…** extracts editable text from a supported document. Click **Save reference**.

Choose **Include this reference when generating new documents** for each source you want used. These sources belong to that job, separately from your global reference-document library. **A URL alone is not fetched** and is marked unread until text is supplied. Included excerpts and source notes appear in the local information review and are sent during generation. They provide company or research context, not evidence of your qualifications or authorship.

Generated versions retain a snapshot of the sources used. Editing the source list changes future initial generations, not earlier saved versions. Long text can be shortened for generation, with that recorded in the snapshot.

## AI settings

Open **AI settings** in the header. Saving or checking the form locally does not make a model request.

For **Codex**, choose an available local model and its supported **Reasoning level**. **Configured default** resolves the installed client's default; the explanation shows what Job Radar will request. These controls apply to Job Radar, not another open Codex chat. Higher reasoning may take longer and consume more allowance; it is not a percentage of capacity.

The local catalogue prevents ordinary typing mistakes but does not prove account access. **Custom model (not verified)** accepts an exact identifier, including any typo; a rejected identifier fails without silently switching models. If the list is unavailable, initialize and sign in to the installed Codex client, then reopen settings.

For API providers, enter the exact model ID available to your account or local server. The OpenAI-compatible option accepts custom endpoints; Ollama (local) accepts a local /api endpoint. It must support chat completions and the requested JSON-schema response format. Hosted providers use their official endpoints.

The key field takes an **environment-variable name**, such as `OPENAI_API_KEY`, not the secret. Set that variable outside Job Radar and reopen the program. Do not put keys in model names or URLs. API access and charges are separate from any Codex subscription allowance. See [integrations](docs/INTEGRATIONS.md) for more detail.

**Check setup** checks the Codex helper/sign-in or the selected API configuration and key presence. It does not test remote model availability. Check the displayed provider and endpoint before generation or improvement: that is the destination of the request.

## Ollama thinking controls

Open **AI settings** and select **Ollama (local)**. Enter the exact model name from **ollama list**, keep **API base URL** as **http://localhost:11434/api** for a standard installation, and leave the key-variable field empty unless your local server requires authentication. **localhost** means this computer; **11434** is Ollama's default port. This provider accepts only local loopback addresses and a base path of **/api**.

Click **Load thinking options**. This contacts the local server with the model name only; no job, profile or reference text is sent. The read-only **Ollama thinking** selector shows the choices reported by that model, such as On/Off or named levels. These are not Codex reasoning levels. **Model default** omits the override. If the server does not report thinking controls, only Model default is available; updating Ollama may make metadata available, but support still depends on the model.

Click **Save settings** to use your choice for generation and improvement. An explicit choice is checked against current model metadata again before document text is sent, preventing an unsupported level from silently falling back. Changing the model or connection resets the selector to Model default; a delayed lookup for the previous model is ignored. A saved choice is preserved when reopening settings and marked unchecked until loaded again.

**Ollama context window** defaults to **32,768 tokens** shared by input, thinking and output. The output allowance is 16,384 tokens within that context. Bigger contexts need more memory and do not inherently improve writing; choose a size supported by your model and machine. Native Ollama generation permits up to ten minutes; other API adapters retain a three-minute timeout.

The older **OpenAI-compatible API / local Ollama** option still works with **http://localhost:11434/v1**, but does not send native thinking/context controls. Existing settings are not migrated automatically. Choose a downloaded local model if you want processing to stay on this computer: a local Ollama server can also route cloud models remotely.

**Check settings locally** and **Check setup** do not probe Ollama. Only **Load thinking options** queries model metadata. It does not run a generation or establish that a full application request will succeed.

## Create documents

First replace the fictional facts in `data/master_profile.yaml` with your own verified information and set `example_only: false` after review. Live generation is blocked while that flag is true; local previews remain available. Adding reference documents does not update this fact bank.

1. Open **Create documents** and choose a saved job.
2. In **Available in your library**, highlight a CV or cover letter and click **Add →** (or double-click). It moves to **Selected for this application** on the right. Only that right-hand list is used. You can add up to three documents in total; select an entry there and click **← Remove** to change your choice. References are optional.
3. Use **Review information (local)** to inspect the job text, profile, references, included supporting sources and writing instructions. This prepares source material; it does not create a finished document.
4. Check the AI provider and use **Check setup** as needed.
5. Click **Generate CV + letter** for one writing request producing both first drafts.
6. Read them in **Review & improve** and open the output folder to inspect the files.

References guide wording, tone and paragraph structure; their PDF design is not copied. New cover letters use a fixed layout with contact details and date above the subject, followed by the salutation, body and signature. Existing drafts retain their saved templates, including when improved. Generate a new pair to use the updated layout.

Generation starts a fresh request without your chat history. The verified profile supplies the facts, and CV experience bullets are selected from that profile rather than rewritten. Review the local input and update the profile when relevant experience is missing. During generation the status bar shows elapsed time, not a completion estimate. Both documents are already requested together; use **Improve selected document** for a focused revision instead of generating the pair again. Model, reasoning level and input length can affect waiting time. No reasoning setting is changed by this update.

Review again if you change the job, profile or selected references. Each desktop generation uses a fresh folder under `applications`. Included reference text may be shortened to the configured limits; original PDF files are not uploaded by the generation workflow.

New desktop applications are saved directly in a folder named `company_YYYY-MM-DD_id`, for example `applications/example-laboratory_2026-10-17_a1b2c3/cv.tex`. The company name is limited to 24 characters and shortened further when needed; the date and six-character ID stay intact. Output settings allow a different root and grouping by model. Each initial generation gets a separate folder. Later desktop edits and improvements update that application's active files, retaining previous states in its hidden `.history` folder.

**Abort generation** becomes available during generation. It stops Job Radar's active request/process and subsequent steps. Incomplete attempts are recorded separately from completed drafts; previous applications remain intact. A provider may already have processed or charged for some work. Elapsed time is not a completion estimate.

Existing folders and version history keep their original paths. If the output location itself is too long, the program asks for a shorter location before an AI request. Older deeply nested files may still need a shorter path to open in an external Windows editor.

Default outputs are `cv.tex` and `motivation_letter.tex`. **Also compile PDFs** requires a working local LaTeX installation with `latexmk`. Otherwise use a LaTeX editor yourself. An online editor is a separate transfer to that service. A readable text view does not verify PDF layout.

If validation fails, inspect `VALIDATION_FAILED.txt`. Automated checks cannot establish that every claim is true. Generation does not submit an application or mark it `applied`; record that only after you submit it yourself.

## Edit and annotate without AI

In **Review & improve**, choose an active application and select **CV** or **Cover letter**, then click **Edit text / notes**. Choose a section, edit the wording and click **Save changes**. This is local; active files stay in the same folder, earlier states are kept in `.history`, and the companion document is preserved. Layout controls can change the cover-letter recipient/header block without rewriting its body.

The editor covers document wording and selected CV facts. Contact details, fixed labels and page design remain in the profile/templates. Edits apply to the saved version, not the master profile. Check factual changes yourself. Invalid references and known forbidden claims remain blocked; deviations from writing preferences can be saved with review notes.

**My notes / annotations** are private to the saved version. They are not printed or automatically sent to AI. Put an instruction in the feedback box if you want AI to act on it. Supporting-source notes, by contrast, can be included in generation.

Later AI improvement starts from saved manual edits. Directly modifying a `.tex` file outside Job Radar is a separate workflow: the structured editor cannot reconstruct those changes. External changes are detected and preserved; do not assume the readable saved-text view includes them.

## Improve with feedback

Select a saved document, enter specific feedback and click **Improve selected document**. The request uses the current AI provider and that draft's saved context, including its original facts and references. Successful changes update the active application folder, preserve the prior state in `.history`, and retain the companion document. Use **Compare previous version** to inspect changes.

For an updated PDF, select **Also compile the revised PDF** and check the result. Without successful compilation, the revised `.tex` is the new document. Editing the master profile or source selection does not retroactively replace a draft's context; create a new initial draft to use those changes.

## Local files and network actions

| Item or action | Behaviour |
| --- | --- |
| `job_radar.sqlite3` | Saved vacancies and application history |
| `data/` | Profile, imported jobs, libraries, sources, cached results and settings |
| `applications/` | Private requests, context snapshots, drafts, revisions and audit records |
| `reports/` | Generated local reports |
| Open window, browse, track, rank, edit or compare | Local operations |
| Fetch details / Refresh vacancies | Explicit website or employer-feed request |
| Review information / Check setup | Local preparation or setup checks; no model generation |
| Generate / Improve / AI extraction / CV suggestions / Match review | Sends relevant content to the chosen provider |

Job Radar does not encrypt local files. Cloud sync or backup software on your computer may also copy them. Keep private backups and do not commit runtime folders or credentials to a public repository.

## Troubleshooting

- **Empty Find jobs:** save a supported employer board and click **Refresh vacancies**. A board may also have no published roles.
- **Missing vacancy details:** copy them from the original page and complete the form.
- **Unreadable reference:** use a text-based PDF, DOCX or text export; encrypted PDFs and scans may be rejected.
- **Model request fails:** check exact model ID, sign-in or key variable, endpoint and account access. Reopen after changing environment variables. Do not repeatedly retry a billed request without reading the error.
- **External edit detected:** retain that file. Its edits are not included in the saved structured text; do not overwrite them on the assumption that they are.
- **No PDF:** source output is the default. Check local `latexmk` setup and compilation logs.

Automated tests use synthetic records and mocked providers. They validate workflows, not writing quality or language. Review [known limitations](docs/KNOWN_ISSUES.md) and [release validation](docs/RELEASE_VALIDATION.md) for the tested scope and pending checks.
