"""The candidate's own writing, used as examples so drafts sound like them."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class Voice(BaseModel):
    samples: list[str] = Field(default_factory=list)  # data/voice/samples/*.md, verbatim
    notes: str = ""  # data/voice/notes.md: style notes the candidate writes
    recent: list[tuple[str, str]] = Field(default_factory=list)  # (question, final answer) they sent recently


def load_voice(voice_dir: Path, recent: list[tuple[str, str]]) -> Voice:
    samples_dir = voice_dir / "samples"
    samples = [p.read_text(encoding="utf-8").strip() for p in sorted(samples_dir.glob("*.md"))] if samples_dir.is_dir() else []
    notes_path = voice_dir / "notes.md"
    notes = notes_path.read_text(encoding="utf-8").strip() if notes_path.is_file() else ""
    return Voice(samples=[s for s in samples if s], notes=notes, recent=recent)
