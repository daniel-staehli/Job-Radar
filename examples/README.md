# Fictional examples

Every person, organisation, publication and qualification in these files is invented for demonstration and testing. `example.org` links are placeholders, not job opportunities or real references.

- `master_profile.yaml` shows the verified fact-bank structure: contact details, experience bullets with unique IDs, skills, education, languages, publications, additional experience, unsupported claims and search preferences.
- `manual_job.yaml` shows the fields accepted by manual import. Replace the complete example vacancy before importing a real job.
- `selected_jobs.yaml` starts empty. Add saved job IDs or vacancy URLs only when you intend to process them.
- `search_strategies.yaml` supplies example settings for the optional experimental CLI search. Edit the queries and regions before a live search.

Use the setup instructions in the main README to initialise your private `data/` directory. Edit the private copies, leaving these public examples unchanged. Replace all invented facts, remove unneeded entries, and set `example_only: false` only after your profile contains your own verified information. Never treat an example qualification or publication as a claim you can reuse.

The optional `contact.additional_information` field appears in the CV only when supplied. It can contain verified work-authorisation or availability information you want included; the template makes no default citizenship or travel claim. `photo_path: null` produces a CV without a photo.

Experience `key` values and individual `id` values link selected content back to the fact bank. Keep them unique and stable. The `never_claim` list blocks specified text patterns; it is an additional check, not a substitute for reviewing every generated statement.
