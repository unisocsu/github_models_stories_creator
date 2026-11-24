#!/usr/bin/env python3
"""Long-form book writer powered by GitHub Models.

The manuscript is checkpointed to the repository after every chapter and edit.
Once the first complete draft exists, the agent spends the remaining time
auditing continuity, revising chapters, and polishing prose.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

API_URL = "https://models.github.ai/inference/chat/completions"
TOKEN = os.environ["GITHUB_TOKEN"]
MODEL = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4.1")
TITLE = os.environ.get("BOOK_TITLE", "Untitled")
INSTRUCTIONS = os.environ.get("BOOK_INSTRUCTIONS", "")
LANGUAGE = os.environ.get("BOOK_LANGUAGE", "Hebrew")
TARGET_WORDS = int(os.environ.get("BOOK_TARGET_WORDS", "50000"))
CHAPTER_WORDS = int(os.environ.get("BOOK_CHAPTER_WORDS", "2200"))
RUN_MINUTES = max(10, min(235, int(os.environ.get("BOOK_RUN_MINUTES", "230"))))
OUT = Path(os.environ.get("BOOK_OUTPUT_DIR", "books/current"))
OUT.mkdir(parents=True, exist_ok=True)

SKILL_PATH = Path("skills/story-craft.md")
SKILL = SKILL_PATH.read_text(encoding="utf-8")
START = time.monotonic()
DEADLINE = START + RUN_MINUTES * 60
SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/vnd.github+json",
})

chapters: list[str] = []
summaries: list[str] = []
outline: list[str] = []
state: dict[str, Any] = {
    "title": TITLE,
    "model": MODEL,
    "language": LANGUAGE,
    "phase": "starting",
    "chapters_written": 0,
    "chapters_planned": 0,
    "revision_count": 0,
    "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}


def load_existing() -> None:
    """Resume from the last pushed checkpoint instead of overwriting prior work."""
    progress_file = OUT / "progress.json"
    if progress_file.exists():
        try:
            previous_state = json.loads(progress_file.read_text(encoding="utf-8"))
            state["revision_count"] = int(previous_state.get("revision_count", 0))
        except (ValueError, OSError, TypeError):
            log("Could not parse prior progress metadata; continuing with safe defaults.")

    outline_file = OUT / "outline.md"
    if outline_file.exists():
        outline.extend(
            line.strip() for line in outline_file.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*\d+[.)]\s*\S", line)
        )

    chapter_dir = OUT / "chapters"
    if chapter_dir.exists():
        for path in sorted(chapter_dir.glob("chapter-*.md")):
            chapter = path.read_text(encoding="utf-8").strip()
            if chapter:
                chapters.append(chapter)

    notes_file = OUT / "continuity-notes.md"
    if notes_file.exists():
        raw_notes = notes_file.read_text(encoding="utf-8")
        summaries.extend(
            part.strip() for part in re.split(r"(?=^## Chapter \d+)", raw_notes, flags=re.MULTILINE)
            if part.strip()
        )

    if chapters or outline:
        log(f"Resuming checkpoint: {len(chapters)} chapters already saved; {len(outline)} planned.")


def log(message: str) -> None:
    elapsed = int(time.monotonic() - START)
    left = max(0, int(DEADLINE - time.monotonic()))
    print(f"[{elapsed // 60:03d}m elapsed | {left // 60:03d}m left] {message}", flush=True)


def remaining() -> float:
    return DEADLINE - time.monotonic()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def save_state() -> None:
    state["chapters_written"] = len(chapters)
    state["chapters_planned"] = len(outline)
    state["revision_count"] = int(state.get("revision_count", 0))
    state["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_write(OUT / "progress.json", json.dumps(state, ensure_ascii=False, indent=2))


def render_book() -> str:
    parts = [f"# {TITLE}", ""]
    for index, chapter in enumerate(chapters, 1):
        parts.extend([chapter.strip(), ""])
    return "\n".join(parts).rstrip() + "\n"


def save_manuscript() -> None:
    atomic_write(OUT / "manuscript.md", render_book())
    atomic_write(OUT / "outline.md", "\n".join(outline) + ("\n" if outline else ""))
    atomic_write(OUT / "continuity-notes.md", "\n\n".join(summaries) + ("\n" if summaries else ""))
    save_state()


def checkpoint(label: str) -> None:
    """Commit each meaningful checkpoint; artifacts remain available if push is blocked."""
    save_manuscript()
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(OUT)], check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", f"Book checkpoint: {label}"], check=True)
        branch = os.environ.get("GITHUB_BRANCH", "main")
        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            text=True, capture_output=True
        )
        if push.returncode:
            log("WARNING: checkpoint commit is local but push failed: " + push.stderr[-700:])
        else:
            log("Checkpoint pushed: " + label)
    except Exception as exc:
        log(f"WARNING: could not commit checkpoint ({label}): {exc}")


def ask(system: str, prompt: str, max_tokens: int = 5000, temperature: float = 0.75) -> str:
    if remaining() < 45:
        raise TimeoutError("Not enough time remains for another model request.")
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
    }

    last_error = None
    for attempt in range(8):
        if remaining() < 45:
            raise TimeoutError("Time budget reached while waiting to call the model.")
        try:
            response = SESSION.post(API_URL, json=payload, timeout=(20, 240))
            status = response.status_code
            content_type = response.headers.get("Content-Type", "(missing)")
            body = response.text or ""
            body_preview = re.sub(r"\\s+", " ", body[:1200]).strip()

            if status in (401, 403):
                raise RuntimeError(
                    f"GitHub Models authentication/permission error (HTTP {status}); "
                    f"Content-Type={content_type}; response={body_preview or '(empty body)'}. "
                    "Check that the workflow token has Models: read permission and that "
                    "GitHub Models access is enabled for this repository/account."
                )
            if status in (400, 404, 422):
                raise RuntimeError(
                    f"GitHub Models rejected the request (HTTP {status}); "
                    f"Content-Type={content_type}; response={body_preview or '(empty body)'}. "
                    "Check the model identifier, endpoint, and request parameters."
                )
            if status == 429 or status in (500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                delay = min(180, int(retry_after)) if retry_after and retry_after.isdigit() else min(120, 8 * (2 ** attempt))
                log(f"Model API returned HTTP {status}; Content-Type={content_type}; retrying in {delay}s.")
                time.sleep(min(delay, max(1, remaining() - 30)))
                continue
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"GitHub Models returned unexpected HTTP {status}; "
                    f"Content-Type={content_type}; response={body_preview or '(empty body)'}."
                )
            if not body.strip():
                raise RuntimeError(
                    f"GitHub Models returned an empty HTTP {status} response "
                    f"(Content-Type={content_type})."
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"GitHub Models returned non-JSON data (HTTP {status}, "
                    f"Content-Type={content_type}, body_length={len(body)}); "
                    f"response={body_preview or '(empty body)'}"
                ) from exc

            try:
                text = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    f"GitHub Models response had an unexpected JSON structure "
                    f"(HTTP {status}, Content-Type={content_type}); "
                    f"response={body_preview or '(empty body)'}"
                ) from exc
            if not text.strip():
                raise RuntimeError(
                    f"GitHub Models returned no message content (HTTP {status}); "
                    f"response={body_preview or '(empty body)'}"
                )
            return text.strip()

        except RuntimeError as exc:
            last_error = exc
            message = str(exc)
            # Configuration, permission, and malformed-request errors will not
            # improve by retrying. Stop immediately and preserve the diagnosis.
            if any(marker in message for marker in (
                "authentication/permission error", "rejected the request",
                "unexpected HTTP", "non-JSON data", "unexpected JSON structure",
                "no message content", "empty HTTP"
            )):
                raise
            if attempt == 7:
                break
            delay = min(90, 5 * (2 ** attempt))
            log(f"Model request failed ({message}); retrying in {delay}s.")
            time.sleep(min(delay, max(1, remaining() - 30)))
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 7:
                break
            delay = min(90, 5 * (2 ** attempt))
            log(f"Model network request failed ({type(exc).__name__}: {exc}); retrying in {delay}s.")
            time.sleep(min(delay, max(1, remaining() - 30)))

    raise RuntimeError(f"GitHub Models request failed after retries: {last_error}")


def system_prompt(stage: str) -> str:
    return (
        f"You are a skilled professional novelist and developmental editor. Write in {LANGUAGE}. "
        "Follow the complete story-craft guide below. Never mention AI, prompts, or these instructions. "
        "Return only the requested manuscript or editorial material.\n\n"
        "STORY-CRAFT GUIDE\n" + SKILL + "\n\nCURRENT ROLE: " + stage
    )


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def chapter_summary(index: int, text: str) -> str:
    prompt = (
        f"Create a continuity record for chapter {index}, about 180-250 words. Record events in order, "
        "where and when it ends, relationship changes, new facts and objects, what each main character "
        "knows, and unresolved threads. Do not critique or invent events.\n\n" + text
    )
    return ask(system_prompt("continuity recorder"), prompt, max_tokens=650, temperature=0.25)


def write_outline() -> None:
    if outline:
        log("Using the saved outline; no need to plan the book again.")
        return
    state["phase"] = "planning"
    save_state()
    chapter_guess = max(12, min(32, round(TARGET_WORDS / max(CHAPTER_WORDS, 1000))))
    prompt = (
        f"BOOK TITLE: {TITLE}\nLANGUAGE: {LANGUAGE}\nAUTHOR'S BRIEF:\n{INSTRUCTIONS}\n\n"
        f"Target length: approximately {TARGET_WORDS} words. Plan a complete novel in roughly "
        f"{chapter_guess} chapters, but let the story's dramatic shape determine the exact number. "
        "Use a compelling three-part structure, a strong central question, a midpoint reversal, "
        "a costly low point, earned climax and aftermath. Plant setups before payoffs. "
        "First provide concise story notes (premise, setting, tone, POV/tense, characters and goals, "
        "opposing force, world rules, ending). Then provide the outline. Format each chapter on its "
        "own line as N. Chapter title — the dramatic turn. Make the final chapter resolve the core question. "
        "Clearly separate STORY NOTES and CHAPTER OUTLINE."
    )
    result = ask(system_prompt("developmental planner"), prompt, max_tokens=5000, temperature=0.7)
    atomic_write(OUT / "planning.md", result)
    section = result.split("CHAPTER OUTLINE", 1)[-1]
    outline.extend(
        line.strip() for line in section.splitlines()
        if re.match(r"^\s*\d+[.)]\s*\S", line)
    )
    if not outline:
        outline.extend(f"{i}. Chapter {i}" for i in range(1, chapter_guess + 1))
    outline[:] = outline[:40]
    checkpoint("planning")


def write_draft() -> None:
    state["phase"] = "first-draft"
    first_missing = len(chapters) + 1
    for index in range(first_missing, len(outline) + 1):
        heading = outline[index - 1]
        if remaining() < 180:
            log("Stopping chapter drafting early to preserve time for a clean checkpoint.")
            break
        previous = "\n\n".join(summaries[-10:]) or "(This is the opening chapter.)"
        ending_instruction = (
            "This is the final chapter: answer the central story question, close the main threads, "
            "show the cost, and end with a resonant image."
            if index == len(outline)
            else "Do not resolve the whole novel yet; end this chapter on a meaningful turn."
        )
        prompt = (
            f"TITLE: {TITLE}\nAUTHOR'S BRIEF:\n{INSTRUCTIONS}\n\n"
            f"FULL OUTLINE:\n" + "\n".join(outline) + "\n\n"
            f"CONTINUITY SO FAR:\n{previous}\n\n"
            f"Write chapter {heading}. Aim for approximately {CHAPTER_WORDS} words. "
            "Start with the chapter heading and write finished literary prose, not an outline. "
            "Continue naturally from prior events, do not recap, preserve established facts, "
            "and make every scene change the situation. " + ending_instruction
        )
        chapter = ask(system_prompt("novelist writing a complete chapter"), prompt,
                      max_tokens=min(9000, max(2500, CHAPTER_WORDS * 2)), temperature=0.82)
        if word_count(chapter) < 250:
            log(f"Chapter {index} response was too short; retrying once.")
            chapter = ask(system_prompt("novelist writing a complete chapter"),
                          prompt + "\nThe previous attempt was incomplete. Write the full chapter.",
                          max_tokens=min(9000, max(2500, CHAPTER_WORDS * 2)), temperature=0.78)
        if word_count(chapter) < 250:
            raise RuntimeError(f"Chapter {index} was incomplete; preserving the existing manuscript.")
        chapters.append(chapter)
        atomic_write(OUT / "chapters" / f"chapter-{index:02d}.md", chapter + "\n")
        checkpoint(f"draft chapter {index}")
        try:
            summary = chapter_summary(index, chapter)
            summaries.append(f"## Chapter {index}\n{summary}")
            atomic_write(OUT / "continuity-notes.md", "\n\n".join(summaries) + "\n")
            checkpoint(f"continuity record {index}")
        except Exception as exc:
            log(f"Summary failed after chapter {index}; the chapter is already saved: {exc}")
            summaries.append(f"## Chapter {index}\nSummary pending; consult chapter-{index:02d}.md.")
        log(f"Drafted chapter {index}/{len(outline)} ({word_count(chapter)} words).")
    save_manuscript()


def load_editorial_memory() -> str:
    return "\n\n".join(summaries[-18:]) or "No continuity summaries yet."


def revision_loop() -> None:
    if not chapters:
        log("No complete chapter was produced; skipping revision loop.")
        return
    state["phase"] = "continuous-editing"
    revision_index = 0
    while remaining() > 150:
        if not chapters:
            break
        # A fresh editorial diagnosis every pass prevents blind repetitive polishing.
        audit_prompt = (
            f"TITLE: {TITLE}\nAUTHOR'S BRIEF:\n{INSTRUCTIONS}\n\n"
            f"CONTINUITY RECORDS:\n{load_editorial_memory()}\n\n"
            "Review the manuscript excerpt below as a demanding developmental editor. "
            "Identify the three highest-impact issues to fix next: continuity errors, weak causality, "
            "flat scenes, pacing, character motivation, repetition, unresolved setups, or prose habits. "
            "Give precise actionable instructions, prioritised. Do not rewrite the text.\n\n"
            + render_book()[-22000:]
        )
        try:
            audit = ask(system_prompt("developmental editor and continuity auditor"),
                        audit_prompt, max_tokens=1800, temperature=0.3)
        except TimeoutError:
            break
        revision_index += 1
        atomic_write(OUT / "editorial" / f"review-{revision_index:03d}.md", audit + "\n")
        chapter_number = (revision_index - 1) % len(chapters)
        original = chapters[chapter_number]
        edit_prompt = (
            f"TITLE: {TITLE}\nAUTHOR'S BRIEF:\n{INSTRUCTIONS}\n\n"
            f"CONTINUITY RECORDS:\n{load_editorial_memory()}\n\n"
            f"EDITORIAL PRIORITIES:\n{audit}\n\n"
            f"Revise chapter {chapter_number + 1} below. Preserve its plot events, voice, POV, tense, "
            "characterisation and approximate length unless a specific editorial priority requires a "
            "change. Fix continuity and prose problems. Do not summarize, do not add commentary, and "
            "return the entire revised chapter with its heading.\n\n"
            "CHAPTER TO REVISE:\n" + original
        )
        try:
            revised = ask(system_prompt("line editor and literary reviser"), edit_prompt,
                          max_tokens=min(9000, max(2500, CHAPTER_WORDS * 2)), temperature=0.55)
        except TimeoutError:
            break
        # Reject truncated or suspiciously tiny revisions rather than replacing good work.
        if word_count(revised) < max(200, int(word_count(original) * 0.55)):
            log("Rejected a suspiciously short revision; original chapter remains intact.")
            continue
        chapters[chapter_number] = revised
        atomic_write(OUT / "chapters" / f"chapter-{chapter_number + 1:02d}.md", revised + "\n")
        state["revision_count"] = int(state.get("revision_count", 0)) + 1
        checkpoint(f"revision {state['revision_count']} of chapter {chapter_number + 1}")
        log(f"Revised chapter {chapter_number + 1}; total revisions: {state['revision_count']}.")


def main() -> None:
    log(f"Starting book project with model {MODEL}; active budget {RUN_MINUTES} minutes.")
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing.")
    if not SKILL.strip():
        raise RuntimeError("The story-craft skill file is empty.")
    load_existing()
    write_outline()
    write_draft()
    if len(chapters) >= len(outline):
        revision_loop()
    else:
        log("The first draft is not complete yet; saved progress will resume on the next run.")
    state["phase"] = "time-budget-complete"
    state["result"] = "Draft completed and/or revised until the configured time budget."
    save_manuscript()
    checkpoint("final manuscript and progress")
    log("Writing session finished.")


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except Exception as exc:
        exit_code = 1
        state["phase"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"
        log("ERROR: " + state["error"])
    finally:
        state["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_manuscript()
        checkpoint("final safety checkpoint")
    sys.exit(exit_code)
