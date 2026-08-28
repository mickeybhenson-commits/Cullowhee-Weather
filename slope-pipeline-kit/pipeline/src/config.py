"""Configuration loader for the Cullowhee Creek LEWS."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else ROOT / "config.yaml"
        with open(path) as f:
            return cls(raw=yaml.safe_load(f))

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def aoi(self):
        return self.raw["aoi"]

    @property
    def bbox(self):
        a = self.aoi
        return (a["min_lon"], a["min_lat"], a["max_lon"], a["max_lat"])

    @property
    def analysis(self):
        return self.raw["analysis"]

    @property
    def forecast(self):
        return self.raw["forecast"]

    @property
    def output_dir(self) -> Path:
        d = ROOT / self.raw["alerting"]["output_dir"]
        d.mkdir(parents=True, exist_ok=True)
        return d


def earthdata_credentials():
    """Earthdata login from env vars (never hard-code credentials)."""
    user = os.environ.get("EARTHDATA_USERNAME")
    pwd = os.environ.get("EARTHDATA_PASSWORD")
    return (user, pwd) if user and pwd else None
