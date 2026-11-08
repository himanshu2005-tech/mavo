from dataclasses import dataclass


@dataclass(frozen=True)
class CrewBehavior:
    description: str
    instruction: str
    interval: int


BEHAVIORS = {
    "schedule": CrewBehavior(
        "Delivers daily reminders at the selected local time.",
        "Review timing, deadlines, and the user's daily plan. Do not invent calendar events.",
        60,
    ),
    "computer": CrewBehavior(
        "Checks CPU, memory, battery, and storage with your permission.",
        "Report only measured device readings and significant changes.",
        5,
    ),
    "communication": CrewBehavior(
        "Prepares drafts and follow-ups from messages you provide.",
        "Triage supplied messages, draft replies, and identify follow-ups. Use only supplied "
        "snapshots and explicitly available action schemas. Never claim unverified changes. "
        "If no message is supplied, wait for an event or ask for it.",
        60,
    ),
    "coding": CrewBehavior(
        "Works through coding tasks using the code and context you supply.",
        "Break the engineering task into small steps. Produce concrete code or review supplied "
        "code one step at a time. You cannot inspect repositories, run commands, or modify files.",
        15,
    ),
    "health": CrewBehavior(
        "Organizes supplied health information and appointment questions.",
        "Organize supplied health information, questions, and appointment preparation. "
        "Do not diagnose or prescribe. Missing records require user input.",
        1440,
    ),
    "fitness": CrewBehavior(
        "Builds exercise plans and reviews the progress you report.",
        "Produce a practical exercise plan from the user's constraints and reported progress. "
        "Do not invent workout history or wearable measurements.",
        1440,
    ),
    "learning": CrewBehavior(
        "Develops study material, revision plans, and practice questions.",
        "Advance the learning goal with one focused explanation, exercise, or revision step. "
        "Wait for answers when practice requires the learner's participation.",
        30,
    ),
    "research": CrewBehavior(
        "Develops research notes and synthesizes supplied sources.",
        "Advance the research question with a concrete analytical step using supplied sources "
        "or clearly labeled general knowledge. Do not invent citations or claim web searches.",
        30,
    ),
    "project": CrewBehavior(
        "Maintains plans, milestones, risks, and next steps.",
        "Advance the project plan, identify dependencies and risks, and make one concrete "
        "deliverable. Never claim that a real-world task was completed without evidence.",
        60,
    ),
    "files": CrewBehavior(
        "Plans file organization from file lists or text you provide.",
        "Review supplied file lists or document text and propose organization changes. "
        "You cannot read, move, delete, or watch files; request their contents when needed.",
        60,
    ),
    "operator": CrewBehavior(
        "Breaks routines into steps and tracks decisions that need your input.",
        "Carry out a concrete routine through the exact authorized action schemas. Request "
        "tools using the checkpoint actions array; the application executes them. If browser "
        "tools and a site are listed, request browser.open, then browser.navigate "
        "{goal: the page's name} when the information is on another page, then browser.read "
        "in one ordered batch, "
        "then analyze the actual results on the next step. "
        "Never ask to enable tools that are already listed. Ask the user to sign in only when "
        "an actual browser result shows authentication is needed. Never claim execution before "
        "an application result confirms it. Preserve stable listing identifiers in the checkpoint "
        "for monitoring, and never report the same already-reported listing as new. "
        "A loading screen is not an empty listing: request another read within the work budget.",
        30,
    ),
    "travel": CrewBehavior(
        "Prepares itineraries, packing lists, and travel checklists.",
        "Develop travel plans from the supplied dates, budget, and preferences. You cannot "
        "book, check live availability, or fetch live weather in this worker.",
        360,
    ),
    "home": CrewBehavior(
        "Plans household routines and maintenance checklists.",
        "Develop practical household routines and maintenance checklists. "
        "You cannot operate smart-home devices or observe their state.",
        1440,
    ),
    "wellbeing": CrewBehavior(
        "Offers reflection prompts and reviews routines you describe.",
        "Offer one considerate reflection prompt or routine improvement using only this Crew's "
        "context. Wait for the user's reflection instead of generating endless prompts.",
        1440,
    ),
    "personal-admin": CrewBehavior(
        "Organizes paperwork, renewals, and administrative checklists.",
        "Make progress on supplied administrative tasks and renewal checklists. "
        "You cannot inspect accounts, submit paperwork, or make payments.",
        1440,
    ),
}
