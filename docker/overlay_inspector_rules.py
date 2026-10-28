"""Replace the installed sc_crawler inspector rules with the version on GitHub.

Inspector rules change more often than sc-crawler gets released to PyPI, so at
Docker build time we look up `inspector_rules.py` at the given branch or commit
of sc-crawler, and use it instead of the installed one if the crawler version
at that revision matches the installed version. Compatibility of the
GitHub rules with the PyPI release is tested in the sc-crawler CI.

Never fails: falls back to the installed rules on any error.
Records the source of the rules for /healthcheck in `inspector_rules_source.txt`
inside the installed sc_keeper package.

Usage: python overlay_inspector_rules.py [ref]
"""

import os
import re
import sys
import tempfile
import urllib.request
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

REPO = "SpareCores/sc-crawler"
TIMEOUT = 10


def fetch(url: str, accept: str | None = None) -> str:
    request = urllib.request.Request(url)
    if accept:
        request.add_header("Accept", accept)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode()


def overlay(ref: str) -> str:
    installed_version = version("sparecores-crawler")
    # pin a commit so that all files are downloaded from the same revision
    sha = fetch(
        f"https://api.github.com/repos/{REPO}/commits/{ref}",
        accept="application/vnd.github.sha",
    ).strip()
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}/src/sc_crawler"
    github_version = re.search(
        r'^__version__ = "([^"]+)"', fetch(f"{raw}/__init__.py"), re.MULTILINE
    ).group(1)
    if github_version != installed_version:
        print(
            f"Keeping installed inspector rules: sc-crawler {installed_version} "
            f"installed, but {github_version} on {ref}."
        )
        return "pypi"
    rules = fetch(f"{raw}/inspector_rules.py")
    target = Path(find_spec("sc_crawler.inspector_rules").origin)
    target.write_text(rules)
    print(f"Installed inspector rules from {REPO}@{sha} to {target}.")
    return f"github@{sha}"


if __name__ == "__main__":
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    try:
        source = overlay(ref) if ref else "pypi"
    except Exception as e:
        print(f"Keeping installed inspector rules due to error: {e!r}")
        source = "pypi"
    keeper_dir = Path(find_spec("sc_keeper").origin).parent
    (keeper_dir / "inspector_rules_source.txt").write_text(source)
