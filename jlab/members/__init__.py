"""Discord members-report pipeline (scan -> aggregate -> translate -> render).

This package holds the person-level statistics pipeline described in
``docs/specs/2026-09-04-jlab-discord-members-report.md``. See
:mod:`jlab.members.paths` for the containment boundary that every stage
writing report output must go through.
"""

from __future__ import annotations
