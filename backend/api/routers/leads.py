from __future__ import annotations
import logging

import asyncio
import csv
import io
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi import Depends
from fastapi.responses import FileResponse, StreamingResponse

from api.dependencies import get_generation_service, get_job_runner, get_ranking_service, get_repository
from api.rate_limit import RateLimiter, require_rate_limit
from core.paths import app_data_path
from core.types import FeedbackBody, FollowupBody, ManualLeadBody, StatusBody
from data.repository import Repository

MANUAL_FEEDBACK_TIMEOUT_SECONDS = 8
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def default_assets_dir() -> str:
    return str(app_data_path("assets"))


def annotate_job_lead(lead: dict) -> dict:
    from gateway.lead_adapters import classify_job_seniority

    meta = dict(lead.get("source_meta") or {})
    level = str(meta.get("seniority_level") or lead.get("seniority_level") or "").strip().lower()
    if level not in {"fresher", "junior", "mid", "senior", "unknown"}:
        level = classify_job_seniority(lead)
    meta["seniority_level"] = level
    meta["is_beginner"] = level in {"fresher", "junior"}
    return {**lead, "source_meta": meta, "seniority_level": level}


def versioned_assets(job_id: str, base_dir: str) -> list[dict]:
    versions: dict[int, dict] = {}
    patterns = [
        ("resume", re.compile(rf"^{re.escape(job_id)}_v(\d+)\.pdf$")),
        ("cover_letter", re.compile(rf"^{re.escape(job_id)}_cl_v(\d+)\.pdf$")),
    ]
    try:
        names = os.listdir(base_dir)
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in backend/api/routers/leads.py:versioned_assets: %s', log_exc)
        return []
    for name in names:
        full = os.path.join(base_dir, name)
        if not os.path.isfile(full):
            continue
        for key, pattern in patterns:
            match = pattern.match(name)
            if match:
                version = int(match.group(1))
                versions.setdefault(version, {"version": version})[key] = full
    return [versions[version] for version in sorted(versions, reverse=True)]


def create_router(manager) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["leads"])
    manual_limiter = RateLimiter(10, 60)

    def _safe_job_id(job_id: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]{1,128}$", job_id):
            raise HTTPException(status_code=400, detail="Invalid job ID format")
        return job_id

    @router.get("/leads")
    async def leads(
        page: int | None = None,
        limit: int = 200,
        beginner_only: bool = False,
        seniority: str | None = None,
        status: str | None = None,
        min_score: int | None = None,
        repo: Repository = Depends(get_repository),
    ):
        all_leads = await asyncio.to_thread(repo.leads.get_all_leads)
        jobs = [annotate_job_lead(lead) for lead in all_leads if (lead.get("kind") or "job") == "job"]
        requested = str(seniority or "").strip().lower()
        if beginner_only or requested == "beginner":
            jobs = [lead for lead in jobs if lead.get("seniority_level") in {"fresher", "junior"}]
        elif requested in {"fresher", "junior", "mid", "senior", "unknown"}:
            jobs = [lead for lead in jobs if lead.get("seniority_level") == requested]
        if status:
            jobs = [lead for lead in jobs if str(lead.get("status") or "") == status]
        if min_score is not None:
            jobs = [lead for lead in jobs if int(lead.get("score") or 0) >= min_score]
        if page is None:
            return jobs
        page = max(1, page)
        limit = max(1, min(limit, 1000))
        total = len(jobs)
        start = (page - 1) * limit
        return {"items": jobs[start:start + limit], "total": total, "page": page, "limit": limit, "pages": (total + limit - 1) // limit}

    @router.get("/leads/export.csv")
    async def export_leads_csv(repo: Repository = Depends(get_repository)):
        rows = await asyncio.to_thread(repo.leads.get_all_leads)
        fields = [
            "job_id",
            "title",
            "company",
            "url",
            "platform",
            "status",
            "score",
            "signal_score",
            "seniority_level",
            "location",
            "reason",
            "created_at",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=jhm_pipeline.csv"},
        )

    @router.get("/leads/{job_id}/versions")
    async def get_lead_versions(job_id: str, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        paths = [
            lead.get("resume_asset") or lead.get("asset") or "",
            lead.get("cover_letter_asset") or "",
        ]
        base_dir = next((os.path.dirname(path) for path in paths if path), None)
        if not base_dir:
            base_dir = default_assets_dir()
        return versioned_assets(job_id, base_dir)

    @router.get("/leads/{job_id}")
    async def get_lead(job_id: str, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        return annotate_job_lead(lead) if (lead.get("kind") or "job") == "job" else lead

    @router.delete("/leads/{job_id}")
    async def delete_lead_endpoint(job_id: str, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        try:
            await asyncio.to_thread(repo.leads.delete_lead, job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="lead not found") from exc
        return {"ok": True}

    @router.put("/leads/{job_id}/status")
    async def update_status(job_id: str, body: StatusBody, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        try:
            await asyncio.to_thread(repo.leads.update_lead_status, job_id, body.status)
            await manager.broadcast({"type": "LEAD_UPDATED", "data": {"job_id": job_id, "status": body.status}})
            return {"ok": True}
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="lead not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/leads/{job_id}/feedback")
    async def update_feedback(job_id: str, body: FeedbackBody, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        try:
            lead = await asyncio.to_thread(repo.leads.save_lead_feedback, job_id, body.feedback, body.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        await manager.broadcast({"type": "LEAD_UPDATED", "data": lead})
        return lead

    @router.put("/leads/{job_id}/followup")
    async def update_followup(job_id: str, body: FollowupBody, repo: Repository = Depends(get_repository)):
        job_id = _safe_job_id(job_id)
        from datetime import datetime, timedelta, timezone

        days = max(1, min(int(body.days or 5), 60))
        now = datetime.now(timezone.utc).isoformat()
        due = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        lead = await asyncio.to_thread(repo.leads.update_lead_followup, job_id, now, due)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        await manager.broadcast({"type": "LEAD_UPDATED", "data": lead})
        return lead

    @router.post("/leads/manual")
    async def create_manual_lead(
        body: ManualLeadBody,
        repo: Repository = Depends(get_repository),
        ranking_service=Depends(get_ranking_service),
    ):
        require_rate_limit(manual_limiter)
        if not body.text.strip() and not body.url.strip():
            raise HTTPException(status_code=400, detail="Paste lead text or a URL")
        from gateway.lead_adapters import manual_lead_from_text

        raw_lead = manual_lead_from_text(body.text, body.url, "job")
        examples = await asyncio.to_thread(repo.feedback.get_feedback_training_examples)
        try:
            lead = await asyncio.wait_for(
                ranking_service.apply_feedback(raw_lead, examples),
                timeout=MANUAL_FEEDBACK_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning('suppressed exception in backend/api/routers/leads.py:create_manual_lead: %s', exc)
            meta = dict(raw_lead.get("source_meta") or {})
            meta["feedback_learning_error"] = str(exc) or "timed out"
            lead = {**raw_lead, "source_meta": meta}
        if lead.get("kind") != "job":
            raise HTTPException(status_code=422, detail="Only job leads are accepted right now")
        lead = annotate_job_lead(lead)
        await asyncio.to_thread(repo.leads.save_lead, lead)
        saved = await asyncio.to_thread(repo.leads.get_lead_by_id, lead["job_id"]) or lead
        await manager.broadcast({"type": "LEAD_UPDATED", "data": saved})
        return saved

    @router.post("/leads/manual/generate/start")
    async def create_manual_lead_and_start_generation(
        body: ManualLeadBody,
        repo: Repository = Depends(get_repository),
        service=Depends(get_generation_service),
        job_store=Depends(get_job_runner),
    ):
        require_rate_limit(manual_limiter)
        if not body.text.strip() and not body.url.strip():
            raise HTTPException(status_code=400, detail="Paste lead text or a URL")
        from api.routers.generation import generate_one
        from gateway.lead_adapters import manual_lead_from_text

        raw_lead = manual_lead_from_text(body.text, body.url, "job")
        if raw_lead.get("kind") != "job":
            raise HTTPException(status_code=422, detail="Only job leads are accepted right now")
        lead = annotate_job_lead(raw_lead)
        from core.generation_readiness import lead_generation_blocker

        blocked_reason = lead_generation_blocker(lead)
        if blocked_reason:
            repo.leads.save_lead(lead)
            saved = repo.leads.get_lead_by_id(lead["job_id"]) or lead
            await manager.broadcast({"type": "LEAD_UPDATED", "data": saved})
            raise HTTPException(status_code=422, detail=blocked_reason)
        queued_lead = {**lead, "status": "tailoring"}

        async def _run():
            try:
                await asyncio.to_thread(repo.leads.save_lead, lead)
                try:
                    await asyncio.to_thread(repo.leads.update_lead_status, lead["job_id"], "tailoring")
                except Exception as log_exc:
                    logging.getLogger(__name__).warning('suppressed exception in backend/api/routers/leads.py:_run: %s', log_exc)
                    pass
                saved = await asyncio.to_thread(repo.leads.get_lead_by_id, lead["job_id"])
                await manager.broadcast({"type": "LEAD_UPDATED", "data": saved or queued_lead})
                await generate_one(lead["job_id"], manager, repo=repo, service=service, job_store=job_store)
            except Exception as exc:
                logging.getLogger(__name__).warning('suppressed exception in backend/api/routers/leads.py:_run: %s', exc)
                failed = {**queued_lead, "status": "discovered"}
                meta = dict(failed.get("source_meta") or {})
                meta["generation_error"] = str(exc)
                failed["source_meta"] = meta
                await manager.broadcast({"type": "LEAD_UPDATED", "data": failed})
                await manager.broadcast({
                    "type": "agent",
                    "event": "gen_error",
                    "msg": f"Generation failed for {lead.get('title','?')}: {exc}",
                })

        _track_background_task(asyncio.create_task(_run()))
        await manager.broadcast({"type": "LEAD_UPDATED", "data": queued_lead})
        return {"status": "started", "job_id": lead["job_id"], "lead": queued_lead}

    @router.get("/followups/due")
    async def due_followups(limit: int = 25, repo: Repository = Depends(get_repository)):
        from datetime import datetime, timezone

        return await asyncio.to_thread(repo.leads.get_due_followups, limit, datetime.now(timezone.utc).isoformat())

    @router.get("/leads/{job_id}/pdf")
    async def get_lead_pdf(
        job_id: str,
        kind: str = "resume",
        version: int | None = None,
        repo: Repository = Depends(get_repository),
    ):
        job_id = _safe_job_id(job_id)
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        is_cover = kind in {"cover", "cover_letter", "cover-letter"}
        if version is not None:
            paths = [
                lead.get("resume_asset") or lead.get("asset") or "",
                lead.get("cover_letter_asset") or "",
            ]
            base_dir = next((os.path.dirname(path) for path in paths if path), None)
            if not base_dir:
                base_dir = default_assets_dir()
            filename = f"{job_id}_cl_v{version}.pdf" if is_cover else f"{job_id}_v{version}.pdf"
            path = os.path.join(base_dir, filename)
            missing = "Cover letter not generated yet" if is_cover else "Resume not generated yet"
        elif is_cover:
            path = lead.get("cover_letter_asset") or ""
            filename = f"{job_id}_cover_letter.pdf"
            missing = "Cover letter not generated yet"
        else:
            path = lead.get("resume_asset") or lead.get("asset") or ""
            filename = f"{job_id}_resume.pdf"
            missing = "Resume not generated yet"
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail=missing)
        return FileResponse(path, media_type="application/pdf", filename=filename)

    # ------------------------------------------------------------------ #
    # Feature: Skills gap analysis                                         #
    # ------------------------------------------------------------------ #
    @router.get("/leads/gap-analysis")
    async def gap_analysis(
        min_score: int = 50,
        repo: Repository = Depends(get_repository),
    ):
        """
        Aggregate the `gaps` field across all scored, non-discarded leads
        and return the most common gaps. Helps the user understand which
        skills to add to their profile to increase match rates.
        """
        leads = await asyncio.to_thread(repo.leads.get_all_leads)
        candidates = [
            lead for lead in leads
            if lead.get("status") != "discarded" and (lead.get("score") or 0) >= min_score
        ]

        from collections import Counter
        import re as _re

        gap_counter: Counter = Counter()
        skill_counter: Counter = Counter()

        # Known skill-like tokens to extract from gap text
        _SKILL_RE = _re.compile(
            r"\b(TypeScript|JavaScript|Python|Java|Go|Rust|C\+\+|React|Vue|Angular|"
            r"Node\.?js|FastAPI|Django|Flask|Spring|Docker|Kubernetes|AWS|GCP|Azure|"
            r"PostgreSQL|MySQL|MongoDB|Redis|Kafka|GraphQL|REST|CI/CD|DevOps|"
            r"Machine Learning|ML|AI|LLM|TensorFlow|PyTorch|Pandas|Numpy|SQL|"
            r"Git|Linux|Terraform|Ansible|Jenkins|GitHub Actions)\b",
            _re.I,
        )

        for lead in candidates:
            for gap in (lead.get("gaps") or []):
                gap_text = str(gap).strip()
                if not gap_text:
                    continue
                # Normalise gap text: lowercase, strip trailing punctuation
                key = gap_text.rstrip(".!?").lower()
                gap_counter[key] += 1
                # Extract skill mentions from the gap text
                for match in _SKILL_RE.findall(gap_text):
                    skill_counter[match.lower()] += 1

        total = len(candidates)
        top_gaps = [
            {"gap": gap, "count": cnt, "pct": round(cnt / total * 100) if total else 0}
            for gap, cnt in gap_counter.most_common(15)
        ]
        top_skills = [
            {"skill": skill, "count": cnt}
            for skill, cnt in skill_counter.most_common(10)
        ]

        return {
            "total_leads_analyzed": total,
            "min_score_filter": min_score,
            "top_gaps": top_gaps,
            "skills_to_add": top_skills,
            "summary": (
                f"Analysed {total} leads scoring {min_score}+. "
                f"Most common gap: '{top_gaps[0]['gap']}' ({top_gaps[0]['pct']}% of leads)"
                if top_gaps else f"No gaps found in {total} scored leads."
            ),
        }

    # ------------------------------------------------------------------ #
    # Feature: Interview prep generator                                    #
    # ------------------------------------------------------------------ #
    @router.post("/leads/{job_id}/interview-prep")
    async def generate_interview_prep(
        job_id: str,
        repo: Repository = Depends(get_repository),
    ):
        """
        Generate 10 likely interview questions + suggested answers based on
        the job description, the candidate profile, and the lead's gap analysis.
        Result is saved to the lead and returned.
        """
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        profile = await asyncio.to_thread(repo.profile.get_profile)
        _ = await asyncio.to_thread(repo.settings.get_settings)  # reserved for future use

        from llm import call_raw

        gaps_text    = "\n".join(f"- {g}" for g in (lead.get("gaps") or []))
        matches_text = "\n".join(f"- {m}" for m in (lead.get("match_points") or []))
        skills       = ", ".join(s.get("n", "") for s in (profile.get("skills") or []))
        exp_text     = "\n".join(
            f"- {e.get('role','')} at {e.get('co','')} ({e.get('period','')})"
            for e in (profile.get("exp") or [])
        )

        system = (
            "You are a senior engineering interview coach. Generate exactly 10 interview "
            "questions for this candidate + role, with a concise suggested answer for each. "
            "Base questions on the job requirements, the candidate's actual strengths, "
            "and especially their identified gaps — interviewers probe gaps most. "
            "Format each as:\nQ: [question]\nA: [2-3 sentence answer using the candidate's real evidence]\n"
            "Questions should mix: 2 behavioural, 3 technical, 2 project deep-dives, "
            "1 gap-addressing, 1 culture fit, 1 closing question. "
            "Answers must reference the candidate's actual profile — never invent facts."
        )
        user = (
            f"ROLE: {lead.get('title','')} at {lead.get('company','')}\n"
            f"JOB DESCRIPTION:\n{(lead.get('description',''))[:2000]}\n\n"
            f"CANDIDATE STRENGTHS:\n{matches_text or 'None listed'}\n\n"
            f"CANDIDATE GAPS (probe these):\n{gaps_text or 'None listed'}\n\n"
            f"CANDIDATE SKILLS: {skills}\n\n"
            f"WORK EXPERIENCE:\n{exp_text or 'No experience listed'}\n\n"
            "Generate 10 interview questions and answers:"
        )

        try:
            result = await asyncio.to_thread(call_raw, system, user, step="generator")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"LLM call failed: {exc}") from exc

        # Save to lead's interview_prep field
        try:
            conn = repo.leads._get_connection() if hasattr(repo.leads, "_get_connection") else None
            if conn is None:
                from data.sqlite.connection import get_connection
                conn = get_connection()
            conn.execute(
                "UPDATE leads SET interview_prep=? WHERE job_id=?",
                (result, job_id),
            )
            conn.commit()
            conn.close()
        except Exception as _save_exc:
            logging.getLogger(__name__).debug("interview_prep save skipped for %s: %s", job_id, _save_exc)

        return {"job_id": job_id, "interview_prep": result}

    @router.get("/leads/{job_id}/interview-prep")
    async def get_interview_prep(
        job_id: str,
        repo: Repository = Depends(get_repository),
    ):
        """Return previously generated interview prep for a lead."""
        lead = await asyncio.to_thread(repo.leads.get_lead_by_id, job_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        prep = lead.get("interview_prep", "")
        return {"job_id": job_id, "interview_prep": prep, "generated": bool(prep)}

    # ------------------------------------------------------------------ #
    # Feature: Source performance dashboard                                #
    # ------------------------------------------------------------------ #
    @router.get("/leads/source-stats")
    async def source_stats(repo: Repository = Depends(get_repository)):
        """
        Aggregate leads by their source platform and return per-source
        performance metrics: total found, avg score, applied count.
        Helps the user see which job sources are yielding the best results.
        """
        leads = await asyncio.to_thread(repo.leads.get_all_leads)
        non_discarded = [lead for lead in leads if lead.get("status") != "discarded"]

        from collections import defaultdict
        buckets: dict = defaultdict(lambda: {
            "total": 0, "scored": 0, "score_sum": 0,
            "applied": 0, "approved": 0, "high_quality": 0,
        })

        for lead in non_discarded:
            src = (lead.get("platform") or "unknown").lower().strip() or "unknown"
            b = buckets[src]
            b["total"] += 1
            score = lead.get("score") or 0
            if score > 0:
                b["scored"] += 1
                b["score_sum"] += score
            if lead.get("status") == "applied":
                b["applied"] += 1
            if lead.get("status") in ("approved", "interviewing", "accepted"):
                b["approved"] += 1
            if score >= 75:
                b["high_quality"] += 1

        by_source = []
        for src, b in sorted(buckets.items(), key=lambda x: x[1]["total"], reverse=True):
            avg_score = round(b["score_sum"] / b["scored"]) if b["scored"] > 0 else 0
            by_source.append({
                "source":       src,
                "total":        b["total"],
                "scored":       b["scored"],
                "avg_score":    avg_score,
                "high_quality": b["high_quality"],
                "applied":      b["applied"],
                "approved":     b["approved"],
            })

        return {
            "total_leads":  len(non_discarded),
            "by_source":    by_source,
            "best_source":  by_source[0]["source"] if by_source else None,
        }

    return router
