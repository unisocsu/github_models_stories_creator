# GitHub Models Stories Creator

A long-running book-writing agent for GitHub Actions, powered by the Google Gemini Interactions API.

## Run it

1. Add two repository Actions secrets named `GEMINI_KEY1` and `GEMINI_KEY2`.
2. Open **Actions** → **Write and polish a full-length book**.
3. Choose **Run workflow** and enter the title, detailed brief, language and Gemini model.
4. The default model is `gemini-3.8-flash`. Start with 50,000 target words, about 2,200 words per chapter, and 230 minutes of active writing/editing time.

The workflow reads both API keys from GitHub Secrets. Keys are sent only in the HTTPS request header and are never written to the manuscript or logs. If one key is rejected, the client tries the other. For temporary HTTP 429/5xx failures or an unavailable model, it retries and then automatically fails over from the selected model to Gemini Flash-Lite backups (`gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite`). The active model is recorded in `books/current/progress.json`.

**Quota note:** Gemini rate limits are generally applied at the Google Cloud project/model level, not separately to each API key. Two keys belonging to the same project improve credential failover, but do not double that project's quota. The exact RPM, TPM and RPD limits shown in Google AI Studio depend on the project, model and tier.

## What the agent does

1. Plans the novel and creates a chapter outline.
2. Writes chapters one at a time, recording continuity notes.
3. Saves and pushes each completed chapter.
4. After the first draft, repeatedly audits continuity and revises chapters until the configured time budget is nearly exhausted.
5. Pushes a checkpoint after every chapter and meaningful revision. If a run is cancelled or reaches its time limit, start the workflow again: it resumes from the saved outline and chapter files instead of starting over.
6. Uploads a `book-and-progress` artifact even if the run fails.

The manuscript is stored under `books/current/`, with the combined `manuscript.md`, individual chapters, continuity notes, editorial reviews, and `progress.json`.

## Important limits

The job is configured for 240 minutes, with a 230-minute default work budget so there is time for a final checkpoint. Gemini model availability, rate limits, context limits and service availability still apply. Check the project's current limits in Google AI Studio.

If repository rules prevent GitHub Actions from pushing commits, the uploaded workflow artifact still contains the latest files generated during the run.
