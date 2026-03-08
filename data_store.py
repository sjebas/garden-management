from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
import json
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

try:
    from google.cloud import firestore
except ImportError:  # pragma: no cover
    firestore = None


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_locations(values: object, fallback_x: object = "", fallback_y: object = "") -> list[dict[str, str]]:
    raw_locations = values if isinstance(values, list) else []
    normalized = []
    for item in raw_locations:
        if not isinstance(item, dict):
            continue
        x = _clean(item.get("x", ""))
        y = _clean(item.get("y", ""))
        label = _clean(item.get("label", ""))
        if x and y:
            normalized.append({"id": _clean(item.get("id", uuid4().hex)) or uuid4().hex, "x": x, "y": y, "label": label})

    legacy_x = _clean(fallback_x)
    legacy_y = _clean(fallback_y)
    if not normalized and legacy_x and legacy_y:
        normalized.append({"id": uuid4().hex, "x": legacy_x, "y": legacy_y, "label": ""})
    return normalized


def _normalize_aliases(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_clean(item) for item in values if _clean(item)]


def _normalize_task_templates(values: object) -> list[dict[str, str]]:
    templates = []
    if not isinstance(values, list):
        return templates
    for item in values:
        if not isinstance(item, dict):
            continue
        template = {
            "month": _clean(item.get("month", "")),
            "week": _clean(item.get("week", "")),
            "category": _clean(item.get("category", "")),
            "action": _clean(item.get("action", "")),
            "priority": _clean(item.get("priority", "")),
            "duration": _clean(item.get("duration", "")),
            "note": _clean(item.get("note", "")),
        }
        if template["month"] and template["action"]:
            templates.append(template)
    return templates


def _normalize_library_key(value: object) -> str:
    text = _clean(value).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _task_sort_key(task: dict[str, str]) -> tuple[int, int, int, str]:
    month_order = {
        "Januari": 0,
        "Februari": 1,
        "Maart": 2,
        "April": 3,
        "Mei": 4,
        "Juni": 5,
        "Juli": 6,
        "Augustus": 7,
        "September": 8,
        "Oktober": 9,
        "November": 10,
        "December": 11,
    }
    priority_order = {"Hoog": 0, "Middel": 1, "Laag": 2}
    week = int(task["Week"]) if str(task.get("Week", "")).isdigit() else 99
    return (
        month_order.get(task.get("Maand", ""), 99),
        priority_order.get(task.get("Prioriteit", ""), 99),
        week,
        task.get("Plant", ""),
    )


def _default_plant_record(values: dict[str, str], plant_id: str | None = None) -> dict[str, str]:
    locations = _normalize_locations(values.get("MapLocations"), values.get("MapX", ""), values.get("MapY", ""))
    return {
        "id": plant_id or uuid4().hex,
        "Plant": values.get("Plant", ""),
        "Type": values.get("Type", ""),
        "Snoeigroep": values.get("Snoeigroep", ""),
        "Standplaats": values.get("Standplaats", ""),
        "Winterhard": values.get("Winterhard", ""),
        "Notitie": values.get("Notitie", ""),
        "LibraryPlantId": _clean(values.get("LibraryPlantId", "")),
        "MapX": locations[0]["x"] if locations else _clean(values.get("MapX", "")),
        "MapY": locations[0]["y"] if locations else _clean(values.get("MapY", "")),
        "MapLocations": locations,
    }


def _default_task_record(values: dict[str, str], plant_id: str) -> dict[str, str]:
    return {
        "ID": values.get("ID", ""),
        "PlantId": plant_id,
        "Plant": values.get("Plant", ""),
        "Maand": values.get("Maand", ""),
        "Week": values.get("Week", ""),
        "Categorie": values.get("Categorie", ""),
        "Actie": values.get("Actie", ""),
        "Prioriteit": values.get("Prioriteit", "Middel") or "Middel",
        "Status": values.get("Status", "Open") or "Open",
        "Duur": values.get("Duur", ""),
        "Opmerking": values.get("Opmerking", ""),
        "DashboardVolgorde": values.get("DashboardVolgorde", ""),
    }


def _default_library_plant_record(values: dict[str, object], library_id: str | None = None) -> dict[str, object]:
    return {
        "id": _clean(library_id or values.get("id", "") or uuid4().hex),
        "CanonicalName": _clean(values.get("CanonicalName", values.get("canonical_name", ""))),
        "Aliases": _normalize_aliases(values.get("Aliases", values.get("aliases", []))),
        "Type": _clean(values.get("Type", values.get("type", ""))),
        "Summary": _clean(values.get("Summary", values.get("summary", ""))),
        "YearRoundMaintenance": _normalize_aliases(values.get("YearRoundMaintenance", values.get("year_round_maintenance", []))),
        "TaskTemplates": _normalize_task_templates(values.get("TaskTemplates", values.get("task_templates", []))),
        "ImageUrl": _clean(values.get("ImageUrl", values.get("image_url", ""))),
        "ImageSourceUrl": _clean(values.get("ImageSourceUrl", values.get("image_source_url", ""))),
        "ImageCredit": _clean(values.get("ImageCredit", values.get("image_credit", ""))),
        "ReviewStatus": _clean(values.get("ReviewStatus", values.get("review_status", "reviewed")) or "reviewed"),
        "SourceNotes": _clean(values.get("SourceNotes", values.get("source_notes", ""))),
    }


class BaseStore(ABC):
    @abstractmethod
    def ensure_seeded(
        self, plants: list[dict[str, str]], tasks: list[dict[str, str]]
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_plants(self) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def list_tasks(self) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def get_plant_by_name(self, name: str) -> dict[str, str] | None:
        raise NotImplementedError

    @abstractmethod
    def get_task(self, task_id: str) -> dict[str, str] | None:
        raise NotImplementedError

    @abstractmethod
    def create_plant(self, values: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def update_plant(self, original_name: str, values: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def delete_plant(self, name: str) -> tuple[dict[str, str], int]:
        raise NotImplementedError

    @abstractmethod
    def ensure_plant(self, name: str) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def create_task(self, values: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def update_task(self, task_id: str, values: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def update_task_status(self, task_id: str, status: str) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def get_garden_map(self) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def save_garden_map(self, values: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def update_plant_location(self, name: str, x: str, y: str, label: str = "") -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def delete_plant_location(self, name: str, location_id: str) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def move_plant_location(self, name: str, location_id: str, x: str, y: str) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def ensure_library_seeded(self, plants: list[dict[str, object]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_library_plants(self) -> list[dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def get_library_plant(self, library_id: str) -> dict[str, object] | None:
        raise NotImplementedError

    @abstractmethod
    def find_library_plant_by_name(self, name: str) -> dict[str, object] | None:
        raise NotImplementedError

    @abstractmethod
    def update_plant_library_link(self, name: str, library_id: str) -> dict[str, str]:
        raise NotImplementedError


class FileStore(BaseStore):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def ensure_seeded(self, plants: list[dict[str, str]], tasks: list[dict[str, str]]) -> None:
        payload = self._read()
        if payload["plants"]:
            return

        seeded_plants = [_default_plant_record(item) for item in plants]
        plant_ids = {plant["Plant"]: plant["id"] for plant in seeded_plants}
        seeded_tasks = []
        for item in tasks:
            plant_id = plant_ids.get(item["Plant"])
            if plant_id is None:
                plant = _default_plant_record({"Plant": item["Plant"]})
                seeded_plants.append(plant)
                plant_ids[plant["Plant"]] = plant["id"]
                plant_id = plant["id"]
            seeded_tasks.append(_default_task_record(item, plant_id))

        payload = {
            "plants": seeded_plants,
            "tasks": seeded_tasks,
            "garden_map": _default_garden_map_record({}),
            "library_plants": [],
        }
        self._write(payload)

    def list_plants(self) -> list[dict[str, str]]:
        return self._read()["plants"]

    def list_tasks(self) -> list[dict[str, str]]:
        return self._read()["tasks"]

    def get_plant_by_name(self, name: str) -> dict[str, str] | None:
        return next((plant for plant in self.list_plants() if plant["Plant"] == name), None)

    def get_task(self, task_id: str) -> dict[str, str] | None:
        return next((task for task in self.list_tasks() if task["ID"] == task_id), None)

    def create_plant(self, values: dict[str, str]) -> dict[str, str]:
        payload = self._read()
        if any(plant["Plant"] == values["Plant"] for plant in payload["plants"]):
            raise ValueError(f"Plant bestaat al: {values['Plant']}")
        plant = _default_plant_record(values)
        payload["plants"].append(plant)
        self._write(payload)
        return plant

    def update_plant(self, original_name: str, values: dict[str, str]) -> dict[str, str]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == original_name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {original_name}")
        duplicate = next(
            (
                item
                for item in payload["plants"]
                if item["Plant"] == values["Plant"] and item["Plant"] != original_name
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(f"Plant bestaat al: {values['Plant']}")

        merged_values = {**plant, **values}
        plant.update(_default_plant_record(merged_values, plant["id"]))
        for task in payload["tasks"]:
            if task["PlantId"] == plant["id"]:
                task["Plant"] = plant["Plant"]
        self._write(payload)
        return plant

    def delete_plant(self, name: str) -> tuple[dict[str, str], int]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")

        remaining_plants = [item for item in payload["plants"] if item["id"] != plant["id"]]
        removed_tasks = [task for task in payload["tasks"] if task["PlantId"] == plant["id"]]
        remaining_tasks = [task for task in payload["tasks"] if task["PlantId"] != plant["id"]]
        self._write(
            {
                "plants": remaining_plants,
                "tasks": remaining_tasks,
                "garden_map": payload["garden_map"],
                "library_plants": payload["library_plants"],
            }
        )
        return plant, len(removed_tasks)

    def ensure_plant(self, name: str) -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is not None:
            return plant
        library_match = self.find_library_plant_by_name(name)
        values = {"Plant": name}
        if library_match:
            values["LibraryPlantId"] = str(library_match["id"])
        return self.create_plant(values)

    def create_task(self, values: dict[str, str]) -> dict[str, str]:
        payload = self._read()
        if any(task["ID"] == values["ID"] for task in payload["tasks"]):
            raise ValueError(f"Taak-ID bestaat al: {values['ID']}")
        plant = self.ensure_plant(values["Plant"])
        payload = self._read()
        task = _default_task_record(values, plant["id"])
        payload["tasks"].append(task)
        self._write(payload)
        return task

    def update_task(self, task_id: str, values: dict[str, str]) -> dict[str, str]:
        payload = self._read()
        task = next((item for item in payload["tasks"] if item["ID"] == task_id), None)
        if task is None:
            raise ValueError(f"Taak niet gevonden: {task_id}")
        plant = self.ensure_plant(values["Plant"])
        payload = self._read()
        task = next((item for item in payload["tasks"] if item["ID"] == task_id), None)
        task.update(_default_task_record(values, plant["id"]))
        self._write(payload)
        return task

    def update_task_status(self, task_id: str, status: str) -> dict[str, str]:
        payload = self._read()
        task = next((item for item in payload["tasks"] if item["ID"] == task_id), None)
        if task is None:
            raise ValueError(f"Taak niet gevonden: {task_id}")
        task["Status"] = status
        self._write(payload)
        return task

    def get_garden_map(self) -> dict[str, str]:
        return self._read()["garden_map"]

    def save_garden_map(self, values: dict[str, str]) -> dict[str, str]:
        payload = self._read()
        record = _default_garden_map_record({**payload["garden_map"], **values})
        payload["garden_map"] = record
        self._write(payload)
        return record

    def update_plant_location(self, name: str, x: str, y: str, label: str = "") -> dict[str, str]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        locations.append({"id": uuid4().hex, "x": _clean(x), "y": _clean(y), "label": _clean(label)})
        plant["MapLocations"] = locations
        plant["MapX"] = locations[0]["x"]
        plant["MapY"] = locations[0]["y"]
        self._write(payload)
        return plant

    def delete_plant_location(self, name: str, location_id: str) -> dict[str, str]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        remaining = [item for item in locations if item["id"] != location_id]
        if len(remaining) == len(locations):
            raise ValueError("Locatie niet gevonden.")
        plant["MapLocations"] = remaining
        plant["MapX"] = remaining[0]["x"] if remaining else ""
        plant["MapY"] = remaining[0]["y"] if remaining else ""
        self._write(payload)
        return plant

    def move_plant_location(self, name: str, location_id: str, x: str, y: str) -> dict[str, str]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        for location in locations:
            if location["id"] == location_id:
                location["x"] = _clean(x)
                location["y"] = _clean(y)
                plant["MapLocations"] = locations
                plant["MapX"] = locations[0]["x"] if locations else ""
                plant["MapY"] = locations[0]["y"] if locations else ""
                self._write(payload)
                return plant
        raise ValueError("Locatie niet gevonden.")

    def ensure_library_seeded(self, plants: list[dict[str, object]]) -> None:
        payload = self._read()
        existing = {item["id"]: item for item in payload["library_plants"]}
        merged = []
        for item in plants:
            default_item = _default_library_plant_record(item)
            merged.append(_default_library_plant_record({**existing.get(default_item["id"], {}), **default_item}, default_item["id"]))
        payload["library_plants"] = merged
        self._write(payload)

    def list_library_plants(self) -> list[dict[str, object]]:
        return self._read()["library_plants"]

    def get_library_plant(self, library_id: str) -> dict[str, object] | None:
        return next((item for item in self.list_library_plants() if item["id"] == library_id), None)

    def find_library_plant_by_name(self, name: str) -> dict[str, object] | None:
        normalized = _normalize_library_key(name)
        if not normalized:
            return None
        for item in self.list_library_plants():
            if _normalize_library_key(item["CanonicalName"]) == normalized:
                return item
            if normalized in {_normalize_library_key(alias) for alias in item.get("Aliases", [])}:
                return item
        return None

    def update_plant_library_link(self, name: str, library_id: str) -> dict[str, str]:
        payload = self._read()
        plant = next((item for item in payload["plants"] if item["Plant"] == name), None)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        if library_id and not any(item["id"] == library_id for item in payload["library_plants"]):
            raise ValueError("Bibliotheekplant niet gevonden.")
        plant["LibraryPlantId"] = _clean(library_id)
        self._write(payload)
        return plant

    def _read(self) -> dict[str, list[dict[str, str]]]:
        if not self.path.exists():
            return {"plants": [], "tasks": [], "garden_map": _default_garden_map_record({}), "library_plants": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("plants", [])
        payload.setdefault("tasks", [])
        payload.setdefault("garden_map", _default_garden_map_record({}))
        payload.setdefault("library_plants", [])
        for plant in payload["plants"]:
            plant["MapLocations"] = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
            plant.setdefault("MapX", "")
            plant.setdefault("MapY", "")
            plant.setdefault("LibraryPlantId", "")
        payload["library_plants"] = [_default_library_plant_record(item) for item in payload["library_plants"]]
        return payload

    def _write(self, payload: dict[str, object]) -> None:
        with self._lock:
            self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


class FirestoreStore(BaseStore):
    def __init__(self, project_id: str | None = None, collection_prefix: str = "garden") -> None:
        if firestore is None:
            raise RuntimeError("google-cloud-firestore is niet beschikbaar.")
        self.client = firestore.Client(project=project_id)
        self.prefix = collection_prefix.strip() or "garden"
        self.plants_collection = self.client.collection(f"{self.prefix}_plants")
        self.tasks_collection = self.client.collection(f"{self.prefix}_tasks")
        self.library_collection = self.client.collection(f"{self.prefix}_library_plants")
        self.settings_collection = self.client.collection(f"{self.prefix}_settings")

    def ensure_seeded(self, plants: list[dict[str, str]], tasks: list[dict[str, str]]) -> None:
        if list(self.plants_collection.limit(1).stream()):
            return

        batch = self.client.batch()
        plant_ids: dict[str, str] = {}
        for item in plants:
            doc = self.plants_collection.document()
            plant = _default_plant_record(item, doc.id)
            batch.set(doc, plant)
            plant_ids[plant["Plant"]] = plant["id"]

        for item in tasks:
            plant_id = plant_ids.get(item["Plant"])
            if plant_id is None:
                doc = self.plants_collection.document()
                plant = _default_plant_record({"Plant": item["Plant"]}, doc.id)
                batch.set(doc, plant)
                plant_ids[plant["Plant"]] = plant["id"]
                plant_id = plant["id"]
            task = _default_task_record(item, plant_id)
            batch.set(self.tasks_collection.document(task["ID"]), task)

        batch.commit()

    def ensure_library_seeded(self, plants: list[dict[str, object]]) -> None:
        batch = self.client.batch()
        for item in plants:
            library_item = _default_library_plant_record(item)
            batch.set(self.library_collection.document(library_item["id"]), library_item, merge=True)
        batch.commit()

    def list_plants(self) -> list[dict[str, str]]:
        items = []
        for doc in self.plants_collection.stream():
            payload = doc.to_dict()
            payload["MapLocations"] = _normalize_locations(payload.get("MapLocations"), payload.get("MapX", ""), payload.get("MapY", ""))
            payload["MapX"] = payload["MapLocations"][0]["x"] if payload["MapLocations"] else _clean(payload.get("MapX", ""))
            payload["MapY"] = payload["MapLocations"][0]["y"] if payload["MapLocations"] else _clean(payload.get("MapY", ""))
            items.append(payload)
        return items

    def list_library_plants(self) -> list[dict[str, object]]:
        return [_default_library_plant_record(doc.to_dict(), doc.id) for doc in self.library_collection.stream()]

    def list_tasks(self) -> list[dict[str, str]]:
        return [doc.to_dict() for doc in self.tasks_collection.stream()]

    def get_plant_by_name(self, name: str) -> dict[str, str] | None:
        docs = list(self.plants_collection.where("Plant", "==", name).limit(1).stream())
        return docs[0].to_dict() if docs else None

    def get_task(self, task_id: str) -> dict[str, str] | None:
        doc = self.tasks_collection.document(task_id).get()
        return doc.to_dict() if doc.exists else None

    def get_library_plant(self, library_id: str) -> dict[str, object] | None:
        doc = self.library_collection.document(library_id).get()
        return _default_library_plant_record(doc.to_dict(), doc.id) if doc.exists else None

    def find_library_plant_by_name(self, name: str) -> dict[str, object] | None:
        normalized = _normalize_library_key(name)
        if not normalized:
            return None
        for item in self.list_library_plants():
            if _normalize_library_key(item["CanonicalName"]) == normalized:
                return item
            if normalized in {_normalize_library_key(alias) for alias in item.get("Aliases", [])}:
                return item
        return None

    def create_plant(self, values: dict[str, str]) -> dict[str, str]:
        if self.get_plant_by_name(values["Plant"]) is not None:
            raise ValueError(f"Plant bestaat al: {values['Plant']}")
        doc = self.plants_collection.document()
        plant = _default_plant_record(values, doc.id)
        doc.set(plant)
        return plant

    def update_plant(self, original_name: str, values: dict[str, str]) -> dict[str, str]:
        plant = self.get_plant_by_name(original_name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {original_name}")
        duplicate = self.get_plant_by_name(values["Plant"])
        if duplicate is not None and duplicate["id"] != plant["id"]:
            raise ValueError(f"Plant bestaat al: {values['Plant']}")

        updated = _default_plant_record({**plant, **values}, plant["id"])
        batch = self.client.batch()
        batch.set(self.plants_collection.document(plant["id"]), updated)
        for doc in self.tasks_collection.where("PlantId", "==", plant["id"]).stream():
            task = doc.to_dict()
            task["Plant"] = updated["Plant"]
            batch.set(doc.reference, task)
        batch.commit()
        return updated

    def delete_plant(self, name: str) -> tuple[dict[str, str], int]:
        plant = self.get_plant_by_name(name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")

        batch = self.client.batch()
        removed_tasks = 0
        batch.delete(self.plants_collection.document(plant["id"]))
        for doc in self.tasks_collection.where("PlantId", "==", plant["id"]).stream():
            batch.delete(doc.reference)
            removed_tasks += 1
        batch.commit()
        return plant, removed_tasks

    def ensure_plant(self, name: str) -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is not None:
            return plant
        library_match = self.find_library_plant_by_name(name)
        values = {"Plant": name}
        if library_match:
            values["LibraryPlantId"] = str(library_match["id"])
        return self.create_plant(values)

    def create_task(self, values: dict[str, str]) -> dict[str, str]:
        if self.get_task(values["ID"]) is not None:
            raise ValueError(f"Taak-ID bestaat al: {values['ID']}")
        plant = self.ensure_plant(values["Plant"])
        task = _default_task_record(values, plant["id"])
        self.tasks_collection.document(task["ID"]).set(task)
        return task

    def update_task(self, task_id: str, values: dict[str, str]) -> dict[str, str]:
        if self.get_task(task_id) is None:
            raise ValueError(f"Taak niet gevonden: {task_id}")
        plant = self.ensure_plant(values["Plant"])
        task = _default_task_record(values, plant["id"])
        self.tasks_collection.document(task_id).set(task)
        return task

    def update_task_status(self, task_id: str, status: str) -> dict[str, str]:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Taak niet gevonden: {task_id}")
        task["Status"] = status
        self.tasks_collection.document(task_id).set(task)
        return task

    def get_garden_map(self) -> dict[str, str]:
        doc = self.settings_collection.document("garden_map").get()
        payload = doc.to_dict() if doc.exists else {}
        return _default_garden_map_record(payload)

    def save_garden_map(self, values: dict[str, str]) -> dict[str, str]:
        current = self.get_garden_map()
        payload = _default_garden_map_record({**current, **values})
        self.settings_collection.document("garden_map").set(payload)
        return payload

    def update_plant_location(self, name: str, x: str, y: str, label: str = "") -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        locations.append({"id": uuid4().hex, "x": _clean(x), "y": _clean(y), "label": _clean(label)})
        plant["MapLocations"] = locations
        plant["MapX"] = locations[0]["x"]
        plant["MapY"] = locations[0]["y"]
        self.plants_collection.document(plant["id"]).set(plant)
        return plant

    def delete_plant_location(self, name: str, location_id: str) -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        remaining = [item for item in locations if item["id"] != location_id]
        if len(remaining) == len(locations):
            raise ValueError("Locatie niet gevonden.")
        plant["MapLocations"] = remaining
        plant["MapX"] = remaining[0]["x"] if remaining else ""
        plant["MapY"] = remaining[0]["y"] if remaining else ""
        self.plants_collection.document(plant["id"]).set(plant)
        return plant

    def move_plant_location(self, name: str, location_id: str, x: str, y: str) -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        locations = _normalize_locations(plant.get("MapLocations"), plant.get("MapX", ""), plant.get("MapY", ""))
        for location in locations:
            if location["id"] == location_id:
                location["x"] = _clean(x)
                location["y"] = _clean(y)
                plant["MapLocations"] = locations
                plant["MapX"] = locations[0]["x"] if locations else ""
                plant["MapY"] = locations[0]["y"] if locations else ""
                self.plants_collection.document(plant["id"]).set(plant)
                return plant
        raise ValueError("Locatie niet gevonden.")

    def update_plant_library_link(self, name: str, library_id: str) -> dict[str, str]:
        plant = self.get_plant_by_name(name)
        if plant is None:
            raise ValueError(f"Plant niet gevonden: {name}")
        if library_id and self.get_library_plant(library_id) is None:
            raise ValueError("Bibliotheekplant niet gevonden.")
        plant["LibraryPlantId"] = _clean(library_id)
        self.plants_collection.document(plant["id"]).set(plant)
        return plant


def create_store(backend: str, file_path: Path, project_id: str | None, prefix: str) -> BaseStore:
    if backend == "firestore":
        return FirestoreStore(project_id=project_id, collection_prefix=prefix)
    return FileStore(file_path)


def generate_task_id(existing_ids: list[str], plant_name: str) -> str:
    base = "".join(part[:1] for part in re.findall(r"[A-Za-z]+", plant_name.upper()))[:3]
    base = (base or "TSK").ljust(3, "X")
    pattern = re.compile(rf"^{re.escape(base)}-(\d+)$")
    numbers = [int(match.group(1)) for value in existing_ids if (match := pattern.match(value))]
    next_number = (max(numbers) + 1) if numbers else 1
    return f"{base}-{next_number:02d}"


def _default_garden_map_record(values: dict[str, str]) -> dict[str, str]:
    return {
        "BackgroundPath": _clean(values.get("BackgroundPath", "")),
        "BackgroundMimeType": _clean(values.get("BackgroundMimeType", "")),
        "UpdatedAt": _clean(values.get("UpdatedAt", datetime.utcnow().isoformat())),
    }
