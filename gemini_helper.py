from __future__ import annotations

import json
import os
from typing import Iterable

from google import genai
from google.genai import types


DEFAULT_GEMINI_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
]


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "identified_plant": {"type": "string"},
        "identification_confidence": {"type": "number"},
        "identification_reason": {"type": "string"},
        "plant_options": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "year_round_maintenance": {"type": "array", "items": {"type": "string"}},
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
        "year_round_maintenance",
        "tasks",
    ],
}


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    pass


def gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ontbreekt.")
    return genai.Client(api_key=api_key)


def _model_candidates() -> list[str]:
    configured = os.getenv("GEMINI_MODEL", "").strip()
    configured_fallbacks = [
        item.strip()
        for item in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",")
        if item.strip()
    ]
    ordered: list[str] = [*([configured] if configured else []), *DEFAULT_GEMINI_MODELS, *configured_fallbacks]
    seen = set()
    models = []
    for model in ordered:
        lowered = model.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        models.append(model)
    return models


def _is_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    quota_markers = [
        "resource_exhausted",
        "quota",
        "rate limit",
        "429",
        "retrydelay",
        "generaterequestsperday",
    ]
    return any(marker in message for marker in quota_markers)


def _generate_with_fallback(
    client: genai.Client,
    *,
    contents: Iterable[object],
    config: dict[str, object],
) -> str:
    quota_failures = []
    last_error: Exception | None = None

    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=list(contents),
                config=config,
            )
            return response.text
        except Exception as exc:
            last_error = exc
            if _is_quota_error(exc):
                quota_failures.append(model)
                continue
            raise GeminiError(
                "Er ging iets mis bij het maken van een voorstel. Probeer het zo nog eens."
            ) from exc

    if quota_failures:
        raise GeminiQuotaError(
            "De slimme invoer zit even aan het daglimiet. Probeer het later opnieuw."
        ) from last_error
    raise GeminiError("Er ging iets mis bij het maken van een voorstel. Probeer het zo nog eens.") from last_error


def analyze_plant_image(
    *,
    selected_plant_name: str,
    image_bytes: bytes | None,
    mime_type: str | None,
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
    has_image = bool(image_bytes and mime_type)

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
Als er geen foto is, gebruik dan de gekozen plantnaam en eventuele profielinformatie als hoofdbron.

Jouw taken:
1. Bepaal welke plant dit waarschijnlijk is.
2. Geef een korte, geruststellende samenvatting van deze plant en de huidige toestand.
3. Geef een compact jaar-rond onderhoudsoverzicht in korte bullets.
4. Geef een volledig jaarplan met concrete onderhoudstaken die de gebruiker meteen kan opslaan.

Regels:
- Gebruik Nederlands.
- Houd het praktisch, duidelijk en compact.
- Gebruik alleen maanden uit deze lijst: {", ".join(allowed_months)}.
- Gebruik alleen categorieen uit deze lijst: {", ".join(allowed_categories) or "Snoeien, Bemesten, Onderhoud, Beschermen, Controle, Water geven"}.
- Gebruik alleen prioriteiten uit deze lijst: {", ".join(allowed_priorities)}.
- Gebruik bij voorkeur een duur uit deze lijst: {", ".join(allowed_durations) or "5 min, 10 min, 15 min, 30 min, 1 uur"}.
- "week" moet alleen een getal zijn zoals 1, 2, 3 of 4. Schrijf nooit "Week 1".
- "plant_options" moet 1 tot 5 mogelijke plantnamen bevatten.
- Gebruik waar mogelijk plantnamen uit deze bestaande plantenlijst: {", ".join(known_plants[:120])}.
- "identified_plant" moet de beste keuze zijn uit "plant_options".
- Als een plantnaam al door de gebruiker is ingevuld en er geen foto is, neem die plantnaam normaal gesproken over als "identified_plant", tenzij de profielinformatie daar duidelijk niet bij past.
- "summary" moet maximaal 2 of 3 korte zinnen zijn.
- "year_round_maintenance" moet uit 3 tot 6 korte bullets bestaan, geen lange alinea's, en alleen de belangrijkste aandachtspunten noemen.
- Noem in "year_round_maintenance" expliciet seizoensgebonden zorg zoals snoeien, water geven, bemesten, standplaats, winterbescherming, ziekten/plagen en bijzonderheden voor deze plant als die relevant zijn.
- De concrete "tasks" moeten het echte jaarplan vormen. Zet daarin de onderhoudsmomenten verspreid over het jaar, niet alleen wat nu in "{current_month}" speelt.
- Voeg voor alle relevante onderhoudsperiodes taken toe als die normaal gezien belangrijk zijn voor deze plant. Denk aan bloei, nazorg, delen/verplanten, bemesten, water geven, controle, snoeien, beschermen en planten van bollen/knollen als dat van toepassing is.
- Zorg dat de combinatie van "tasks" samen een bruikbaar jaarplan oplevert. Voor veel tuinplanten zullen dat vaak 4 tot 12 taken zijn, maar gebruik minder of meer als de plant dat echt vraagt.
- Voeg geen opvultaak toe alleen om een aantal te halen.
- Iedere taak moet concreet, uitvoerbaar en los opslaanbaar zijn.
- Vermijd dubbele taken die hetzelfde moment en dezelfde handeling beschrijven.
- Gebruik de huidige foto en de huidige situatie als extra context als er een foto is, maar laat belangrijke onderhoudsmomenten later in het jaar niet weg.
- Als er geen foto is, zeg dan niets over zichtbare kenmerken of huidige beeldwaarnemingen.
- Geef alleen JSON terug volgens het schema.

Plantprofiel van eventueel gekozen plant:
{chr(10).join(plant_lines) if plant_lines else "- Geen extra profielinformatie beschikbaar"}

Bestaande voorbeeldtaken:
{chr(10).join(examples) if examples else "- Nog geen bestaande taken voor deze plant"}
""".strip()

    contents: list[object] = [prompt]
    if has_image:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    response_text = _generate_with_fallback(
        client,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": ANALYSIS_SCHEMA,
            "temperature": 0.3,
        },
    )
    return json.loads(response_text)
