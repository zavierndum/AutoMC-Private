"""The build result: plain data, JSON-serializable, written next to the input as result.json."""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field

RESULT_VERSION = 1


@dataclass
class BuildResult:
    result_version: int
    triga_version: str
    spec_version: int
    built_at: str
    code: str
    facility: str
    name: str
    preset: str
    spec: dict
    spec_hash: str
    profile: dict
    nuclear_data: dict
    assumptions: list
    files: list
    summary: dict
    warnings: list
    outdir: str
    reused: bool = False
    build_seconds: float = 0.0
    facility_data: dict | None = None      # which facility-pack items the build read and their labels (presets.facility_data)

    def to_dict(self):
        return dataclasses.asdict(self)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(d) - names
        if unknown:
            raise ValueError(f"unknown result fields: {sorted(unknown)}")
        return cls(**d)
