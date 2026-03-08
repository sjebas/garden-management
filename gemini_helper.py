from __future__ import annotations

import json
import os

from google import genai
from google.genai import types


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "identified_plant": {"type": "string"},
        "identification_confidence": {"type": "number"},
        "identification_reason": {"type": "string"},
        "plant_options": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "direct_tips": {"type": "array", "items": {"type": "string"}},
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
    "required": [
        "identified_plant",
        "identification_confidence",
        "identification_reason",
        "plant_options",
        "summary",
        "direct_tips",
        "tasks",
    ],
}


def gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ontbreekt.")
    return genai.Client(api_key=api_key)


def analyze_plant_image(
    *,
    selected_plant_name: str,
    image_bytes: bytes,
    mime_type: str,
    current_month: str,
    plant_profile: dict[str, object] | None,
    existing_tasks: list[dict[str, str]],
    known_plants: list[str],
    allowed_months: list[str],
    allowed_categories: list[str],
    allowed_priorities: list[str],
    allowed_durations: list[str],
) -> dict[str, object]:
    client = gemini_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    plant_context = selected_plant_name or "Niet vooraf gekozen"
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
Je helpt in een Nederlandse tuinbeheer-app voor particulieren.

De gebruiker heeft mogelijk al een plant gekozen: "{plant_context}".
Als dat veld leeg is, gebruik de foto om de plant zo goed mogelijk te herkennen.

Jouw taken:
1. Bepaal welke plant dit waarschijnlijk is.
2. Geef 2 tot 4 directe onderhoudstips.
3. Geef 2 tot 6 concrete onderhoudstaken die in een planning kunnen worden opgeslagen.

Regels:
- Gebruik Nederlands.
- Houd het praktisch en kort.
- Gebruik alleen maanden uit deze lijst: {", ".join(allowed_months)}.
- Gebruik alleen categorieen uit deze lijst: {", ".join(allowed_categories) or "Snoeien, Bemesten, Onderhoud, Beschermen, Controle, Water geven"}.
- Gebruik alleen prioriteiten uit deze lijst: {", ".join(allowed_priorities)}.
- Gebruik bij voorkeur een duur uit deze lijst: {", ".join(allowed_durations) or "5 min, 10 min, 15 min, 30 min, 1 uur"}.
- "plant_options" moet 1 tot 5 mogelijke plantnamen bevatten.
- Gebruik waar mogelijk plantnamen uit deze bestaande plantenlijst: {", ".join(known_plants[:120])}.
- "identified_plant" moet de beste keuze zijn uit "plant_options".
- Gebruik de huidige maand "{current_month}" tenzij een andere maand duidelijk logischer is.
- Geef alleen JSON terug volgens het schema.

Plantprofiel van eventueel gekozen plant:
{chr(10).join(plant_lines) if plant_lines else "- Geen extra profielinformatie beschikbaar"}

Bestaande voorbeeldtaken:
{chr(10).join(examples) if examples else "- Nog geen bestaande taken voor deze plant"}
""".strip()

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt, image_part],
        config={
            "response_mime_type": "application/json",
            "response_schema": ANALYSIS_SCHEMA,
            "temperature": 0.3,
        },
    )
    return json.loads(response.text)
