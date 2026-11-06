"""Daily Markdown report."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from jobcopilot.models import Draft, Evaluation, Job, Run

SOURCE_NAMES = {
    "remotive": "Remotive", "remoteok": "Remote OK", "arbeitnow": "Arbeitnow", "hn": "Hacker News", "legacy": "Imported",
    "jobicy": "Jobicy", "himalayas": "Himalayas", "workingnomads": "Working Nomads", "wwr": "We Work Remotely",
    "watchlist": "Company job board", "linkedin": "LinkedIn", "indeed": "Indeed", "manual": "Added by me",
}


def _item(job: Job, evaluation: Evaluation, draft: Draft | None) -> list[str]:
    lines = [
        f"## {job.company} — {job.title} · {evaluation.score}/100",
        "",
        f"- **Location:** {job.location or 'not stated'}{' (remote)' if job.remote else ''}",
        f"- **Salary:** {job.salary_text or 'not stated'}",
        f"- **Link:** {job.url} (via {SOURCE_NAMES.get(job.source, job.source)})",
        f"- **Fit:** {evaluation.fit_reasons}",
        f"- **Gaps:** {evaluation.gaps}",
        "",
    ]
    if draft is None:
        lines += ["_Draft failed — write this one yourself or re-run later._", ""]
        return lines
    lines += ["**CV tweaks**", ""] + [f"- {t}" for t in draft.cv_tweaks] + [""]
    lines += ["**Cover note**", "", draft.cover_note, ""]
    lines += [f"**Outreach** (send to: {draft.contact_hint})", "", draft.outreach_message, ""]
    return lines


def render_report(
    day: date,
    run: Run,
    items: list[tuple[Job, Evaluation, Draft | None]],
    follow_ups: list[tuple[Job, datetime]],
    unscored: int = 0,
) -> str:
    lines = [f"# Job matches — {day.isoformat()}", "", "## Today's focus", ""]
    lines.append("1. Send the top 3–5 applications below.")
    lines.append("2. Message one person on each team the same day.")
    if follow_ups:
        lines.append("3. Follow up on:")
        lines += [f"   - {j.company} — {j.title} (due {d.date().isoformat()}) {j.url}" for j, d in follow_ups]
    else:
        lines.append("3. No follow-ups due.")
    summary = f"{run.fetched} fetched · {run.new} new · {run.kept} kept · {run.scored} scored · {run.drafted} drafted"
    lines += ["", f"_{summary}_"]
    if run.failed_sources:
        lines.append(f"_Failed sources: {', '.join(run.failed_sources)}_")
    if unscored:
        lines.append(f"_{unscored} jobs could not be scored; they will be retried next run._")
    lines.append("")
    for job, evaluation, draft in sorted(items, key=lambda it: it[1].score, reverse=True):
        lines += _item(job, evaluation, draft)
    if not items:
        lines += ["No new matches today.", ""]
    return "\n".join(lines)


def write_report(reports_dir: Path, day: date, content: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{day.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    return path


def add_to_report(reports_dir: Path, day: date, content: str, has_items: bool) -> Path:
    """Write today's report, without losing an earlier run's report from the same day.

    A later run with nothing new leaves the existing report untouched; one with new
    matches is appended below it."""
    path = reports_dir / f"{day.isoformat()}.md"
    if not path.exists():
        return write_report(reports_dir, day, content)
    if has_items:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n\n---\n\n" + content.replace("# Job matches", "# Later run — job matches", 1))
    return path
