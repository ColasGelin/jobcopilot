"""Local dashboard: Dashboard, Today, Pipeline, Companies, CV and Runs views over jobs.db."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates

from jobcopilot.apply.cover import cover_filename, is_cover_field, render_cover
from jobcopilot.apply.models import FormSnapshot
from jobcopilot.config import CompanyToCheck
from jobcopilot.cv.bank import BankError, load_bank
from jobcopilot.cv.content import CvContent, main_content
from jobcopilot.cv.render import FONTS_DIR, render_html, render_pdf
from jobcopilot.cv.tailor import TAILOR_PROMPT_VERSION, TailorError, cv_filename, download_name, tailor_cv
from jobcopilot.db import DB
from jobcopilot.llm import LLMClient, LLMError
from jobcopilot.models import STATUSES, Run
from jobcopilot.report import SOURCE_NAMES
from jobcopilot.text import normalize
from jobcopilot.web.insights import TODAY_FILTERS, dashboard, next_step, remote_unconfirmed, run_warning, steps

HERE = Path(__file__).parent


def duration(run: Run) -> str:
    if not run.finished_at:
        return "—"
    seconds = int((run.finished_at - run.started_at).total_seconds())
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def safe_url(url: str) -> str | None:
    """Only http(s) links are rendered; feed URLs like `javascript:` are shown as text."""
    return url if url.lower().startswith(("http://", "https://")) else None


def _shift(day: date, days: int) -> date | None:
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return None


OFFERS_SHOWN = 30


def now_local() -> datetime:
    return datetime.now().astimezone()


def create_app(
    db_path: Path,
    clock: Callable[[], datetime] = now_local,
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1"),
    min_score: int = 60,
    companies: list[CompanyToCheck] | None = None,
    cv_path: Path | None = None,
    cvs_dir: Path | None = None,
    llm_factory: Callable[[], LLMClient] | None = None,
    draft_model: str = "sonnet",
    preparer_factory: Callable[[], object] | None = None,  # builds a Preparer per request (own DB connection)
) -> FastAPI:
    app = FastAPI(title="jobcopilot", docs_url=None, redoc_url=None, openapi_url=None)
    # No auth by design, so: refuse foreign Host headers (DNS rebinding) ...
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")  # autoescape is on for .html
    templates.env.globals["source_name"] = lambda s: SOURCE_NAMES.get(s, s)
    templates.env.globals["duration"] = duration
    templates.env.globals["safe_url"] = safe_url
    templates.env.globals["remote_unconfirmed"] = remote_unconfirmed

    def db() -> DB:
        return DB(db_path)  # one connection per request; the daemon writes to the same file

    app.state.tailor_lock = threading.Lock()  # one tailoring at a time (double clicks, two tabs)
    app.state.prepare_lock = threading.Lock()
    bank_cache: dict[str, object] = {}

    def bank_or_error():
        """(bank, None) or (None, message); re-read only when cv.yaml changes."""
        if cv_path is None:
            return None, "No cv.yaml configured."
        try:
            stamp = cv_path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if bank_cache.get("stamp") != stamp or "result" not in bank_cache:
            try:
                result = (load_bank(cv_path), None)
            except BankError as e:
                result = (None, str(e))
            bank_cache.update(stamp=stamp, result=result)
        return bank_cache["result"]

    templates.env.globals["bank_error"] = lambda: bank_or_error()[1]
    templates.env.globals["cv_info"] = lambda job_id: db().latest_tailored_cv(job_id)
    templates.env.globals["prep_info"] = lambda job_id: db().prepared_for_job(job_id)
    templates.env.globals["command_info"] = lambda app_id: db().latest_command(app_id)
    templates.env.globals["apply_url"] = lambda job_id: db().apply_url(job_id)

    def progress_info(job_id: str) -> dict:
        """What the progress strip shows: the steps of a job and the one thing to do next."""
        store = db()
        status, prepared = store.status(job_id) or "new", store.prepared_for_job(job_id)
        prep = prepared.status if prepared else None
        return {
            "steps": steps(status, store.latest_tailored_cv(job_id) is not None, prep, store.cover_letter(job_id) is not None),
            "next": next_step(status, prep),
            "reason": prepared.reason if prepared else None,
        }

    templates.env.globals["progress_info"] = progress_info

    def changed(response: Response, job_id: str) -> Response:
        response.headers["HX-Trigger"] = f"job-changed-{job_id}"  # the progress strip listens and refreshes itself
        return response

    def filter_matches(store: DB, matches: list, show: str) -> tuple[str, list, list]:
        """The chosen filter (or "all"), each filter's count, and the matches it keeps."""
        ready = {m.job_id for m in matches if (p := store.prepared_for_job(m.job_id)) and p.status in ("ready", "filled")}
        tests = {
            "all": lambda m: True,
            "strong": lambda m: m.evaluation.score >= 80,
            "remote": lambda m: m.job.remote,
            "ready": lambda m: m.job_id in ready,
        }
        show = show if show in tests else "all"
        filters = [(key, label, sum(1 for m in matches if tests[key](m))) for key, label in TODAY_FILTERS]
        return show, filters, [m for m in matches if tests[show](m)]

    @app.get("/offers", response_class=HTMLResponse)
    def offers(request: Request, q: str = "", show: str = "all", limit: int = OFFERS_SHOWN):
        store = db()
        matches = [store.match(i) for i in store.open_job_ids(min_score)]
        if q.strip():
            needle = normalize(q)
            matches = [m for m in matches if needle in normalize(f"{m.job.company} {m.job.title} {m.job.location}")]
        show, filters, matches = filter_matches(store, matches, show)
        limit = max(1, limit)
        return templates.TemplateResponse(request, "offers.html", {
            "matches": matches[:limit], "more": max(0, len(matches) - limit), "limit": limit, "q": q, "show": show,
            "filters": filters, "min_score": min_score, "follow_ups": store.follow_up_due_ids(clock()), "active": "offers",
        })

    @app.get("/applied", response_class=HTMLResponse)
    def applied(request: Request):
        return templates.TemplateResponse(request, "applied.html", {"rows": db().applications_list(), "now": clock(), "active": "applied"})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        store = db()
        runs = store.list_runs(1)
        stats = dashboard(store.dashboard_rows(), clock().date(), min_score, store.follow_up_due_ids(clock()))
        return templates.TemplateResponse(
            request, "dashboard.html",
            {**stats, "run": runs[0][1] if runs else None, "warning": run_warning(store.last_success_day(), clock()),
             "min_score": min_score, "active": "dashboard"},
        )

    @app.get("/today", response_class=HTMLResponse)
    def today(request: Request, day: str | None = None, all: bool = False, show: str = "all"):
        store = db()
        notice = None
        parsed = None
        if day:
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                notice = f"Invalid date {day!r}; showing the latest matches."
        day = parsed or store.latest_match_day()
        matches = store.matches_for_day(day) if day else []
        hidden = 0 if all else sum(1 for m in matches if m.evaluation.score < min_score)
        if not all:
            matches = [m for m in matches if m.evaluation.score >= min_score]
        show, filters, matches = filter_matches(store, matches, show)
        return templates.TemplateResponse(
            request,
            "today.html",
            {
                "filters": filters,
                "show": show,
                "show_all": all,
                "day": day,
                "prev_day": _shift(day, -1) if day else None,
                "next_day": _shift(day, 1) if day and day < clock().date() else None,
                "notice": notice,
                "hidden": hidden,
                "min_score": min_score,
                "matches": matches,
                "follow_ups": store.follow_up_due_ids(clock()),
                "active": "today",
            },
        )

    @app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
    def set_status(request: Request, job_id: str, status: str = Form(...), compact: bool = False):
        # ... and require htmx's custom header, which a cross-site form cannot send (CSRF).
        if request.headers.get("hx-request") != "true":
            raise HTTPException(403, "requests must come from the dashboard")
        if status not in STATUSES:
            raise HTTPException(400, f"unknown status {status!r}")
        store = db()
        if store.match(job_id) is None:
            raise HTTPException(404, "unknown job")
        store.set_status(job_id, status, clock())
        prepared = store.prepared_for_job(job_id)
        if status == "applied" and prepared is not None:
            store.update_prepared(prepared.id, clock(), status="applied")
        return templates.TemplateResponse(
            request,
            "_card.html",
            {"m": store.match(job_id), "follow_ups": store.follow_up_due_ids(clock()), "compact": compact},
        )

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline(request: Request):
        store = db()
        groups = store.pipeline()
        return templates.TemplateResponse(
            request,
            "pipeline.html",
            {
                "groups": groups,
                "total": sum(len(v) for v in groups.values()),
                "follow_ups": store.follow_up_due_ids(clock()),
                "active": "pipeline",
            },
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        return templates.TemplateResponse(request, "runs.html", {"runs": db().list_runs(), "active": "runs"})

    companies = companies or []
    by_name = {c.name: c for c in companies}

    def company_row(c: CompanyToCheck, checks: dict[str, datetime]) -> dict:
        return {"c": c, "done_at": checks.get(c.name)}

    def require_htmx(request: Request) -> None:
        if request.headers.get("hx-request") != "true":
            raise HTTPException(403, "requests must come from the dashboard")

    @app.get("/companies", response_class=HTMLResponse)
    def companies_page(request: Request):
        checks = db().company_checks()
        groups: dict[str, list[dict]] = {}
        for c in companies:
            groups.setdefault(c.category, []).append(company_row(c, checks))
        for rows in groups.values():  # to-do first (config order), done last
            rows.sort(key=lambda r: r["done_at"] is not None)
        done = sum(1 for c in companies if c.name in checks)
        return templates.TemplateResponse(
            request, "companies.html", {"groups": groups, "done": done, "total": len(companies), "active": "companies"}
        )

    @app.post("/companies/{name}/toggle", response_class=HTMLResponse)
    def toggle_company(request: Request, name: str):
        require_htmx(request)
        if name not in by_name:
            raise HTTPException(404, "unknown company")
        store = db()
        if name in store.company_checks():
            store.unmark_company(name)
        else:
            store.mark_company_checked(name, clock())
        return templates.TemplateResponse(request, "_company.html", {"r": company_row(by_name[name], store.company_checks())})

    # --- CV ---------------------------------------------------------------------
    def pdf_response(pdf: bytes, content: CvContent, company: str = "") -> Response:
        filename = download_name(content.contact.name, company)
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/cv", response_class=HTMLResponse)
    def cv_page(request: Request):
        _, error = bank_or_error()
        return templates.TemplateResponse(request, "cv.html", {"error": error, "active": "cv"})

    @app.get("/cv/preview", response_class=HTMLResponse)
    def cv_preview():
        bank, error = bank_or_error()
        if bank is None:
            raise HTTPException(404, error)
        return render_html(main_content(bank), for_web=True)

    @app.get("/cv.pdf")
    def cv_pdf():
        bank, error = bank_or_error()
        if bank is None:
            raise HTTPException(404, error)
        content = main_content(bank)
        return pdf_response(render_pdf(content).pdf, content)

    @app.get("/cv-assets/{name}")
    def cv_asset(name: str):
        fonts = {f.name: f for f in FONTS_DIR.glob("*.woff")}
        if name not in fonts:
            raise HTTPException(404, "unknown asset")
        return FileResponse(fonts[name], media_type="font/woff")

    def cv_fragment(request: Request, job_id: str, errors: list[str] | None = None, status_code: int = 200):
        return changed(templates.TemplateResponse(
            request, "_cv.html", {"job_id": job_id, "errors": errors or []}, status_code=status_code
        ), job_id)

    @app.post("/jobs/{job_id}/cv", response_class=HTMLResponse)
    def tailor(request: Request, job_id: str):
        require_htmx(request)
        store = db()
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        if not app.state.tailor_lock.acquire(blocking=False):
            return cv_fragment(request, job_id, ["Already generating a CV; wait for it to finish, then try again."], 409)
        try:
            bank, error = bank_or_error()
            if bank is None:
                return cv_fragment(request, job_id, [error])
            if llm_factory is None:
                return cv_fragment(request, job_id, ["Tailoring is not configured (no Claude client)."])
            match = store.match(job_id)
            try:
                content = tailor_cv(job, match.evaluation if match else None, bank, llm_factory(), draft_model)
            except TailorError as e:
                return cv_fragment(request, job_id, ["Claude's answer broke these rules twice; try again:"] + e.violations)
            except LLMError as e:
                return cv_fragment(request, job_id, [f"Claude is unavailable: {e}"])
            try:
                result = render_pdf(content)
            except Exception as e:  # keep Claude's work; the preview still works without a PDF
                store.save_tailored_cv(job_id, content, 0, draft_model, TAILOR_PROMPT_VERSION)
                return cv_fragment(request, job_id, [f"The CV was saved but the PDF could not be rendered: {e}"])
            if cvs_dir is not None:
                cvs_dir.mkdir(parents=True, exist_ok=True)
                (cvs_dir / cv_filename(job)).write_bytes(result.pdf)
            store.save_tailored_cv(job_id, content, result.pages, draft_model, TAILOR_PROMPT_VERSION)
            return cv_fragment(request, job_id)
        finally:
            app.state.tailor_lock.release()

    def tailored_or_404(job_id: str):
        store = db()
        job, info = store.get_job(job_id), store.latest_tailored_cv(job_id)
        if job is None or info is None:
            raise HTTPException(404, "no tailored CV for this job")
        return job, info[0]

    @app.get("/jobs/{job_id}/cv", response_class=HTMLResponse)
    def tailored_preview(job_id: str):
        _, content = tailored_or_404(job_id)
        return render_html(content, for_web=True)

    @app.get("/jobs/{job_id}/cv.pdf")
    def tailored_pdf(job_id: str):
        job, content = tailored_or_404(job_id)
        path = cvs_dir / cv_filename(job) if cvs_dir is not None else None
        pdf = path.read_bytes() if path is not None and path.exists() else render_pdf(content).pdf
        return pdf_response(pdf, content, job.company)

    # --- prepared applications --------------------------------------------------
    def prep_fragment(request: Request, job_id: str, errors: list[str] | None = None, status_code: int = 200):
        return changed(templates.TemplateResponse(
            request, "_prep.html", {"job_id": job_id, "errors": errors or [], "page": False}, status_code=status_code
        ), job_id)

    @app.get("/jobs/{job_id}/progress", response_class=HTMLResponse)
    def progress(request: Request, job_id: str, page: bool = False):
        if db().get_job(job_id) is None:
            raise HTTPException(404, "unknown job")
        return templates.TemplateResponse(request, "_progress.html", {"job_id": job_id, "page": page})

    def prepared_or_404(app_id: int):
        prepared = db().get_prepared(app_id)
        if prepared is None:
            raise HTTPException(404, "unknown application")
        return prepared

    @app.post("/jobs/{job_id}/prepare", response_class=HTMLResponse)
    def prepare_now(request: Request, job_id: str):
        require_htmx(request)
        if db().get_job(job_id) is None:
            raise HTTPException(404, "unknown job")
        if preparer_factory is None:
            return prep_fragment(request, job_id, ["Preparing is not configured."])
        if not app.state.prepare_lock.acquire(blocking=False):
            return prep_fragment(request, job_id, ["Already preparing an application; wait for it to finish."], 409)
        try:
            preparer_factory().prepare_job(job_id, clock())
        except Exception as e:  # show it on the card; the application row keeps its last state
            return prep_fragment(request, job_id, [f"Preparing failed: {e}"])
        finally:
            app.state.prepare_lock.release()
        return prep_fragment(request, job_id)

    @app.get("/jobs/{job_id}/application", response_class=HTMLResponse)
    def application_page(request: Request, job_id: str):
        store = db()
        job, prepared = store.get_job(job_id), store.prepared_for_job(job_id)
        if job is None or prepared is None:
            raise HTTPException(404, "no application for this job")
        return templates.TemplateResponse(
            request, "application.html",
            {"job": job, "app": prepared, "answers": store.answers(prepared.id), "active": "today"},
        )

    @app.post("/answers/{answer_id}")
    def autosave(request: Request, answer_id: int, final: str = Form("")):
        require_htmx(request)
        if not db().set_final(answer_id, final, clock()):
            raise HTTPException(404, "unknown answer")
        return Response(status_code=204)

    @app.post("/applications/{app_id}/fill", response_class=HTMLResponse)
    async def fill(request: Request, app_id: int):
        require_htmx(request)
        prepared = prepared_or_404(app_id)
        store, now = db(), clock()
        own = {a.id for a in store.answers(app_id)}
        for name, value in (await request.form()).items():  # edits typed in the last second, not yet autosaved
            if name.startswith("final-") and name[6:].isdigit() and int(name[6:]) in own:
                store.set_final(int(name[6:]), str(value), now)
        store.queue_command(app_id, "fill", now)
        return prep_fragment(request, prepared.job_id)

    @app.post("/applications/{app_id}/open", response_class=HTMLResponse)
    def open_on_mac(request: Request, app_id: int):
        require_htmx(request)
        prepared = prepared_or_404(app_id)
        db().queue_command(app_id, "read", clock())
        return prep_fragment(request, prepared.job_id)

    @app.post("/applications/{app_id}/continue", response_class=HTMLResponse)
    def continue_on_mac(request: Request, app_id: int):
        require_htmx(request)
        prepared = prepared_or_404(app_id)
        store = db()
        cmd = store.latest_command(app_id)
        if cmd is not None and cmd.status == "waiting":
            store.set_command(cmd.id, "queued", clock())
        return prep_fragment(request, prepared.job_id)

    @app.post("/applications/{app_id}/applied", response_class=HTMLResponse)
    def mark_applied(request: Request, app_id: int):
        require_htmx(request)
        prepared = prepared_or_404(app_id)
        store, now = db(), clock()
        store.set_status(prepared.job_id, "applied", now)
        store.update_prepared(app_id, now, status="applied")
        return prep_fragment(request, prepared.job_id)

    # --- add a job by link, cover letters --------------------------------------
    @app.post("/jobs/add", response_class=HTMLResponse)
    def add_job(request: Request, url: str = Form("")):
        require_htmx(request)

        def problem(message: str, status_code: int = 200):
            return templates.TemplateResponse(request, "_add.html", {"error": message, "url": url}, status_code=status_code)

        if preparer_factory is None:
            return problem("Adding jobs is not configured.")
        if not url.strip().lower().startswith(("http://", "https://")):
            return problem("Paste the offer's full link (starting with https://).")
        if not app.state.prepare_lock.acquire(blocking=False):
            return problem("Already preparing an application; wait for it to finish.", 409)
        try:
            job_id, error = preparer_factory().add_job(url.strip(), clock())
        except Exception as e:
            return problem(f"Adding the job failed: {e}")
        finally:
            app.state.prepare_lock.release()
        if error:
            return problem(error)
        return Response(status_code=200, headers={"HX-Redirect": f"/jobs/{job_id}/application"})

    @app.post("/jobs/{job_id}/links", response_class=HTMLResponse)
    def use_links(request: Request, job_id: str, apply_url: str = Form(""), role_url: str = Form("")):
        require_htmx(request)
        job_or_404(job_id)
        apply_url, role_url = apply_url.strip(), role_url.strip()
        is_link = lambda u: u.lower().startswith(("http://", "https://"))
        if not is_link(apply_url) or (role_url and not is_link(role_url)):
            return prep_fragment(request, job_id, ["Paste the full link (starting with https://)."])
        if preparer_factory is None:
            return prep_fragment(request, job_id, ["Preparing is not configured."])
        if not app.state.prepare_lock.acquire(blocking=False):
            return prep_fragment(request, job_id, ["Already preparing an application; wait for it to finish."], 409)
        try:
            preparer_factory().use_links(job_id, apply_url, role_url, clock())
        except Exception as e:
            return prep_fragment(request, job_id, [f"Preparing from those links failed: {e}"])
        finally:
            app.state.prepare_lock.release()
        return prep_fragment(request, job_id)

    def job_or_404(job_id: str):
        job = db().get_job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    def cover_fragment(request: Request, job_id: str, errors: list[str] | None = None, status_code: int = 200):
        return changed(
            templates.TemplateResponse(request, "_cover.html", {"job_id": job_id, "errors": errors or []}, status_code=status_code),
            job_id,
        )

    @app.post("/jobs/{job_id}/cover", response_class=HTMLResponse)
    def write_cover(request: Request, job_id: str):
        require_htmx(request)
        job_or_404(job_id)
        if preparer_factory is None:
            return cover_fragment(request, job_id, ["Cover letters are not configured."])
        if not app.state.tailor_lock.acquire(blocking=False):
            return cover_fragment(request, job_id, ["Already writing; wait for it to finish."], 409)
        try:
            preparer_factory().write_cover(job_id, clock())
        except Exception as e:
            return cover_fragment(request, job_id, [f"Writing the cover letter failed: {e}"])
        finally:
            app.state.tailor_lock.release()
        return cover_fragment(request, job_id)

    @app.post("/jobs/{job_id}/cover/final")
    def save_cover(request: Request, job_id: str, final: str = Form("")):
        require_htmx(request)
        if not db().set_cover_final(job_id, final, clock()):
            raise HTTPException(404, "no cover letter for this job")
        return Response(status_code=204)

    def cover_pdf(job_id: str) -> Response:
        job, letter = job_or_404(job_id), db().cover_letter(job_id)
        bank, error = bank_or_error()
        if letter is None or bank is None:
            raise HTTPException(404, error or "no cover letter for this job")
        pdf = render_cover(bank.contact, job, letter["final"], clock().date())
        name = cover_filename(bank.contact.name, job.company)
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/jobs/{job_id}/cover.pdf")
    def cover_download(job_id: str):
        return cover_pdf(job_id)

    templates.env.globals["cover_info"] = lambda job_id: db().cover_letter(job_id)
    templates.env.globals["is_cover_field"] = is_cover_field

    # --- JSON API for the Mac agent ----------------------------------------------
    def require_agent(request: Request) -> None:
        # Like HX-Request: a cross-site page cannot set a custom header, so it cannot drive the agent.
        if request.headers.get("x-jobcopilot-agent") != "1":
            raise HTTPException(403, "requests must come from the jobcopilot agent")

    api = APIRouter(prefix="/api", dependencies=[Depends(require_agent)])

    def command_or_404(cmd_id: int):
        cmd = db().get_command(cmd_id)
        if cmd is None:
            raise HTTPException(404, "unknown command")
        return cmd

    @api.get("/agent/next")
    def agent_next():
        store = db()
        cmd = store.take_next_command(clock())
        if cmd is None:
            return Response(status_code=204)
        job_id = store.get_prepared(cmd.app_id).job_id
        return {"id": cmd.id, "app_id": cmd.app_id, "action": cmd.action, "url": store.form_url(job_id)}

    @api.get("/agent/commands/{cmd_id}")
    def agent_command(cmd_id: int):
        cmd = command_or_404(cmd_id)
        return {"id": cmd.id, "status": cmd.status}

    @api.post("/agent/commands/{cmd_id}/result")
    def agent_result(cmd_id: int, body: dict):
        cmd = command_or_404(cmd_id)
        status = body.get("status")
        if status not in ("waiting", "done", "failed"):
            raise HTTPException(400, "status must be waiting, done or failed")
        store, now = db(), clock()
        result = {"filled": list(body.get("filled") or []), "unfilled": list(body.get("unfilled") or []), "error": body.get("error")}
        store.set_command(cmd_id, status, now, result=result)
        current = store.get_prepared(cmd.app_id)
        if cmd.action == "fill" and status == "done" and result["filled"] and current and current.status in ("ready", "filled"):
            store.update_prepared(cmd.app_id, now, status="filled")
        return {"ok": True}

    @api.get("/applications/{app_id}")
    def agent_application(app_id: int):
        store = db()
        prepared = prepared_or_404(app_id)
        fields = {(f.key, f.step): f for f in (prepared.form.fields if prepared.form else [])}
        answers = store.answers(app_id)
        last_step = max((a.step for a in answers), default=0)
        has_cover = store.cover_letter(prepared.job_id) is not None
        payload = []
        for a in answers:
            field = fields.get((a.field_key, a.step))
            if field is None or a.step != last_step:
                continue
            if a.kind == "leave" and has_cover and field.kind == "file" and is_cover_field(field.label):
                payload.append(field.model_dump() | {"value": "Cover letter", "answer_kind": "cover"})
                continue
            if a.kind == "leave" or (a.kind != "cv" and not a.final.strip()):
                continue
            payload.append(field.model_dump() | {"value": a.final, "answer_kind": a.kind})
        return {"job_url": store.form_url(prepared.job_id), "fields": payload}

    @api.get("/applications/{app_id}/cv.pdf")
    def agent_cv(app_id: int):
        return tailored_pdf(prepared_or_404(app_id).job_id)

    @api.get("/applications/{app_id}/cover.pdf")
    def agent_cover(app_id: int):
        return cover_pdf(prepared_or_404(app_id).job_id)

    @api.post("/applications/{app_id}/form")
    def agent_form(app_id: int, form: FormSnapshot):
        prepared_or_404(app_id)
        if preparer_factory is None:
            raise HTTPException(503, "preparing is not configured")
        try:
            return {"status": preparer_factory().ingest(app_id, form, clock()).status}
        except LLMError as e:
            raise HTTPException(503, f"Claude is unavailable: {e}")

    app.include_router(api)
    return app
