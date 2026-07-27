"""Configuration loading for the Dombori collector.

Configuration is sourced from environment variables (optionally populated
from a local ``.env.local`` file). Loading fails fast with a clear error
message when required variables are missing -- no silent defaults for
anything that touches the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_REQUIRED_VARS = (
    "DOMBORI_DB_HOST",
    "DOMBORI_DB_PORT",
    "DOMBORI_DB_NAME",
    "DOMBORI_DB_USER",
    "DOMBORI_DB_PASSWORD",
    "DOMBORI_DATA_DIR",
)

# Optional: read-only role password. Absent means the `dombori_ro` role is
# not (re)configured by `init-db`.
_OPTIONAL_VARS = ("DOMBORI_RO_PASSWORD",)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    ro_password: str | None
    data_dir: Path


def load_config(dotenv_path: str | Path = ".env.local") -> Config:
    """Load configuration from the environment.

    Loads ``dotenv_path`` (if present) into ``os.environ`` first, then reads
    all ``DOMBORI_*`` variables. Raises :class:`ConfigError` listing every
    missing required variable in one shot, rather than failing on the first.
    """
    load_dotenv(dotenv_path)

    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in the environment or in "
            + str(dotenv_path)
        )

    try:
        db_port = int(os.environ["DOMBORI_DB_PORT"])
    except ValueError as exc:
        raise ConfigError(
            f"DOMBORI_DB_PORT must be an integer, got {os.environ['DOMBORI_DB_PORT']!r}"
        ) from exc

    return Config(
        db_host=os.environ["DOMBORI_DB_HOST"],
        db_port=db_port,
        db_name=os.environ["DOMBORI_DB_NAME"],
        db_user=os.environ["DOMBORI_DB_USER"],
        db_password=os.environ["DOMBORI_DB_PASSWORD"],
        ro_password=os.environ.get("DOMBORI_RO_PASSWORD") or None,
        data_dir=Path(os.environ["DOMBORI_DATA_DIR"]),
    )
