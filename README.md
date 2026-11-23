# GitHub Models Stories Creator

A long-running book-writing agent for GitHub Actions. It reuses the existing
story-craft skill from the Kaggle Stories Generator and calls GitHub Models
directly through its OpenAI-compatible inference endpoint.

## Run it

1. Open **Actions** → **Write and polish a full-length book**.
2. Choose **Run workflow**.
3. Enter the title, detailed brief, language and model.
4. Start with the defaults: 50,000 target words, about 2,200 words per chapter,
   and 230 minutes of active writing/editing time.

The workflow uses the built-in `GITHUB_TOKEN`; no PAT or API key secret is
required. It requests:

- `models: read` for GitHub Models inference.
- `contents: write` to checkpoint the manuscript into this repository.

## What the agent does

1. Plans the novel and creates a chapter outline.
2. Writes chapters one at a time, recording continuity notes.
3. Saves and pushes each completed chapter.
4. After the first draft, repeatedly audits continuity and revises chapters
   until the configured time budget is nearly exhausted.
5. Uploads a `book-and-progress` artifact even if the run fails.

The manuscript is stored under `books/current/`, with the combined
`manuscript.md`, individual chapters, continuity notes, editorial reviews,
and `progress.json`.

## Important limits

The job is configured for 240 minutes, with a 230-minute default work budget
so there is time for a final checkpoint. GitHub-hosted jobs have a hard
six-hour execution ceiling. GitHub Models rate limits, model availability,
context limits and service availability still apply; therefore a four-hour
run is a target, not a guarantee of uninterrupted model output.

If repository rules prevent GitHub Actions from pushing commits, the uploaded
workflow artifact still contains the latest files generated during the run.
