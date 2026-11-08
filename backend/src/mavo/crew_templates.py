from __future__ import annotations

from mavo.crew_behaviors import BEHAVIORS
from mavo.schemas import CrewTemplateManifest


def _template(
    template_id: str,
    name: str,
    purpose: str,
    capabilities: list[str],
    resources: list[str],
    triggers: list[str],
) -> CrewTemplateManifest:
    return CrewTemplateManifest(
        id=template_id,
        name=name,
        purpose=purpose,
        supported_capabilities=capabilities,
        compatible_resources=resources,
        default_permissions=["model:invoke"],
        suggested_triggers=triggers,
        behavior=BEHAVIORS[template_id].description,
        default_interval_minutes=BEHAVIORS[template_id].interval,
        implementation_status=(
            "daily_reminders" if template_id == "schedule" else "manual_assistance"
        ),
    )


CREW_TEMPLATES = [
    _template(
        "schedule",
        "Schedule Crew",
        "Organize calendars, deadlines, and daily plans.",
        ["agenda review", "deadline planning", "schedule conflict analysis"],
        ["local-calendar", "local-tasks", "google-calendar"],
        ["daily review", "deadline approaching", "calendar changed"],
    ),
    _template(
        "communication",
        "Communication Crew",
        "Review authorized communications and prepare responses.",
        ["message triage", "response drafting", "follow-up tracking"],
        ["gmail"],
        ["relevant message received", "follow-up due"],
    ),
    _template(
        "computer",
        "Computer Crew",
        "Monitor approved device health and diagnose local issues.",
        ["device health review", "battery analysis", "storage diagnostics"],
        ["cpu-memory", "battery", "storage", "windows-diagnostics"],
        ["battery below threshold", "sustained resource usage", "storage low"],
    ),
    _template(
        "coding",
        "Coding Crew",
        "Track approved software projects and assist with bounded engineering work.",
        ["repository review", "issue triage", "CI analysis"],
        ["local-git", "github"],
        ["CI failed", "issue updated", "repository changed"],
    ),
    _template(
        "health",
        "Health Crew",
        "Help organize explicitly authorized health information with strict isolation.",
        ["health record organization", "appointment preparation"],
        ["health-records"],
        ["health review scheduled"],
    ),
    _template(
        "fitness",
        "Fitness Crew",
        "Support exercise planning and progress reviews.",
        ["workout planning", "fitness trend review"],
        ["fitness-data", "local-calendar"],
        ["workout scheduled", "weekly review"],
    ),
    _template(
        "learning",
        "Learning Crew",
        "Plan study sessions and track learning objectives.",
        ["study planning", "progress review", "revision scheduling"],
        ["local-calendar", "local-tasks", "approved-files"],
        ["study session due", "deadline approaching"],
    ),
    _template(
        "research",
        "Research Crew",
        "Organize research questions, sources, and findings.",
        ["source organization", "research synthesis", "question tracking"],
        ["approved-files", "web-research"],
        ["scheduled research review"],
    ),
    _template(
        "project",
        "Project Crew",
        "Coordinate milestones, tasks, risks, and project updates.",
        ["milestone planning", "risk review", "status briefing"],
        ["local-tasks", "local-calendar", "approved-files"],
        ["milestone approaching", "task changed"],
    ),
    _template(
        "files",
        "Files Crew",
        "Organize and monitor explicitly approved files and folders.",
        ["file organization", "change review", "duplicate analysis"],
        ["approved-files"],
        ["file created", "file modified"],
    ),
    _template(
        "operator",
        "Operator Crew",
        "Coordinate approved multi-step routines with explicit action controls.",
        ["routine coordination", "approval handoff", "outcome verification"],
        ["mavo-goals", "crew-memory"],
        ["approved routine scheduled", "goal state changed"],
    ),
    _template(
        "travel",
        "Travel Crew",
        "Prepare itineraries and track authorized travel details.",
        ["itinerary planning", "travel checklist", "weather review"],
        ["local-calendar", "weather", "approved-files"],
        ["trip approaching", "forecast risk changed"],
    ),
    _template(
        "home",
        "Home Crew",
        "Coordinate authorized household routines and connected services.",
        ["household planning", "maintenance reminders"],
        ["home-assistant", "local-tasks"],
        ["home event received", "maintenance due"],
    ),
    _template(
        "wellbeing",
        "Wellbeing Crew",
        "Support private reflection and wellbeing routines with strict boundaries.",
        ["reflection prompts", "routine review"],
        ["crew-memory", "local-calendar"],
        ["wellbeing check-in"],
    ),
    _template(
        "personal-admin",
        "Personal Admin Crew",
        "Help manage personal paperwork, reminders, and recurring administration.",
        ["admin checklist", "renewal tracking", "document organization"],
        ["local-tasks", "local-calendar", "approved-files"],
        ["renewal approaching", "weekly admin review"],
    ),
]

CREW_TEMPLATES_BY_ID = {template.id: template for template in CREW_TEMPLATES}
