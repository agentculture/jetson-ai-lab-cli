"""Discord links-report pipeline (scan -> extract -> render).

This package holds the URL-sharing report described in
``docs/plans/2026-09-05-jlab-discord-links-report.md``: ``jlab discord
links`` scans the Jetson AI Lab Discord's public channels for shared URLs
over a time window and renders a local, gitignored report of what the
community is sharing. The stages are: reuse the existing windowed scan
(``jlab/cli/_discord.py::scan_window``) to fetch messages, extract URL
records from them (``jlab/links/extract.py``), resolve author display
names in one final batch (via the unchanged ``jlab/members/resolve.py``),
and render HTML plus CSV artifacts (``jlab/links/report.py``) through the
shared CSV writer.

See :mod:`jlab.links.paths` for the containment boundary that every stage
writing report output must go through.
"""

from __future__ import annotations
