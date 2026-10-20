"""Persistence. One JSON document per household keeps reads and writes simple.

Records ("blobs") hold everything that isn't the pantry itself, such as a user's grocery-app
logins. They live under their own keys so they never travel to the browser with the pantry.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from .models import Household, seed_household


class Store(Protocol):
    def load(self, household_id: str) -> Household: ...
    def save(self, household_id: str, household: Household) -> None: ...
    def load_record(self, key: str) -> dict[str, Any] | None: ...
    def save_record(self, key: str, value: dict[str, Any]) -> None: ...
    def delete_record(self, key: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, Household] = {}
        self._records: dict[str, str] = {}

    def load(self, household_id: str) -> Household:
        if household_id not in self._data:
            self._data[household_id] = seed_household()
        return self._data[household_id].model_copy(deep=True)

    def save(self, household_id: str, household: Household) -> None:
        self._data[household_id] = household.model_copy(deep=True)

    def load_record(self, key: str) -> dict[str, Any] | None:
        raw = self._records.get(key)
        return json.loads(raw) if raw else None

    def save_record(self, key: str, value: dict[str, Any]) -> None:
        self._records[key] = json.dumps(value)

    def delete_record(self, key: str) -> None:
        self._records.pop(key, None)


class FileStore:
    """Local development store that survives server restarts."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        (self._dir / "records").mkdir(parents=True, exist_ok=True)

    def _path(self, household_id: str) -> Path:
        return self._dir / f"{household_id}.json"

    def _record_path(self, key: str) -> Path:
        return self._dir / "records" / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json")

    def load(self, household_id: str) -> Household:
        path = self._path(household_id)
        if not path.exists():
            return seed_household()
        return Household.model_validate_json(path.read_text())

    def save(self, household_id: str, household: Household) -> None:
        self._path(household_id).write_text(household.model_dump_json(indent=2))

    def load_record(self, key: str) -> dict[str, Any] | None:
        path = self._record_path(key)
        return json.loads(path.read_text()) if path.exists() else None

    def save_record(self, key: str, value: dict[str, Any]) -> None:
        self._record_path(key).write_text(json.dumps(value))

    def delete_record(self, key: str) -> None:
        self._record_path(key).unlink(missing_ok=True)


class DynamoStore:
    """Production store: one DynamoDB item per household (pk = household id), records under pk = "record#<key>"."""

    def __init__(self, table_name: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb").Table(table_name)

    def load(self, household_id: str) -> Household:
        item = self._table.get_item(Key={"pk": household_id}).get("Item")
        if not item:
            return seed_household()
        return Household.model_validate_json(item["doc"])

    def save(self, household_id: str, household: Household) -> None:
        self._table.put_item(Item={"pk": household_id, "doc": household.model_dump_json()})

    def load_record(self, key: str) -> dict[str, Any] | None:
        item = self._table.get_item(Key={"pk": f"record#{key}"}).get("Item")
        return json.loads(item["doc"]) if item else None

    def save_record(self, key: str, value: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"record#{key}", "doc": json.dumps(value)})

    def delete_record(self, key: str) -> None:
        self._table.delete_item(Key={"pk": f"record#{key}"})


def store_from_env() -> Store:
    if table := os.environ.get("PANTRY_TABLE"):
        return DynamoStore(table)
    if os.environ.get("PANTRY_STORE") == "memory":
        return MemoryStore()
    return FileStore(Path(os.environ.get("PANTRY_DATA_DIR", ".data")))


def dumps_for_api(household: Household) -> str:
    return json.dumps(household.model_dump(mode="json", by_alias=True))
