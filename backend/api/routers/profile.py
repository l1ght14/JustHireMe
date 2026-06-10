from __future__ import annotations

import inspect

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_profile_service
from core.types import CandidateBody, ExperienceBody, IdentityBody, ProfileEntryBody, ProjectBody, SkillBody
from data.graph.connection import run_graph


router = APIRouter(prefix="/api/v1", tags=["profile"])


@router.get("/profile")
async def get_profile_endpoint(service=Depends(get_profile_service)):
    return await _call_service(service.get_profile)


@router.put("/profile/candidate")
async def update_candidate_endpoint(body: CandidateBody, service=Depends(get_profile_service)):
    if not body.n.strip() and not body.s.strip():
        raise HTTPException(status_code=422, detail="Name or summary is required")
    return await _call_service(service.update_candidate, body.n, body.s)


@router.put("/profile/identity")
async def update_identity_endpoint(body: IdentityBody, service=Depends(get_profile_service)):
    return await _call_service(service.update_identity, body.model_dump())


@router.post("/profile/skill")
async def add_skill_endpoint(body: SkillBody, service=Depends(get_profile_service)):
    if not body.n.strip():
        raise HTTPException(status_code=422, detail="Skill name is required")
    return await _call_service(service.add_skill, body.n, body.cat)


@router.put("/profile/skill/{sid}")
async def update_skill_endpoint(sid: str, body: SkillBody, service=Depends(get_profile_service)):
    if not body.n.strip():
        raise HTTPException(status_code=422, detail="Skill name is required")
    return await _call_service(service.update_skill, sid, body.n, body.cat)


@router.delete("/profile/skill/{sid}")
async def delete_skill_endpoint(sid: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_skill, sid)
    return {"ok": True}


@router.post("/profile/experience")
async def add_experience_endpoint(body: ExperienceBody, service=Depends(get_profile_service)):
    if not body.role.strip() and not body.co.strip():
        raise HTTPException(status_code=422, detail="Role or company is required")
    return await _call_service(service.add_experience, body.role, body.co, body.period, body.d)


@router.put("/profile/experience/{eid}")
async def update_experience_endpoint(eid: str, body: ExperienceBody, service=Depends(get_profile_service)):
    if not body.role.strip() and not body.co.strip():
        raise HTTPException(status_code=422, detail="Role or company is required")
    return await _call_service(service.update_experience, eid, body.role, body.co, body.period, body.d)


@router.delete("/profile/experience/{eid}")
async def delete_experience_endpoint(eid: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_experience, eid)
    return {"ok": True}


@router.post("/profile/project")
async def add_project_endpoint(body: ProjectBody, service=Depends(get_profile_service)):
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Project title is required")
    return await _call_service(service.add_project, body.title, body.stack, body.repo, body.impact)


@router.put("/profile/project/{pid}")
async def update_project_endpoint(pid: str, body: ProjectBody, service=Depends(get_profile_service)):
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Project title is required")
    return await _call_service(service.update_project, pid, body.title, body.stack, body.repo, body.impact)


@router.delete("/profile/project/{pid}")
async def delete_project_endpoint(pid: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_project, pid)
    return {"ok": True}


@router.post("/profile/education")
async def add_education_endpoint(body: ProfileEntryBody, service=Depends(get_profile_service)):
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Education title is required")
    return await _call_service(service.add_education, body.title)


@router.delete("/profile/education/{entry:path}")
async def delete_education_endpoint(entry: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_education, entry)
    return {"ok": True}


@router.post("/profile/certification")
async def add_certification_endpoint(body: ProfileEntryBody, service=Depends(get_profile_service)):
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Certification title is required")
    return await _call_service(service.add_certification, body.title)


@router.delete("/profile/certification/{entry:path}")
async def delete_certification_endpoint(entry: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_certification, entry)
    return {"ok": True}


@router.post("/profile/achievement")
async def add_achievement_endpoint(body: ProfileEntryBody, service=Depends(get_profile_service)):
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Achievement title is required")
    return await _call_service(service.add_achievement, body.title)


@router.delete("/profile/achievement/{entry:path}")
async def delete_achievement_endpoint(entry: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_achievement, entry)
    return {"ok": True}


@router.delete("/profile/achievement/{entry:path}")
async def delete_achievement_endpoint(entry: str, service=Depends(get_profile_service)):
    await _call_service(service.delete_achievement, entry)
    return {"ok": True}


@router.get("/profile/completeness")
async def profile_completeness_endpoint(service=Depends(get_profile_service)):
    """
    Returns a completeness score (0-100) and a prioritised list of missing
    profile fields. Higher completeness → better fit scoring accuracy.
    """
    profile = await _call_service(service.get_profile)
    return _compute_completeness(profile or {})


def _compute_completeness(p: dict) -> dict:
    checks: list[dict] = []
    score = 0

    def check(key: str, label: str, points: int, ok: bool, tip: str) -> None:
        nonlocal score
        if ok:
            score += points
        checks.append({"key": key, "label": label, "points": points, "ok": ok, "tip": tip})

    name     = str(p.get("n") or "").strip()
    summary  = str(p.get("s") or "").strip()
    skills   = p.get("skills") or []
    exp      = p.get("exp") or []
    projects = p.get("projects") or []
    edu      = p.get("education") or []
    identity = p.get("identity") or {}
    email    = str(identity.get("email") or "").strip()
    linkedin = str(identity.get("linkedin_url") or "").strip()
    github   = str(identity.get("github_url") or "").strip()
    city     = str(identity.get("city") or "").strip()

    check("name",      "Full name",              10, bool(name),
          "Add your name in Profile → Identity so it appears on generated resumes.")
    check("summary",   "Professional summary",   10, bool(summary),
          "Write a 2-3 sentence summary in Profile → Identity. This is the first thing the AI uses to score fit.")
    check("email",     "Email address",           8, bool(email),
          "Add your email in Profile → Contact & Links — required for generated documents and contact lookup.")
    check("skills_3",  "At least 3 skills",      15, len(skills) >= 3,
          f"You have {len(skills)} skill(s). Add more in Profile → Skills — skills drive keyword matching.")
    check("experience","Work experience",         15, len(exp) >= 1,
          "Add at least one job in Profile → Experience. Without it, senior roles will score low against you.")
    check("project",   "At least 1 project",     12, len(projects) >= 1,
          "Add a project in Profile → Projects with its tech stack. Projects are strong evidence for fit scoring.")
    check("linkedin",  "LinkedIn URL",             8, bool(linkedin),
          "Add your LinkedIn URL in Profile → Contact & Links. Used for contact lookup and outreach.")
    check("github",    "GitHub URL",               7, bool(github),
          "Add your GitHub URL to let the app analyse your repositories for additional skill evidence.")
    check("city",      "City / location",          5, bool(city),
          "Add your city in Profile → Contact & Links for location-aware job matching.")
    check("education", "Education",                5, len(edu) >= 1,
          "Add your degree/institution in Profile → Education.")
    check("skills_5",  "5+ skills (bonus)",        5, len(skills) >= 5,
          f"You have {len(skills)} skill(s). Aim for 8-12 skills covering your main technologies.")

    missing = [c for c in checks if not c["ok"]]
    # Sort missing: highest-points first
    missing.sort(key=lambda c: c["points"], reverse=True)

    return {
        "score":       score,
        "max_score":   100,
        "pct":         score,
        "status":      "excellent" if score >= 85 else "good" if score >= 65 else "needs_work",
        "checks":      checks,
        "missing":     missing,
        "top_action":  missing[0]["tip"] if missing else None,
    }


async def _call_service(method, *args, **kwargs):
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await run_graph(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
