"""Independent host-monotonic label events and ground-truth intervals."""

import csv
import json
import os
from collections.abc import Callable
from pathlib import Path

ACTIVITIES = {
    "1": "pull-up",
    "2": "push-up",
    "3": "squat",
    "4": "jump",
    "5": "walking",
    "6": "stairs",
    "7": "household",
    "8": "standing",
    "9": "sitting",
    "0": "other",
}
FIELDS = [
    "set_id",
    "activity",
    "start_time_s",
    "end_time_s",
    "expected_rep_count",
    "note",
    "completion",
]


class Labels:
    def __init__(self, path: Path, clock: Callable[[], float]):
        self.path = path
        self.clock = clock
        self.activity = "other"
        self.sets: list[dict] = []
        self.active: dict | None = None
        self.events = (path / "label_events.jsonl").open("w", encoding="utf-8")
        self.save()

    def event(self, kind: str, **values: object) -> None:
        self.events.write(json.dumps(dict(event=kind, time_s=self.clock(), **values)) + "\n")
        self.events.flush()

    def select(self, activity: str) -> None:
        if self.active:
            raise ValueError("End the current set before changing activity")
        if not activity.strip():
            raise ValueError("Activity cannot be empty")
        self.activity = activity.strip()
        self.event("activity_selected", activity=self.activity)

    def toggle(self) -> dict:
        now = self.clock()
        if self.active:
            current = self.active
            current.update(end_time_s=now, completion="complete")
            self.active = None
            self.event("set_ended", set_id=current["set_id"])
        else:
            current = dict(
                set_id=f"{len(self.sets) + 1:03d}",
                activity=self.activity,
                start_time_s=now,
                end_time_s=None,
                expected_rep_count=None,
                note="",
                completion="open",
            )
            self.sets.append(current)
            self.active = current
            self.event("set_started", set_id=current["set_id"], activity=self.activity)
        self.save()
        return current

    def reps(self, item: dict, value: str) -> None:
        if value.strip() and (not value.strip().isascii() or not value.strip().isdigit()):
            raise ValueError("Enter a non-negative integer, or Enter for unknown/background")
        item["expected_rep_count"] = int(value) if value.strip() else None
        self.event("repetition_count", set_id=item["set_id"], count=item["expected_rep_count"])
        self.save()

    def note(self, text: str) -> None:
        item = self.active or (self.sets[-1] if self.sets else None)
        if item:
            item["note"] = (item["note"] + " " + text).strip()
            self.save()
        self.event("note", set_id=item["set_id"] if item else None, note=text)

    def save(self) -> None:
        temp = self.path / "labels.csv.tmp"
        with temp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, FIELDS)
            writer.writeheader()
            writer.writerows(self.sets)
        os.replace(temp, self.path / "labels.csv")

    def close(self) -> None:
        if self.active:
            self.active.update(end_time_s=self.clock(), completion="interrupted")
            self.event("set_interrupted", set_id=self.active["set_id"])
            self.active = None
        self.save()
        self.events.close()
