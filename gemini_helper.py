from __future__ import annotations

import json
import os

from google import genai
from google.genai import types


TASK_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "month": {"type": "string"},
                    "week": {"type": "string"},
                    "category": {"type": "string"},
                    "action": {"type": "string"},
                    "priority": {"type": "string"},
                    "duration": {"type": "string"},
                    "note": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "month",
                    "week",
                    "category",
                    "action",
                    "priority",
                    "duration",
                    "note",
                    "confidence",
                    "reason",
                ],
            },
        },
    },
    "required": ["summary", "tasks"],
}


def gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ontbreekt.")
    return genai.Client(api_key=api_key)


def propose_tasks_from_image(
    *,
    plant_name: str,
    image_bytes: bytes,
    mime_type: str,
    current_month: str,
    plant_profile: dict[str, object] | None,
    existing_tasks: list[dict[str, str]],
    allowed_months: list[str],
    allowed_categories: list[str],
    allowed_priorities: list[str],
    allowed_durations: list[str],
) -> dict[str, object]:
    client = gemini_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    plant_lines = []
    if plant_profile:
        for key in ["Type", "Snoeigroep", "Standplaats", "Winterhard", "Notitie"]:
            value = str(plant_profile.get(key, "")).strip()
            if value:
                plant_lines.append(f"- {key}: {value}")

    examples = []
    for task in existing_tasks[:10]:
        examples.append(
            f"- {task['Maand']} week {task['Week'] or '-'} | {task['Categorie']} | "
            f"{task['Actie']} | prioriteit {task['Prioriteit']} | duur {task['Duur']}"
        )

    prompt = f"""
Je helpt in een Nederlandse tuinbeheer-app.

De gebruiker heeft plant "{plant_name}" gekozen en een actuele foto geüpload.
Analyseer de staat van de plant op de foto en stel alleen concrete, nuttige tuintaken voor die logisch zijn voor deze plant.

Regels:
- Geef 2 tot 6 voorgestelde taken.
- Gebruik Nederlands.
- Wees praktisch en kort.
- Gebruik alleen maanden uit deze lijst: {", ".join(allowed_months)}.
- Gebruik alleen categorieen uit deze lijst: {", ".join(allowed_categories) or "Snoeien, Bemesten, Onderhoud, Beschermen, Controle"}.
- Gebruik alleen prioriteiten uit deze lijst: {", ".join(allowed_priorities)}.
- Gebruik bij voorkeur een duur uit deze lijst: {", ".join(allowed_durations) or "5 min, 10 min, 15 min, 30 min, 1 uur"}.
- Gebruik de huidige maand "{current_month}" tenzij een andere maand duidelijk logischer is.
- Als iets onzeker is op basis van alleen de foto, benoem dat in de reden of notitie.
- Geen algemene uitleg buiten het JSON antwoord.

Plantprofiel:
{chr(10).join(plant_lines) if plant_lines else "- Geen extra profielinformatie beschikbaar"}

Bestaande voorbeeldtaken voor deze plant:
{chr(10).join(examples) if examples else "- Nog geen bestaande taken voor deze plant"}

Geef JSON terug volgens het schema.
""".strip()

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt, image_part],
        config={
            "response_mime_type": "application/json",
            "response_schema": TASK_PROPOSAL_SCHEMA,
            "temperature": 0.3,
        },
    )
    return json.loads(response.text)
