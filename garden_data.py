from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET


MONTHS = [
    "Januari",
    "Februari",
    "Maart",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Augustus",
    "September",
    "Oktober",
    "November",
    "December",
]
MONTH_INDEX = {month: index for index, month in enumerate(MONTHS)}
PRIORITY_ORDER = {"Hoog": 0, "Middel": 1, "Laag": 2}
STATUS_ORDER = {"Open": 0, "Uitgesteld": 1, "Gereed": 2}

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"main": MAIN_NS, "rel": REL_NS}


def _excel_col_to_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    value = 0
    for char in letters:
        value = value * 26 + (ord(char.upper()) - 64)
    return max(value - 1, 0)


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _row_to_values(row: ET.Element, shared_strings: list[str]) -> list[str]:
    cells: dict[int, str] = {}
    for cell in row.findall("main:c", NS):
        ref = cell.attrib.get("r", "")
        col_idx = _excel_col_to_index(ref)
        cell_type = cell.attrib.get("t")
        value = ""
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iterfind(".//main:t", NS))
        else:
            raw = cell.findtext("main:v", default="", namespaces=NS)
            if cell_type == "s" and raw:
                value = shared_strings[int(raw)]
            else:
                value = raw
        cells[col_idx] = _clean(value)

    if not cells:
        return []

    max_idx = max(cells)
    return [cells.get(index, "") for index in range(max_idx + 1)]


def _sheet_rows(workbook_path: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for string_item in root.findall("main:si", NS):
                texts = [node.text or "" for node in string_item.iterfind(".//main:t", NS)]
                shared_strings.append("".join(texts))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {
            rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
            for rel in relationships.findall("rel:Relationship", NS)
        }

        sheets = workbook.find("main:sheets", NS)
        if sheets is None:
            return []

        target = None
        for sheet in sheets:
            if sheet.attrib.get("name") == sheet_name:
                rel_id = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
                target = relationship_map.get(rel_id or "")
                break

        if not target:
            return []

        sheet_xml = ET.fromstring(archive.read(target))
        rows = sheet_xml.findall(".//main:sheetData/main:row", NS)
        return [_row_to_values(row, shared_strings) for row in rows]


def _records_from_rows(rows: list[list[str]], header_row_index: int) -> list[dict[str, str]]:
    if len(rows) <= header_row_index:
        return []

    headers = [_clean(header) for header in rows[header_row_index]]
    records: list[dict[str, str]] = []
    for row in rows[header_row_index + 1 :]:
        if not any(_clean(cell) for cell in row):
            continue
        padded = row + [""] * max(0, len(headers) - len(row))
        record = {
            header: _clean(padded[index]) for index, header in enumerate(headers) if header
        }
        if any(record.values()):
            records.append(record)
    return records


def _non_empty_column_values(rows: list[list[str]], header_row_index: int) -> dict[str, list[str]]:
    if len(rows) <= header_row_index:
        return {}

    headers = [_clean(header) for header in rows[header_row_index]]
    columns: dict[str, list[str]] = {}
    for index, header in enumerate(headers):
        if not header:
            continue
        values = []
        for row in rows[header_row_index + 1 :]:
            if index < len(row):
                value = _clean(row[index])
                if value:
                    values.append(value)
        columns[header] = values
    return columns


@dataclass
class GardenWorkbook:
    workbook_path: Path

    def load(self) -> dict[str, object]:
        tasks = self._load_tasks()
        plants = self._load_plants(tasks)
        lists = self._load_lists()
        months = lists.get("Maanden", MONTHS[:])
        categories = lists.get("Categorieën") or sorted(
            {task["Categorie"] for task in tasks if task["Categorie"]}
        )
        priorities = lists.get("Prioriteiten", ["Hoog", "Middel", "Laag"])
        statuses = lists.get("Statussen", ["Open", "Uitgesteld", "Gereed"])
        durations = lists.get("Duur") or sorted({task["Duur"] for task in tasks if task["Duur"]})

        monthly_summary = self._build_monthly_summary(tasks)
        yearly_heatmap = self._build_yearly_heatmap(tasks, plants)
        plant_workload = self._build_plant_workload(tasks)
        next_up = self._build_next_up(tasks)

        return {
            "tasks": tasks,
            "plants": plants,
            "months": months,
            "categories": categories,
            "priorities": priorities,
            "statuses": statuses,
            "durations": durations,
            "monthly_summary": monthly_summary,
            "yearly_heatmap": yearly_heatmap,
            "plant_workload": plant_workload,
            "next_up": next_up,
        }

    def _load_tasks(self) -> list[dict[str, str]]:
        rows = _sheet_rows(self.workbook_path, "Takenlijst")
        records = _records_from_rows(rows, header_row_index=2)
        tasks: list[dict[str, str]] = []
        for record in records:
            task = {
                "ID": record.get("ID", ""),
                "Plant": record.get("Plant", ""),
                "Maand": record.get("Maand", ""),
                "Week": record.get("Week", ""),
                "Categorie": record.get("Categorie", ""),
                "Actie": record.get("Actie", ""),
                "Prioriteit": record.get("Prioriteit", ""),
                "Status": record.get("Status", ""),
                "Duur": record.get("Duur", ""),
                "Opmerking": record.get("Opmerking", ""),
                "DashboardVolgorde": record.get("DashboardVolgorde", ""),
            }
            if task["ID"] and task["Plant"] and task["Actie"]:
                tasks.append(task)

        return sorted(
            tasks,
            key=lambda task: (
                MONTH_INDEX.get(task["Maand"], 99),
                int(task["Week"]) if task["Week"].isdigit() else 99,
                PRIORITY_ORDER.get(task["Prioriteit"], 99),
                task["Plant"],
                task["ID"],
            ),
        )

    def _load_plants(self, tasks: list[dict[str, str]]) -> list[dict[str, object]]:
        rows = _sheet_rows(self.workbook_path, "Plantregister")
        records = _records_from_rows(rows, header_row_index=2)
        task_counter = Counter(task["Plant"] for task in tasks)
        open_counter = Counter(task["Plant"] for task in tasks if task["Status"] != "Gereed")

        plants = []
        for record in records:
            name = record.get("Plant", "")
            if not name:
                continue
            plants.append(
                {
                    "Plant": name,
                    "Type": record.get("Type", ""),
                    "Snoeigroep": record.get("Snoeigroep", ""),
                    "Standplaats": record.get("Standplaats", ""),
                    "Winterhard": record.get("Winterhard", ""),
                    "Notitie": record.get("Notitie", ""),
                    "Taken": task_counter.get(name, 0),
                    "OpenTaken": open_counter.get(name, 0),
                }
            )

        return sorted(plants, key=lambda plant: plant["Plant"])

    def _load_lists(self) -> dict[str, list[str]]:
        rows = _sheet_rows(self.workbook_path, "Lijsten")
        return _non_empty_column_values(rows, header_row_index=0)

    def _build_monthly_summary(self, tasks: list[dict[str, str]]) -> list[dict[str, object]]:
        by_month = {month: [] for month in MONTHS}
        for task in tasks:
            by_month.setdefault(task["Maand"], []).append(task)

        summary = []
        for month in MONTHS:
            month_tasks = by_month.get(month, [])
            category_counts = Counter(task["Categorie"] for task in month_tasks)
            summary.append(
                {
                    "month": month,
                    "total": len(month_tasks),
                    "open": sum(task["Status"] != "Gereed" for task in month_tasks),
                    "done": sum(task["Status"] == "Gereed" for task in month_tasks),
                    "high_priority": sum(task["Prioriteit"] == "Hoog" for task in month_tasks),
                    "categories": category_counts,
                }
            )
        return summary

    def _build_yearly_heatmap(
        self, tasks: list[dict[str, str]], plant_register: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        plants = defaultdict(lambda: {month: 0 for month in MONTHS})
        for plant in plant_register:
            plants[plant["Plant"]]
        for task in tasks:
            plants[task["Plant"]][task["Maand"]] += 1

        heatmap = []
        for plant, counts in sorted(plants.items()):
            total = sum(counts.values())
            heatmap.append({"plant": plant, "months": counts, "total": total})
        return heatmap

    def _build_plant_workload(self, tasks: list[dict[str, str]]) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for task in tasks:
            grouped[task["Plant"]].append(task)

        workload = []
        for plant, items in grouped.items():
            workload.append(
                {
                    "plant": plant,
                    "total": len(items),
                    "open": sum(task["Status"] != "Gereed" for task in items),
                    "high": sum(task["Prioriteit"] == "Hoog" for task in items),
                    "next_month": min(
                        (MONTH_INDEX.get(task["Maand"], 99) for task in items if task["Status"] != "Gereed"),
                        default=99,
                    ),
                }
            )

        return sorted(
            workload,
            key=lambda item: (-item["open"], -item["high"], item["next_month"], item["plant"]),
        )

    def _build_next_up(self, tasks: list[dict[str, str]]) -> list[dict[str, str]]:
        open_tasks = [task for task in tasks if task["Status"] != "Gereed"]
        return sorted(
            open_tasks,
            key=lambda task: (
                MONTH_INDEX.get(task["Maand"], 99),
                PRIORITY_ORDER.get(task["Prioriteit"], 99),
                int(task["Week"]) if task["Week"].isdigit() else 99,
                task["Plant"],
            ),
        )[:8]
