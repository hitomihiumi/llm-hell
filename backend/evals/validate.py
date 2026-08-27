"""Check that every document a case expects still exists.

The labels were mined from searches run weeks earlier, and a corpus moves.
Three cases in the first set expected Drive files - `.env`, `TEST`, and the
`Copy of Week N Progress Report` duplicates - that had since been deleted.
Nothing said so: they scored as retrieval failures, the baseline was three
cases too pessimistic, and two rounds of tuning were spent trying to fix a
search that was working correctly.

A stale label is worse than a missing one, because it looks like a bug in the
thing being measured. Run this whenever the corpus might have moved, and
before believing a drop in the score.

    python backend/evals/validate.py [account-email]

Drive only, for now: it is the source the labels are concentrated in and the
one where a file can quietly disappear. A GitLab path that vanishes takes its
whole repository with it and shows up as every case for that repo failing at
once, which is hard to mistake for a ranking problem.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mcp.transport import call_tool, http_session  # noqa: E402

CASES = Path(__file__).with_name("cases.json")
# The container's published port, not the compose hostname: this runs from a
# shell on the host rather than from inside the network.
GOOGLE_MCP = "http://127.0.0.1:3103/mcp"

BACKSLASH = chr(92)
QUOTE = "'"


def escape(term: str) -> str:
    return term.replace(BACKSLASH, BACKSLASH * 2).replace(QUOTE, BACKSLASH + QUOTE)


async def exists(email: str, title: str) -> bool:
    """Whether Drive still holds a file with this name.

    Matched with `contains` rather than `=`, because a title recorded from a
    search result can carry a suffix Drive does not store, and a false
    "missing" would mislead exactly as much as the staleness being looked for.
    """
    query = f"name contains {QUOTE}{escape(title)}{QUOTE} and trashed = false"
    async with http_session(GOOGLE_MCP, {}) as session:
        raw = await call_tool(
            session,
            "manage_drive",
            {"operation": "search", "email": email, "query": query, "pageSize": 5},
            timeout=60,
        )
    # `raw.text`, not `raw.payload()`: this server answers in Markdown,
    # so the payload ladder finds neither JSON nor a Python literal, logs that
    # it could not read it, and returns None - which made every lookup succeed.
    return "No files found" not in raw.text


async def main_async() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "vlad@borzo.ai"
    with open(CASES, encoding="utf-8") as handle:
        data = json.load(handle)

    wanted = sorted(
        {
            expected["title"]
            for case in data["cases"]
            for expected in case["expect"]
            if expected["source"] == "google_drive"
        }
    )

    missing = []
    for title in wanted:
        # A Drive id used as a title has no name to look up; the leading run is
        # enough to find it and short enough not to trip Drive's query length.
        probe = title[:16] if re.fullmatch(r"[0-9a-f]{16,}.*", title) else title
        found = await exists(email, probe)
        if not found:
            missing.append(title)
        sys.stdout.buffer.write(
            (("  ok      " if found else "  MISSING ") + title[:70] + "\n").encode("utf-8")
        )

    print(f"\n{len(wanted) - len(missing)}/{len(wanted)} Drive documents still exist")
    if missing:
        print("\nCases expecting these score as retrieval failures and are not:")
        for title in missing:
            for case in data["cases"]:
                if any(expected["title"] == title for expected in case["expect"]):
                    sys.stdout.buffer.write(
                        f"  {title[:38]:40} <- {case['query'][:48]}\n".encode()
                    )
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main_async())
