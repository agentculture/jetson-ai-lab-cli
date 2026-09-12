"""Enforce approved-dependencies allowlist for runtime and optional dependencies."""

from __future__ import annotations

import tomllib
from pathlib import Path

# Approved runtime and optional dependencies.
# Any addition requires a deliberate discussion and update to this list
# AND to CLAUDE.md's Conventions section.
APPROVED_DEPENDENCIES = {
    "discord-bot-cli",
    "pymongo",
}


def test_dependencies_in_approved_allowlist() -> None:
    """All runtime dependencies must be in the approved allowlist.

    This test reads pyproject.toml's [project] dependencies and
    [project.optional-dependencies], extracts package names, and
    verifies each is in the APPROVED_DEPENDENCIES allowlist.
    Adding a dependency without approving it here breaks the build.
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)

    # Extract all distributions from [project] dependencies
    dependencies = pyproject.get("project", {}).get("dependencies", [])

    # Extract all distributions from [project.optional-dependencies]
    optional_deps = pyproject.get("project", {}).get("optional-dependencies", {})
    all_optional_specs = [dep for deps in optional_deps.values() for dep in deps]

    all_specs = dependencies + all_optional_specs

    # Extract package names from specs (e.g., "discord-bot-cli[discord]" -> "discord-bot-cli")
    found_deps = set()
    for spec in all_specs:
        # Split on '[' and '>' to isolate the base package name
        pkg_name = (
            spec.split("[")[0].split(">")[0].split("<")[0].split("=")[0].split(";")[0].strip()
        )
        if pkg_name:
            found_deps.add(pkg_name)

    # Check all found dependencies are approved
    unapproved = found_deps - APPROVED_DEPENDENCIES
    assert not unapproved, (
        f"Unapproved dependencies found: {sorted(unapproved)}. "
        f"Update APPROVED_DEPENDENCIES in this test and CLAUDE.md's Conventions section."
    )
