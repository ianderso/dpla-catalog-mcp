"""Tests over the MCP Registry listing: ``server.json`` and the README marker.

The registry lists what ``server.json`` says, and confirms that the PyPI
package is this project's by finding ``mcp-name: <name>`` in the README that
PyPI holds for that exact version. Nothing else fails if the three drift
apart; the release would, after PyPI had already accepted it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dpla_catalog_mcp import __version__

ROOT = Path(__file__).parent.parent
SERVER_JSON = json.loads((ROOT / "server.json").read_text())
PACKAGE = SERVER_JSON["packages"][0]
VARIABLES = {v["name"]: v for v in PACKAGE["environmentVariables"]}


def test_server_json_carries_the_package_version_twice():
    assert SERVER_JSON["version"] == __version__
    assert PACKAGE["version"] == __version__


def test_server_json_points_at_this_package_on_pypi():
    assert PACKAGE["registryType"] == "pypi"
    assert PACKAGE["identifier"] == "dpla-catalog-mcp"
    assert SERVER_JSON["name"] == "io.github.ianderso/dpla-catalog-mcp"


def test_the_readme_carries_the_registry_marker_for_this_name():
    marker = re.escape(f"mcp-name: {SERVER_JSON['name']}")
    assert re.search(marker + r"(\s|-->|<)", (ROOT / "README.md").read_text())


def test_server_json_declares_every_setting_the_server_reads():
    read = set(
        re.findall(r'"(DPLA_[A-Z_]+)"', (ROOT / "src/dpla_catalog_mcp/config.py").read_text())
    )
    assert set(VARIABLES) == read


def test_the_key_is_required_and_secret_and_nothing_else_is():
    """A client should ask for the key, store it as a secret, and ask for nothing else."""
    for name, var in VARIABLES.items():
        expected = name == "DPLA_API_KEY"
        assert bool(var.get("isRequired")) is expected, name
        assert bool(var.get("isSecret")) is expected, name


def test_the_description_fits_the_registry_limit():
    """The MCP Registry refuses a description over 100 characters, after PyPI has the release."""
    assert len(SERVER_JSON["description"]) <= 100
