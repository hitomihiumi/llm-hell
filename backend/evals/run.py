"""Run the evaluation set against a live deployment and score the retrieval.

Deliberately not a pytest: this needs the whole stack up - Drive, GitLab, the
MCP servers, the planner's model - and something that fails because a
container is down is not a test failure. It is a measurement, run on purpose,
against whatever is currently deployed.

**What it measures is retrieval, not the answer.** `answer: false`, so no
model writes prose and nothing is billed for synthesis. The question is only
ever "did the documents that answer this question come back, and how far up".

    MRR      1.0 means the first expected document was the top hit, 0.5 the
             second, and so on. This is the number that moves when ranking
             improves, and the one that would have caught `package.json`
             ranking above the README it was asked about.
    recall@k did any expected document make the top k at all - a retrieval
             failure rather than a ranking one
    noise    the share of returned hits that no case expected. The recorded
             searches put this around 83%, and it is what everything below
             rank 5 costs the answer prompt.

Usage:

    LLMHELL_KEY=... python backend/evals/run.py [--limit 20] [--out report.json]

Comparing two configurations means running it twice and diffing the report -
there is no baseline stored here, because a baseline that is not re-measured
against the same corpus lies quietly.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).with_name("cases.json")
RECALL_AT = (1, 3, 5, 10)


def search(base: str, key: str, query: str, limit: int) -> tuple[list[dict], list[dict]]:
    body = {"query": query, "limit": limit, "answer": False}
    request = urllib.request.Request(
        f"{base}/api/search",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Cookie": key["cookie"], "X-CSRF-Token": key["csrf"]},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)
    return payload.get("hits") or [], payload.get("source_status") or []


def enabled_sources(base: str, key: dict) -> set[str]:
    """Which sources this deployment will actually search.

    Without this the score silently punishes cases whose documents live in a
    source an admin switched off - two of the first run's five "found nothing"
    were `postgres_kb`, which was disabled. Worse, turning a source back on
    would then look like a retrieval improvement.
    """
    request = urllib.request.Request(
        f"{base}/api/sources", headers={"Cookie": key["cookie"]}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return {row["key"] for row in json.load(response) if row.get("enabled")}


def sign_in(base: str, username: str, password: str) -> dict:
    request = urllib.request.Request(
        f"{base}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    session = csrf = None
    with urllib.request.urlopen(request, timeout=30) as response:
        for name, value in response.getheaders():
            if name.lower() != "set-cookie":
                continue
            if value.startswith("kb_session="):
                session = value.split(";")[0].split("=", 1)[1]
            if value.startswith("kb_csrf="):
                csrf = value.split(";")[0].split("=", 1)[1]
    if not session:
        raise SystemExit("sign-in returned no session cookie")
    return {"cookie": f"kb_session={session}; kb_csrf={csrf}", "csrf": csrf}


def matches(hit: dict, expected: dict) -> bool:
    """Whether a returned hit is one the case was expecting.

    Matched on source and title rather than on the hit id, because an id is
    unique within one response only - `SearchHit.id` says so - and a case file
    keyed on it would silently stop matching anything the next time the corpus
    is reindexed.
    """
    if hit.get("source") != expected["source"]:
        return False
    title = (hit.get("title") or "").strip()
    want = expected["title"].strip()
    # A GitLab code hit is titled `group/project/path`, so a case naming the
    # repository matches the files inside it too.
    return title == want or title.startswith(f"{want}/")


def score(hits: list[dict], expect: list[dict]) -> dict:
    ranks = []
    for wanted in expect:
        for position, hit in enumerate(hits, start=1):
            if matches(hit, wanted):
                ranks.append(position)
                break
    best = min(ranks) if ranks else None
    hit_is_expected = [any(matches(hit, wanted) for wanted in expect) for hit in hits]
    return {
        "found": len(ranks),
        "of": len(expect),
        "best_rank": best,
        "rr": 1.0 / best if best else 0.0,
        "returned": len(hits),
        "useful": sum(hit_is_expected),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("LLMHELL_BASE", "http://localhost:8000"))
    parser.add_argument("--user", default=os.environ.get("LLMHELL_USER", "demo"))
    parser.add_argument("--password", default=os.environ.get("LLMHELL_PASSWORD", "demo-2026"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", default="")
    parser.add_argument("--label", default="", help="what configuration this run is measuring")
    args = parser.parse_args()

    with open(CASES, encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    key = sign_in(args.base, args.user, args.password)
    live = enabled_sources(args.base, key)

    rows = []
    started = time.monotonic()
    for case in cases:
        try:
            hits, payload_status = search(args.base, key, case["query"], args.limit)
        except (urllib.error.URLError, TimeoutError) as exc:
            rows.append({"query": case["query"], "error": str(exc), "rr": 0.0, "found": 0,
                         "of": len(case["expect"]), "best_rank": None, "returned": 0, "useful": 0})
            continue
        # The semantic index serves other sources' documents, so its coverage
        # counts as reachability: with the lexical connectors switched off,
        # every case would otherwise be scored unreachable and the run would
        # have nothing to average.
        for status in payload_status:
            for covered in status.get("detail", {}).get("indexed_sources", []):
                live.add(covered)
        wanted = {expected["source"] for expected in case["expect"]}
        rows.append(
            {
                "query": case["query"],
                # Not "did it fail" but "could it have succeeded": a case whose
                # documents all live in a disabled source is unanswerable, and
                # counting it as a miss makes the score a fact about the
                # configuration rather than about the search.
                "reachable": bool(wanted & live),
                **score(hits, case["expect"]),
            }
        )

    scored = [row for row in rows if row["reachable"]]
    if not scored:
        raise SystemExit(
            "no case could be scored - every expected source is disabled and not "
            "covered by the semantic index. Enable a source, or index one."
        )
    unreachable = len(rows) - len(scored)
    found_any = [row for row in scored if row["best_rank"]]
    returned = sum(row["returned"] for row in scored)
    useful = sum(row["useful"] for row in scored)
    summary = {
        "label": args.label,
        "enabled_sources": sorted(live),
        "cases": len(scored),
        "skipped_unreachable": unreachable,
        "mrr": round(statistics.fmean(row["rr"] for row in scored), 3),
        "recall": {
            f"@{k}": round(sum(1 for row in scored if row["best_rank"] and row["best_rank"] <= k) / len(scored), 3)
            for k in RECALL_AT
        },
        "found_all_expected": sum(1 for row in scored if row["found"] == row["of"]),
        "found_nothing": len(scored) - len(found_any),
        "median_best_rank": statistics.median([row["best_rank"] for row in found_any]) if found_any else None,
        "noise": round(1 - useful / returned, 3) if returned else None,
        "seconds": round(time.monotonic() - started, 1),
    }

    report = {"summary": summary, "cases": rows}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")

    print(f"cases            {summary['cases']}"
          f"   (skipped {summary['skipped_unreachable']} needing a disabled source)")
    print(f"MRR              {summary['mrr']}")
    for k in RECALL_AT:
        print(f"recall@{k:<9} {summary['recall'][f'@{k}']}")
    print(f"all expected     {summary['found_all_expected']}/{summary['cases']}")
    print(f"found nothing    {summary['found_nothing']}")
    print(f"median rank      {summary['median_best_rank']}")
    print(f"noise            {summary['noise']}")
    print(f"took             {summary['seconds']}s")

    worst = sorted(scored, key=lambda row: (row["rr"], -row["of"]))[:8]
    print("\nworst cases:")
    for row in worst:
        where = row["best_rank"] or "-"
        sys.stdout.buffer.write(
            f"  rank {str(where):>3}  {row['found']}/{row['of']}  {row['query'][:64]}\n".encode()
        )


if __name__ == "__main__":
    main()
