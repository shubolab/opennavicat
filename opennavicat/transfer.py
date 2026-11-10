"""Portable, loss-aware result import and export."""
import base64
import csv
import json
from pathlib import Path


def json_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$binary": base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)


def export_result(result, path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        # Row arrays preserve duplicate SQL column names, unlike object conversion.
        value = {"columns": result.columns, "rows": result.rows}
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value), "utf-8")
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(result.columns)
            for row in result.rows:
                writer.writerow(["\\N" if value is None else str(value).replace("\\", "\\\\") for value in row])


def import_data(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text("utf-8-sig"))
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            columns, rows = data["columns"], data["rows"]
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            columns = list(data[0])
            if any(set(row) != set(columns) for row in data):
                raise ValueError("Every JSON object must contain the same columns.")
            rows = [[row[column] for column in columns] for row in data]
        else:
            raise ValueError("Expected {columns: [...], rows: [...]} or a non-empty array of objects.")
        rows = [[base64.b64decode(value["$binary"], validate=True) if isinstance(value, dict) and set(value) == {"$binary"} else value for value in row] for row in rows]
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            columns = next(reader, [])
            rows = [[None if v == "\\N" else v.replace("\\\\", "\\") for v in row] for row in reader]
    if not columns or len(set(columns)) != len(columns) or any(not isinstance(c, str) or not c for c in columns):
        raise ValueError("Import needs unique, non-empty column names.")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("A row has a different number of cells from the header.")
    return columns, rows
