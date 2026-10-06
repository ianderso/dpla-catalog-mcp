"""Configuration from environment variables.

======================  ======================================================
``DPLA_API_KEY``        The 32-character key DPLA issued. Required.
``DPLA_CACHE_DIR``      Directory for the on-disk response cache.
``DPLA_TIMEOUT``        HTTP timeout in seconds.
``DPLA_MIN_INTERVAL``   Least seconds between the start of one request to
                        DPLA and the next.
======================  ======================================================

A ``.env`` file in the working directory supplies any of these that the
environment does not.

The key is never echoed: not in an error, not in a log line, not in the
dataclass's ``repr``.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

#: The value ``.env.example`` ships with, which is not a key.
PLACEHOLDER_KEY = "your-key-here"

#: What DPLA accepts as a key: 32 letters, digits or hyphens. Anything else is
#: refused by DPLA with the same 403 as a wrong key, so it is caught here.
KEY_PATTERN = re.compile(r"[A-Za-z0-9-]{32}")

#: Where a key is requested. DPLA emails it to the address given.
KEY_HELP = (
    "Request a free key with `curl -X POST https://api.dp.la/v2/api_key/YOUR_EMAIL`; "
    "it arrives by email. Put it in the environment as DPLA_API_KEY, or in a .env "
    "file in the directory the server starts in. See README 'Setup'."
)


class ConfigError(RuntimeError):
    """Raised when a setting is missing or unusable."""


@dataclass
class Config:
    """Resolved server configuration.

    Attributes
    ----------
    api_key : str
        The DPLA key. Excluded from ``repr`` so it cannot reach a log line.
    cache_dir : Path
        Directory holding cached responses.
    timeout : float
        HTTP timeout in seconds.
    min_interval : float
        Least seconds between the start of one DPLA request and the next.
    """

    api_key: str = field(repr=False)
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "dpla-catalog-mcp")
    timeout: float = 30.0
    min_interval: float = 0.25


def load_config() -> Config:
    """Load configuration from the environment.

    A ``.env`` file in the working directory is read if present; real
    environment variables win. Only the working directory is consulted.
    ``load_dotenv()`` with no path searches upward from the *calling
    module's* location instead, which for an installed package is
    ``site-packages``: it would ignore the ``.env`` beside the user and could
    read an unrelated one from a parent such as the home directory.

    Returns
    -------
    Config
        Fully resolved configuration.

    Raises
    ------
    ConfigError
        If ``DPLA_API_KEY`` is unset, still the ``.env.example`` placeholder,
        or not shaped like a DPLA key, or a numeric setting is unusable. The
        server loads its configuration on the first tool call, so this
        surfaces there as a ``not_configured`` result rather than as a crash
        at launch. The message never contains the key.
    """
    load_dotenv(Path.cwd() / ".env")

    api_key = (os.environ.get("DPLA_API_KEY") or "").strip()
    if not api_key:
        raise ConfigError(f"DPLA_API_KEY is not set. {KEY_HELP}")
    if api_key == PLACEHOLDER_KEY:
        raise ConfigError(
            "DPLA_API_KEY is still the placeholder copied from .env.example. "
            "Replace it with the key DPLA emailed you."
        )
    if not KEY_PATTERN.fullmatch(api_key):
        # Describe the value, never repeat it.
        raise ConfigError(
            "DPLA_API_KEY is set but is not shaped like a DPLA key, which is 32 "
            f"letters, digits or hyphens; the value set has {len(api_key)} "
            "characters. Check for quotes or a stray character copied with it."
        )

    cfg = Config(api_key=api_key)
    if raw := os.environ.get("DPLA_CACHE_DIR"):
        cfg.cache_dir = Path(raw).expanduser()
    if raw := os.environ.get("DPLA_TIMEOUT"):
        cfg.timeout = _number("DPLA_TIMEOUT", raw, allow_zero=False)
    if raw := os.environ.get("DPLA_MIN_INTERVAL"):
        cfg.min_interval = _number("DPLA_MIN_INTERVAL", raw, allow_zero=True)
    return cfg


def _number(name: str, raw: str, *, allow_zero: bool) -> float:
    """Parse one numeric setting, naming the variable if it is unusable.

    A bare ``float("thirty")`` would surface as "could not convert string to
    float", which does not say which setting is wrong.
    """
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number; got {raw!r}.") from None
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        bound = "zero or more" if allow_zero else "greater than zero"
        raise ConfigError(f"{name} must be {bound}; got {raw!r}.")
    return value
