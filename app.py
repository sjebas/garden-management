from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from math import ceil
from io import BytesIO

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from data_store import create_store, generate_task_id
from garden_map_assets import GardenMapAssetStore
from gemini_helper import GeminiError, GeminiQuotaError, analyze_plant_image
from garden_data import MONTHS, MONTH_INDEX, PRIORITY_ORDER, STATUS_ORDER, GardenWorkbook


BASE_DIR = Path(__file__).resolve().parent
SEED_WORKBOOK_PATH = BASE_DIR / "professioneel_tuinbeheer_snoeiplan_verrijkt.xlsx"
LOCAL_DATA_PATH = Path(
    os.getenv("GARDEN_FILE_STORE_PATH", str(BASE_DIR / "instance" / "garden-data.json"))
)
LOCAL_MAP_ASSET_PATH = BASE_DIR / "instance" / "garden-map-assets"
TASK_FIELDS = [
    "ID",
    "Plant",
    "Maand",
    "Week",
    "Categorie",
    "Actie",
    "Prioriteit",
    "Status",
    "Duur",
    "Opmerking",
    "DashboardVolgorde",
]
PLANT_FIELDS = ["Plant", "Type", "Snoeigroep", "Standplaats", "Winterhard", "Notitie"]


def _current_month_name() -> str:
    return MONTHS[datetime.now().month - 1]


def _current_week_of_month() -> str:
    return str(min(5, ceil(datetime.now().day / 7)))


def _backend_name() -> str:
    configured = os.getenv("GARDEN_DATA_BACKEND", "").strip().lower()
    if configured:
        return configured
    return "firestore" if os.getenv("K_SERVICE") else "file"


STORE = create_store(
    backend=_backend_name(),
    file_path=LOCAL_DATA_PATH,
    project_id=os.getenv("FIRESTORE_PROJECT_ID", "").strip() or None,
    prefix=os.getenv("FIRESTORE_COLLECTION_PREFIX", "garden"),
)
MAP_ASSET_STORE = GardenMapAssetStore(
    local_dir=LOCAL_MAP_ASSET_PATH,
    bucket_name=os.getenv("GARDEN_MAP_BUCKET", "").strip() or None,
)


def _seed_store() -> None:
    if not SEED_WORKBOOK_PATH.exists():
        return
    workbook_data = GardenWorkbook(SEED_WORKBOOK_PATH).load()
    STORE.ensure_seeded(workbook_data["plants"], workbook_data["tasks"])


def _task_form_values(form) -> dict[str, str]:
    return {field: form.get(field, "").strip() for field in TASK_FIELDS}


def _plant_form_values(form) -> dict[str, str]:
    return {field: form.get(field, "").strip() for field in PLANT_FIELDS}


def _as_percentage(value: object) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 0 <= parsed <= 100:
        return round(parsed, 2)
    return None


def _plants_with_stats() -> list[dict[str, object]]:
    tasks = STORE.list_tasks()
    plants = []
    for plant in STORE.list_plants():
        related_tasks = [task for task in tasks if task["PlantId"] == plant["id"]]
        open_tasks = [task for task in related_tasks if task["Status"] != "Gereed"]
        highest_priority = min(
            (PRIORITY_ORDER.get(task["Prioriteit"], 99) for task in open_tasks),
            default=99,
        )
        pin_variant = "done"
        if open_tasks:
            pin_variant = "low"
            if highest_priority == PRIORITY_ORDER.get("Hoog", 0):
                pin_variant = "high"
            elif highest_priority == PRIORITY_ORDER.get("Middel", 1):
                pin_variant = "medium"
        plants.append(
            {
                **plant,
                "Taken": len(related_tasks),
                "OpenTaken": sum(task["Status"] != "Gereed" for task in related_tasks),
                "MapPlaced": _as_percentage(plant.get("MapX")) is not None and _as_percentage(plant.get("MapY")) is not None,
                "MapXValue": _as_percentage(plant.get("MapX")),
                "MapYValue": _as_percentage(plant.get("MapY")),
                "PinVariant": pin_variant,
            }
        )
    return sorted(plants, key=lambda plant: str(plant["Plant"]))


def _load_reference_data() -> dict[str, list[str]]:
    plants = STORE.list_plants()
    tasks = STORE.list_tasks()
    return {
        "plants": [plant["Plant"] for plant in sorted(plants, key=lambda item: item["Plant"])],
        "plant_types": sorted({plant["Type"] for plant in plants if plant["Type"]}),
        "hardiness_options": sorted({plant["Winterhard"] for plant in plants if plant["Winterhard"]}),
        "categories": sorted({task["Categorie"] for task in tasks if task["Categorie"]}),
        "durations": sorted({task["Duur"] for task in tasks if task["Duur"]}),
        "priorities": ["Hoog", "Middel", "Laag"],
        "statuses": ["Open", "Uitgesteld", "Gereed"],
        "months": MONTHS[:],
    }


def _monthly_summary(tasks: list[dict[str, str]]) -> list[dict[str, object]]:
    by_month = {month: [] for month in MONTHS}
    for task in tasks:
        by_month.setdefault(task["Maand"], []).append(task)

    summary = []
    for month in MONTHS:
        month_tasks = by_month.get(month, [])
        categories = Counter(task["Categorie"] for task in month_tasks if task["Categorie"])
        summary.append(
            {
                "month": month,
                "total": len(month_tasks),
                "open": sum(task["Status"] != "Gereed" for task in month_tasks),
                "done": sum(task["Status"] == "Gereed" for task in month_tasks),
                "high_priority": sum(task["Prioriteit"] == "Hoog" for task in month_tasks),
                "categories": categories,
            }
        )
    return summary


def _estimate_minutes(duration: str) -> int:
    text = str(duration or "").strip().lower()
    if not text:
        return 0
    values = [int(match) for match in re.findall(r"\d+", text)]
    if not values:
        return 0
    number = sum(values) // len(values)
    if "uur" in text or "hour" in text:
        return number * 60
    return number


def _format_minutes(total_minutes: int) -> str:
    if total_minutes <= 0:
        return "Nog niet ingeschat"
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} u {minutes} min"
    if hours:
        return f"{hours} uur"
    return f"{minutes} min"


def _yearly_heatmap(plants: list[dict[str, object]], tasks: list[dict[str, str]]) -> list[dict[str, object]]:
    rows = []
    for plant in plants:
        counts = {month: 0 for month in MONTHS}
        for task in tasks:
            if task["PlantId"] == plant["id"]:
                counts[task["Maand"]] += 1
        rows.append({"plant": plant["Plant"], "months": counts, "total": plant["Taken"]})
    return rows


def _plant_workload(plants: list[dict[str, object]], tasks: list[dict[str, str]]) -> list[dict[str, object]]:
    rows = []
    for plant in plants:
        plant_tasks = [task for task in tasks if task["PlantId"] == plant["id"]]
        open_tasks = [task for task in plant_tasks if task["Status"] != "Gereed"]
        rows.append(
            {
                "plant": plant["Plant"],
                "total": len(plant_tasks),
                "open": len(open_tasks),
                "high": sum(task["Prioriteit"] == "Hoog" for task in plant_tasks),
                "next_month": min((MONTH_INDEX.get(task["Maand"], 99) for task in open_tasks), default=99),
            }
        )
    return sorted(rows, key=lambda item: (-item["open"], -item["high"], item["next_month"], item["plant"]))


def _next_up(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    open_tasks = [task for task in tasks if task["Status"] != "Gereed"]
    return sorted(
        open_tasks,
        key=lambda task: (
            MONTH_INDEX.get(task["Maand"], 99),
            PRIORITY_ORDER.get(task["Prioriteit"], 99),
            int(task["Week"]) if str(task["Week"]).isdigit() else 99,
            task["Plant"],
        ),
    )[:8]


def _task_display_sort_key(task: dict[str, str]) -> tuple[int, int, int, int, str]:
    return (
        1 if task.get("Status") == "Gereed" else 0,
        MONTH_INDEX.get(task.get("Maand", ""), 99),
        PRIORITY_ORDER.get(task.get("Prioriteit", ""), 99),
        int(task["Week"]) if str(task.get("Week", "")).isdigit() else 99,
        task.get("Plant", ""),
    )


def _normalize_week(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d+)", raw)
    return match.group(1) if match else raw


def _coerce_option(value: object, allowed: list[str], fallback: str = "") -> str:
    candidate = str(value or "").strip()
    if candidate in allowed:
        return candidate
    lowered = {item.lower(): item for item in allowed}
    return lowered.get(candidate.lower(), fallback or (allowed[0] if allowed else candidate))


def _proposal_form_values(form, index: int) -> dict[str, str]:
    selected_plant = form.get("selected_plant_name", "").strip()
    custom_plant = form.get("custom_plant_name", "").strip()
    resolved_plant = custom_plant or selected_plant or form.get(f"proposal-{index}-Plant", "").strip()
    return {
        "Plant": resolved_plant,
        "Maand": form.get(f"proposal-{index}-Maand", "").strip(),
        "Week": _normalize_week(form.get(f"proposal-{index}-Week", "")),
        "Categorie": form.get(f"proposal-{index}-Categorie", "").strip(),
        "Actie": form.get(f"proposal-{index}-Actie", "").strip(),
        "Prioriteit": form.get(f"proposal-{index}-Prioriteit", "").strip(),
        "Status": "Open",
        "Duur": form.get(f"proposal-{index}-Duur", "").strip(),
        "Opmerking": form.get(f"proposal-{index}-Opmerking", "").strip(),
        "DashboardVolgorde": "",
    }


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
    _seed_store()

    @app.context_processor
    def inject_globals() -> dict[str, object]:
        return {"month_index": MONTH_INDEX, "current_year": datetime.now().year}

    @app.get("/healthz")
    def healthcheck():
        return {"status": "ok", "backend": _backend_name()}, 200

    @app.route("/")
    def dashboard():
        plants = _plants_with_stats()
        tasks = STORE.list_tasks()
        selected_month = request.args.get("month") or _current_month_name()
        if selected_month not in MONTHS:
            selected_month = _current_month_name()
        selected_week = request.args.get("week") or _current_week_of_month()
        if selected_week not in {"1", "2", "3", "4", "5"}:
            selected_week = _current_week_of_month()
        show_done = request.args.get("show_done", "").strip() == "1"

        week_tasks = [task for task in tasks if task["Maand"] == selected_month and str(task["Week"]) == selected_week]
        visible_week_tasks = week_tasks if show_done else [task for task in week_tasks if task["Status"] != "Gereed"]
        week_tasks = sorted(
            visible_week_tasks,
            key=_task_display_sort_key,
        )
        open_week_tasks = [task for task in week_tasks if task["Status"] != "Gereed"]
        week_minutes = sum(_estimate_minutes(task.get("Duur", "")) for task in open_week_tasks)

        workload = []
        for item in _plant_workload(plants, tasks):
            matching = [task for task in week_tasks if task["Plant"] == item["plant"]]
            if matching:
                workload.append(
                    {
                        "plant": item["plant"],
                        "tasks": len(matching),
                        "open": sum(task["Status"] != "Gereed" for task in matching),
                        "high": sum(task["Prioriteit"] == "Hoog" for task in matching),
                    }
                )

        summary = _monthly_summary(tasks)
        month_summary = next(entry for entry in summary if entry["month"] == selected_month)

        return render_template(
            "dashboard.html",
            page_title="Dashboard",
            selected_month=selected_month,
            selected_week=selected_week,
            show_done=show_done,
            months=MONTHS,
            weeks=["1", "2", "3", "4", "5"],
            tasks=week_tasks,
            open_week_tasks=len(open_week_tasks),
            week_time_budget=_format_minutes(week_minutes),
            workload=sorted(workload, key=lambda item: (-item["open"], -item["high"], item["plant"])),
            month_summary=month_summary,
            next_up=_next_up(tasks),
            total_plants=len(plants),
        )

    @app.post("/assistant/propose")
    def propose_tasks():
        plants = _plants_with_stats()
        reference = _load_reference_data()
        plant_name = request.form.get("plant_name", "").strip()
        image = request.files.get("plant_photo")
        has_image = bool(image is not None and image.filename)
        image_bytes = None
        mime_type = None

        if not plant_name and not has_image:
            flash("Vul een plantnaam in of upload een foto.", "error")
            return redirect(url_for("plants"))

        if has_image:
            mime_type = image.mimetype or mimetypes.guess_type(image.filename)[0] or ""
            if not mime_type.startswith("image/"):
                flash("Alleen afbeeldingsbestanden zijn toegestaan.", "error")
                return redirect(url_for("plants"))

            image_bytes = image.read()
            if not image_bytes:
                flash("De geuploade foto is leeg.", "error")
                return redirect(url_for("plants"))

        all_tasks = STORE.list_tasks()
        plant_profile = next((plant for plant in plants if plant["Plant"] == plant_name), None)
        existing_tasks = [task for task in all_tasks if task["Plant"] == plant_name]

        try:
            analysis_result = analyze_plant_image(
                selected_plant_name=plant_name,
                image_bytes=image_bytes,
                mime_type=mime_type,
                current_month=_current_month_name(),
                plant_profile=plant_profile,
                existing_tasks=existing_tasks,
                known_plants=reference["plants"],
                allowed_months=reference["months"],
                allowed_categories=reference["categories"],
                allowed_priorities=reference["priorities"],
                allowed_durations=reference["durations"],
            )
        except GeminiQuotaError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plants"))
        except GeminiError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plants"))
        except Exception:
            flash("Er ging iets mis bij het maken van een voorstel. Probeer het zo nog eens.", "error")
            return redirect(url_for("plants"))

        selected_month = request.args.get("month") or _current_month_name()
        if selected_month not in MONTHS:
            selected_month = _current_month_name()
        selected_week = request.args.get("week") or _current_week_of_month()
        if selected_week not in {"1", "2", "3", "4", "5"}:
            selected_week = _current_week_of_month()
        week_tasks = [task for task in all_tasks if task["Maand"] == selected_month and str(task["Week"]) == selected_week]
        summary = _monthly_summary(all_tasks)
        month_summary = next(entry for entry in summary if entry["month"] == selected_month)
        workload = []
        for item in _plant_workload(plants, all_tasks):
            matching = [task for task in week_tasks if task["Plant"] == item["plant"]]
            if matching:
                workload.append(
                    {
                        "plant": item["plant"],
                        "tasks": len(matching),
                        "open": sum(task["Status"] != "Gereed" for task in matching),
                        "high": sum(task["Prioriteit"] == "Hoog" for task in matching),
                    }
                )

        resolved_plant_name = (
            plant_name
            or str(analysis_result.get("identified_plant", "")).strip()
            or "Onbekende plant"
        )
        proposal_tasks = []
        for item in analysis_result.get("tasks", []):
            proposal_tasks.append(
                {
                    "Plant": resolved_plant_name,
                    "Maand": _coerce_option(item.get("month", ""), reference["months"], _current_month_name()),
                    "Week": _normalize_week(item.get("week", "")),
                    "Categorie": _coerce_option(item.get("category", ""), reference["categories"], "Onderhoud"),
                    "Actie": str(item.get("action", "")).strip(),
                    "Prioriteit": _coerce_option(item.get("priority", ""), reference["priorities"], "Middel"),
                    "Duur": _coerce_option(item.get("duration", ""), reference["durations"], "15 min"),
                    "Opmerking": str(item.get("note", "")).strip(),
                    "Confidence": item.get("confidence", ""),
                    "Reason": str(item.get("reason", "")).strip(),
                }
            )
        proposal_tasks = sorted(
            proposal_tasks,
            key=lambda task: (
                MONTH_INDEX.get(task["Maand"], 99),
                int(task["Week"]) if str(task["Week"]).isdigit() else 99,
                PRIORITY_ORDER.get(task["Prioriteit"], 99),
                task["Actie"],
            ),
        )

        year_round_maintenance = [
            str(item).strip()
            for item in analysis_result.get("year_round_maintenance", [])
            if str(item).strip()
        ]
        open_week_tasks = [task for task in week_tasks if task["Status"] != "Gereed"]
        week_minutes = sum(_estimate_minutes(task.get("Duur", "")) for task in open_week_tasks)

        plant_items = _plants_with_stats()
        return render_template(
            "plants.html",
            page_title="Planten",
            plants=sorted(plant_items, key=lambda plant: (-int(plant["OpenTaken"]), str(plant["Plant"]))),
            types=reference["plant_types"],
            hardiness_options=reference["hardiness_options"],
            filters={"q": "", "type": "", "winterhard": ""},
            new_plant={
                "Plant": "",
                "Type": "",
                "Snoeigroep": "",
                "Standplaats": "",
                "Winterhard": "",
                "Notitie": "",
            },
            plants_for_upload=[plant["Plant"] for plant in plant_items],
            proposal_result={
                "summary": analysis_result.get("summary", ""),
                "tasks": proposal_tasks,
                "plant_name": resolved_plant_name,
                "manual_plant": plant_name,
                "plant_options": [
                    option
                    for option in [resolved_plant_name, *analysis_result.get("plant_options", [])]
                    if option
                ],
                "identification_confidence": analysis_result.get("identification_confidence", ""),
                "identification_reason": analysis_result.get("identification_reason", ""),
                "year_round_maintenance": year_round_maintenance,
                "all_plant_options": sorted(
                    {
                        option
                        for option in [*reference["plants"], resolved_plant_name, *analysis_result.get("plant_options", [])]
                        if str(option).strip()
                    }
                ),
            },
            proposal_reference=reference,
        )

    @app.post("/assistant/accept")
    def accept_proposals():
        count = int(request.form.get("proposal-count", "0") or "0")
        created = 0
        existing_ids = [task["ID"] for task in STORE.list_tasks()]
        for index in range(count):
            if request.form.get(f"proposal-{index}-selected") != "1":
                continue
            values = _proposal_form_values(request.form, index)
            if not values["Plant"] or not values["Maand"] or not values["Actie"]:
                continue
            values["ID"] = generate_task_id(existing_ids, values["Plant"])
            existing_ids.append(values["ID"])
            try:
                STORE.create_task(values)
            except ValueError:
                continue
            created += 1

        if created:
            flash(f"{created} voorgestelde taken toegevoegd aan de database.", "success")
        else:
            flash("Geen taken toegevoegd. Selecteer minstens een geldig voorstel.", "error")
        return redirect(url_for("plants"))

    @app.route("/tasks")
    def tasks():
        reference = _load_reference_data()
        items = STORE.list_tasks()

        selected_month = request.args.get("month", "").strip()
        selected_status = request.args.get("status", "").strip()
        selected_priority = request.args.get("priority", "").strip()
        selected_plant = request.args.get("plant", "").strip()
        text_query = request.args.get("q", "").strip().lower()
        show_done = request.args.get("show_done", "").strip() == "1"

        if selected_month:
            items = [task for task in items if task["Maand"] == selected_month]
        if selected_status:
            items = [task for task in items if task["Status"] == selected_status]
        if selected_priority:
            items = [task for task in items if task["Prioriteit"] == selected_priority]
        if selected_plant:
            items = [task for task in items if task["Plant"] == selected_plant]
        if text_query:
            items = [
                task
                for task in items
                if text_query in " ".join(
                    [task["ID"], task["Plant"], task["Categorie"], task["Actie"], task["Opmerking"]]
                ).lower()
            ]
        if not show_done:
            items = [task for task in items if task["Status"] != "Gereed"]

        items = sorted(items, key=_task_display_sort_key)

        return render_template(
            "tasks.html",
            page_title="Taken",
            tasks=items,
            months=reference["months"],
            categories=reference["categories"],
            statuses=reference["statuses"],
            priorities=reference["priorities"],
            durations=reference["durations"],
            plants=reference["plants"],
            filters={
                "month": selected_month,
                "status": selected_status,
                "priority": selected_priority,
                "plant": selected_plant,
                "q": request.args.get("q", "").strip(),
                "show_done": show_done,
            },
            new_task={
                "Plant": selected_plant,
                "Maand": selected_month,
                "Week": "",
                "Categorie": "",
                "Actie": "",
                "Prioriteit": "",
                "Status": "Open",
                "Duur": "",
                "Opmerking": "",
            },
        )

    @app.post("/tasks/create")
    def create_task():
        values = _task_form_values(request.form)
        if not values["Plant"] or not values["Maand"] or not values["Actie"]:
            flash("Plant, maand en actie zijn verplicht voor een nieuwe taak.", "error")
            return redirect(url_for("tasks"))

        existing_ids = [task["ID"] for task in STORE.list_tasks()]
        values["ID"] = values["ID"] or generate_task_id(existing_ids, values["Plant"])

        try:
            task = STORE.create_task(values)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("tasks"))

        flash(f"Taak {task['ID']} is opgeslagen.", "success")
        return redirect(url_for("task_detail", task_id=task["ID"]))

    @app.route("/plants")
    def plants():
        reference = _load_reference_data()
        items = _plants_with_stats()
        text_query = request.args.get("q", "").strip().lower()
        selected_type = request.args.get("type", "").strip()
        selected_hardiness = request.args.get("winterhard", "").strip()

        if text_query:
            items = [
                plant
                for plant in items
                if text_query in " ".join(
                    [
                        str(plant["Plant"]),
                        str(plant["Type"]),
                        str(plant["Snoeigroep"]),
                        str(plant["Standplaats"]),
                        str(plant["Notitie"]),
                    ]
                ).lower()
            ]
        if selected_type:
            items = [plant for plant in items if plant["Type"] == selected_type]
        if selected_hardiness:
            items = [plant for plant in items if plant["Winterhard"] == selected_hardiness]

        items = sorted(items, key=lambda plant: (-int(plant["OpenTaken"]), str(plant["Plant"])))

        return render_template(
            "plants.html",
            page_title="Planten",
            plants=items,
            types=reference["plant_types"],
            hardiness_options=reference["hardiness_options"],
            filters={"q": request.args.get("q", "").strip(), "type": selected_type, "winterhard": selected_hardiness},
            new_plant={
                "Plant": "",
                "Type": "",
                "Snoeigroep": "",
                "Standplaats": "",
                "Winterhard": "",
                "Notitie": "",
            },
            plants_for_upload=[plant["Plant"] for plant in items],
            proposal_result=None,
            proposal_reference=reference,
        )

    @app.route("/map")
    def garden_map():
        plants = _plants_with_stats()
        garden_map_settings = STORE.get_garden_map()
        placed_plants = [plant for plant in plants if plant["MapPlaced"]]
        unplaced_plants = [plant for plant in plants if not plant["MapPlaced"]]

        return render_template(
            "map.html",
            page_title="Kaart",
            garden_map=garden_map_settings,
            placed_plants=sorted(placed_plants, key=lambda plant: (-int(plant["OpenTaken"]), str(plant["Plant"]))),
            unplaced_plants=sorted(unplaced_plants, key=lambda plant: str(plant["Plant"])),
            plants=plants,
        )

    @app.post("/map/background")
    def upload_garden_map_background():
        image = request.files.get("background_image")
        if image is None or not image.filename:
            flash("Kies eerst een afbeelding van je tuin.", "error")
            return redirect(url_for("garden_map"))
        if os.getenv("K_SERVICE") and not os.getenv("GARDEN_MAP_BUCKET", "").strip():
            flash("Voor de tuinkaart in de cloud is eerst een opslagbucket nodig.", "error")
            return redirect(url_for("garden_map"))

        mime_type = image.mimetype or mimetypes.guess_type(image.filename)[0] or ""
        if not mime_type.startswith("image/"):
            flash("Gebruik een afbeeldingsbestand voor de kaartachtergrond.", "error")
            return redirect(url_for("garden_map"))

        image_bytes = image.read()
        if not image_bytes:
            flash("De gekozen kaartafbeelding is leeg.", "error")
            return redirect(url_for("garden_map"))

        try:
            saved = MAP_ASSET_STORE.save_background(
                filename=secure_filename(image.filename),
                data=image_bytes,
                mime_type=mime_type,
            )
            STORE.save_garden_map(
                {
                    "BackgroundPath": saved.path,
                    "BackgroundMimeType": saved.mime_type,
                    "UpdatedAt": datetime.utcnow().isoformat(),
                }
            )
        except RuntimeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("garden_map"))

        flash("De tuinfoto staat klaar als achtergrond van je kaart.", "success")
        return redirect(url_for("garden_map"))

    @app.get("/map/background")
    def garden_map_background():
        settings = STORE.get_garden_map()
        if not settings.get("BackgroundPath"):
            abort(404)
        try:
            data, mime_type = MAP_ASSET_STORE.load_background(settings["BackgroundPath"])
        except FileNotFoundError:
            abort(404)
        return send_file(BytesIO(data), mimetype=settings.get("BackgroundMimeType") or mime_type, max_age=300)

    @app.post("/map/place")
    def place_plant_on_map():
        plant_name = request.form.get("plant_name", "").strip()
        x = _as_percentage(request.form.get("map_x", ""))
        y = _as_percentage(request.form.get("map_y", ""))

        if not plant_name:
            flash("Kies eerst welke plant je wilt plaatsen.", "error")
            return redirect(url_for("garden_map"))
        if x is None or y is None:
            flash("Klik op de kaart om een geldige plek te kiezen.", "error")
            return redirect(url_for("garden_map"))

        try:
            STORE.update_plant_location(plant_name, f"{x:.2f}", f"{y:.2f}")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("garden_map"))

        flash(f"{plant_name} staat nu op je tuinkaart.", "success")
        return redirect(url_for("garden_map"))

    @app.post("/plants/create")
    def create_plant():
        values = _plant_form_values(request.form)
        if not values["Plant"]:
            flash("Plantnaam is verplicht.", "error")
            return redirect(url_for("plants"))

        try:
            plant = STORE.create_plant(values)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plants"))

        flash(f"{plant['Plant']} is toegevoegd.", "success")
        return redirect(url_for("plant_detail", plant_name=plant["Plant"]))

    @app.route("/calendar")
    def calendar():
        plants = _plants_with_stats()
        tasks = STORE.list_tasks()
        months = []
        for summary in _monthly_summary(tasks):
            month_tasks = [task for task in tasks if task["Maand"] == summary["month"]]
            weeks = defaultdict(list)
            for task in month_tasks:
                week_label = f"Week {task['Week']}" if task["Week"] else "Geen week"
                weeks[week_label].append(task)
            months.append({"summary": summary, "weeks": sorted(weeks.items())})

        return render_template(
            "calendar.html",
            page_title="Jaarplanner",
            months=months,
            yearly_heatmap=_yearly_heatmap(plants, tasks),
        )

    @app.route("/plant/<path:plant_name>")
    def plant_detail(plant_name: str):
        plant = next((item for item in _plants_with_stats() if item["Plant"] == plant_name), None)
        if plant is None:
            abort(404)

        tasks = [task for task in STORE.list_tasks() if task["PlantId"] == plant["id"]]
        tasks = sorted(tasks, key=_task_display_sort_key)
        heatmap = next(item for item in _yearly_heatmap([plant], tasks) if item["plant"] == plant["Plant"])

        return render_template(
            "plant_detail.html",
            page_title=plant["Plant"],
            plant=plant,
            tasks=tasks,
            heatmap=heatmap,
        )

    @app.post("/plant/<path:plant_name>/save")
    def save_plant(plant_name: str):
        values = _plant_form_values(request.form)
        if not values["Plant"]:
            flash("Plantnaam is verplicht.", "error")
            return redirect(url_for("plant_detail", plant_name=plant_name))

        try:
            plant = STORE.update_plant(plant_name, values)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plant_detail", plant_name=plant_name))

        flash(f"{plant['Plant']} is bijgewerkt.", "success")
        return redirect(url_for("plant_detail", plant_name=plant["Plant"]))

    @app.post("/plant/<path:plant_name>/delete")
    def delete_plant(plant_name: str):
        try:
            plant, removed_tasks = STORE.delete_plant(plant_name)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plants"))

        message = f"{plant['Plant']} is verwijderd."
        if removed_tasks:
            message += f" {removed_tasks} gekoppelde taken zijn ook verwijderd."
        flash(message, "success")
        return redirect(url_for("plants"))

    @app.post("/task/<task_id>/status")
    def update_task_status(task_id: str):
        next_url = request.form.get("next", "").strip() or request.referrer or url_for("tasks")
        status = request.form.get("status", "").strip()
        if status not in {"Open", "Uitgesteld", "Gereed"}:
            flash("Onbekende taakstatus.", "error")
            return redirect(next_url)

        try:
            task = STORE.update_task_status(task_id, status)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(next_url)

        if status == "Gereed":
            flash(f"{task['Plant']} is afgevinkt.", "success")
        else:
            flash(f"{task['Plant']} staat weer op {status.lower()}.", "success")
        return redirect(next_url)

    @app.route("/task/<task_id>", methods=["GET", "POST"])
    def task_detail(task_id: str):
        task = STORE.get_task(task_id)
        if task is None:
            abort(404)

        if request.method == "POST":
            values = _task_form_values(request.form)
            if not values["Plant"] or not values["Maand"] or not values["Actie"]:
                flash("Plant, maand en actie zijn verplicht.", "error")
                return redirect(url_for("task_detail", task_id=task_id))

            try:
                task = STORE.update_task(task_id, values)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("task_detail", task_id=task_id))

            flash(f"Taak {task['ID']} is bijgewerkt.", "success")
            return redirect(url_for("task_detail", task_id=task["ID"]))

        reference = _load_reference_data()
        return render_template(
            "task_detail.html",
            page_title=f"Taak {task['ID']}",
            task=task,
            months=reference["months"],
            categories=reference["categories"],
            priorities=reference["priorities"],
            statuses=reference["statuses"],
            durations=reference["durations"],
            plants=reference["plants"],
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
