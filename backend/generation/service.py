from __future__ import annotations

import asyncio
from dataclasses import dataclass

from data.repository import Repository, create_repository
from core.logging import get_logger


_log = get_logger(__name__)


@dataclass
class GenerationResult:
    package: dict
    contact_lookup: dict | None = None


def run_package(lead: dict, template: str = "", repo: Repository | None = None) -> dict:
    from generation.generator import run_package as _run_package

    return _run_package(lead, template, repo=repo)


def lookup_contacts(lead: dict, settings: dict | None = None, profile: dict | None = None) -> dict:
    from generation.contact_lookup import run as _lookup_contacts

    return _lookup_contacts(lead, settings=settings, profile=profile)


class GenerationService:
    def __init__(self, repo: Repository | None = None):
        self.repo = repo or create_repository()

    async def generate_package(self, lead: dict, template: str = "") -> dict:
        return await asyncio.to_thread(run_package, lead, template, self.repo)

    async def lookup_contact(self, lead: dict) -> dict:
        settings = await asyncio.to_thread(self.repo.settings.get_settings)
        profile = await asyncio.to_thread(self.repo.profile.get_profile)
        return await asyncio.to_thread(lookup_contacts, lead, settings, profile)

    async def generate_with_contacts(
        self,
        lead: dict,
        *,
        template: str = "",
        include_contacts: bool = True,
    ) -> GenerationResult:
        package = await self.generate_package(lead, template)
        contacts = None
        if include_contacts:
            try:
                contacts = await self.lookup_contact(lead)
                # The LLM-generated cold_email from drafting.py is higher quality
                # than the template-based personalized_email from contact_lookup.py.
                # Prefer it when available so the contact card shows the better email.
                llm_cold_email = (package.get("cold_email") or "").strip()
                if llm_cold_email and contacts.get("primary_contact"):
                    contacts["primary_contact"]["personalized_email"] = llm_cold_email
            except Exception as exc:
                _log.warning("contact lookup skipped for %s: %s", lead.get("job_id", "?"), exc)
                contacts = {"contacts": [], "error": str(exc)}
        return GenerationResult(package=package, contact_lookup=contacts)


def create_generation_service() -> GenerationService:
    return GenerationService()
