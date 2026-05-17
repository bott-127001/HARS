"""Minimal in-memory async Mongo stand-in for unit tests (no implementation changes)."""

from __future__ import annotations

import copy
from typing import Any, Iterator


def _match(filter_doc: dict[str, Any], doc: dict[str, Any]) -> bool:
    for k, v in filter_doc.items():
        if k == "$or":
            return any(_match(sub, doc) for sub in v)
        if k not in doc:
            return False
        if isinstance(v, dict) and "$in" in v:
            if doc[k] not in v["$in"]:
                return False
        elif doc[k] != v:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]], coll: "FakeCollection"):
        self._raw = list(docs)
        self._coll = coll
        self._sort_spec: list[tuple[str, int]] | None = None
        self._limit_n: int | None = None
        self._skip = 0

    def sort(self, key_or_list: Any, direction: int | None = None) -> FakeCursor:
        if isinstance(key_or_list, list):
            self._sort_spec = [(k, d) for k, d in key_or_list]
        else:
            self._sort_spec = [(str(key_or_list), int(direction if direction is not None else 1))]
        return self

    def limit(self, n: int) -> FakeCursor:
        self._limit_n = n
        return self

    def skip(self, n: int) -> FakeCursor:
        self._skip = n
        return self

    def _materialize(self) -> list[dict[str, Any]]:
        docs = list(self._raw)
        if self._sort_spec:
            for key, direction in reversed(self._sort_spec):
                docs.sort(key=lambda d, k=key: d.get(k, ""), reverse=(direction < 0))
        if self._skip:
            docs = docs[self._skip :]
        if self._limit_n is not None:
            docs = docs[: self._limit_n]
        return [copy.deepcopy(d) for d in docs]

    def __aiter__(self) -> "FakeCursor":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not hasattr(self, "_iter_queue"):
            self._iter_queue = iter(self._materialize())
        try:
            return next(self._iter_queue)
        except StopIteration as ex:
            del self._iter_queue
            raise StopAsyncIteration from ex

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        out = self._materialize()
        if length is not None:
            return out[:length]
        return out


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self._docs: list[dict[str, Any]] = []
        self.update_one_calls: list[dict[str, Any]] = []
        self.insert_one_calls: list[dict[str, Any]] = []
        self.insert_many_calls: list[list[dict[str, Any]]] = []
        self.delete_many_calls: list[dict[str, Any]] = []

    def find(self, filter_doc: dict[str, Any] | None = None) -> FakeCursor:
        filt = filter_doc or {}
        matched = [d for d in self._docs if _match(filt, d)]
        return FakeCursor(matched, self)

    async def find_one(self, filter_doc: dict[str, Any] | None = None) -> dict[str, Any] | None:
        filt = filter_doc or {}
        for d in self._docs:
            if _match(filt, d):
                return copy.deepcopy(d)
        return None

    async def count_documents(self, filter_doc: dict[str, Any] | None = None) -> int:
        filt = filter_doc or {}
        return sum(1 for d in self._docs if _match(filt, d))

    async def update_one(
        self,
        filt: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> Any:
        self.update_one_calls.append({"filter": filt, "update": update, "upsert": upsert})
        set_doc = (update or {}).get("$set", {})
        for i, d in enumerate(self._docs):
            if _match(filt, d):
                d.update(set_doc)
                return type("Res", (), {"modified_count": 1})()
        if upsert:
            nd = {**filt, **set_doc}
            if "_id" not in nd:
                nd["_id"] = len(self._docs)
            self._docs.append(nd)
            return type("Res", (), {"modified_count": 0})()
        return type("Res", (), {"modified_count": 0})()

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self.insert_one_calls.append(doc)
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = len(self._docs)
        self._docs.append(d)
        return type("Res", (), {"inserted_id": d["_id"]})()

    async def insert_many(self, docs: list[dict[str, Any]]) -> None:
        self.insert_many_calls.append(list(docs))
        for d in docs:
            await self.insert_one(d)

    async def delete_many(self, filt: dict[str, Any]) -> Any:
        self.delete_many_calls.append(filt)
        keep: list[dict[str, Any]] = []
        removed = 0
        id_in = filt.get("_id", {}).get("$in") if isinstance(filt.get("_id"), dict) else None
        for d in self._docs:
            if id_in is not None and d.get("_id") in id_in:
                removed += 1
                continue
            keep.append(d)
        self._docs = keep
        return type("Res", (), {"deleted_count": removed})()

    def seed(self, docs: list[dict[str, Any]]) -> None:
        self._docs = [copy.deepcopy(x) for x in docs]


class FakeMotorDb:
    """Motor-like database object with named collections."""

    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(name)
        return self._collections[name]

    def clear_all(self) -> None:
        self._collections.clear()


async def fake_ping_ok() -> dict[str, Any]:
    return {"ok": 1}
