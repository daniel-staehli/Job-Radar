# Known limitations and follow-up work

The first public source version focuses on a usable local workflow: reviewed imports, application tracking, verified profile facts, optional AI drafts and manual revision. It does not submit applications.

## AI writing quality and language

A response that passes the document schema is not necessarily a good application. Model output can be generic, omit useful context, or choose a different language from the vacancy. Smaller local models may struggle with long profiles and structured responses. More context capacity does not by itself improve writing.

Review the wording, language, evidence and compiled layout before using a draft. You can edit manually or request a focused revision. Reference documents guide wording and structure; the supplied LaTeX templates control appearance.

The next investigation will compare the prepared prompt, selected references, profile, language instructions and actual model settings across providers. Planned follow-up includes a clear output-language choice and checks that the generated language matches it. These changes are not implemented in this release.

## Import coverage

Some vacancy websites require a browser, block requests, omit structured metadata or have ambiguous headings. Local extraction and optional AI extraction may leave fields blank when there is no reliable evidence. Review missing values manually; pasted text and text-based files provide alternatives. Scanned PDFs need external OCR.

Find jobs reads configured Ashby and Greenhouse employer boards. It is not a search of all job websites and does not populate itself on first launch.

## Installation and document output

- This is a Python source release, not a packaged desktop installer.
- Windows is the primary development platform. A real download/install/use test on macOS remains pending. Linux also remains unverified end to end.
- PDF output needs a separate LaTeX installation with `latexmk`. The text editor is not a PDF layout preview.
- Large local model contexts can require substantial memory and increase waiting time. Native Ollama starts with a 32,768-token context; choose a value supported by your model and machine.
- Abort stops Job Radar's active work and its local request/process. A remote service may already have processed or billed some work; cancellation is not a refund.

See [release validation](RELEASE_VALIDATION.md) for completed checks. Keep bug reports synthetic: do not attach personal CVs, job history, provider requests or credentials.
