# Security and privacy

Job Radar handles information that belongs in a private workspace: candidate profiles, contact details, applications, reference documents and AI request contents. This repository contains source and fictional examples; running it creates separate local data.

## Boundaries

- The application stores files and SQLite records locally without encryption. Access depends on your operating system, backups and any cloud-sync software.
- Runtime folders and credentials are excluded by ignore rules. Git can still track an ignored file if it was added earlier or forcibly added. Review the staged contents before publishing.
- Website/feed fetches contact the selected sources. Explicit AI generation, improvement, vacancy extraction, profile suggestions and job-match reviews send their relevant inputs to the provider shown in the interface. Provider retention and account settings are outside this application's control.
- API settings store an environment-variable name, not its secret. Environment variables are not a secret vault. Do not put credentials in URLs, model fields, screenshots, issue reports or source files.
- Supporting URLs are not automatically fetched. Selected excerpts and source notes may be sent during generation; private document annotations are excluded.
- PDF compilation uses a separate local LaTeX installation. Only compile source you trust and review files obtained from elsewhere.

Validation, request limits and source separation reduce some failure modes; they are not a guarantee that generated claims are true or that arbitrary external content is safe. Inspect drafts, sources and compiled output before using them.

## Before publishing a copy

Use a source-only checkout. Keep the database, `data/`, `applications/`, `reports/`, `.env` files, reference originals and generated documents out of commits. The public-release checker is an additional check, not a substitute for reviewing what Git will publish:

```powershell
.\.venv\Scripts\python.exe scripts\check_public_release.py --tracked
```

If a secret has already been published, revoke or rotate it with its provider. Removing it from the current file does not remove it from Git history or earlier downloads.

## Report a vulnerability

If the GitHub repository has private vulnerability reporting enabled, use **Security → Report a vulnerability**. Include a minimal synthetic reproduction, affected version and expected versus observed behaviour. Do not attach real CVs, application records or credentials.

If private reporting is unavailable, open a minimal issue asking the maintainer to arrange a private channel. Do not post exploit details or sensitive data publicly. There is no guaranteed response time; this is a small project rather than a managed service.

## Verification scope

Automated tests use temporary synthetic data and mocked providers. Live model calls and end-to-end PDF rendering have not been verified for the public source release. Dependency versions and their notices are recorded in the lockfile and [third-party notices](THIRD_PARTY_NOTICES.md); updates should include a fresh dependency and privacy review.
