"""Score the answer, not the retrieval.

Everything else in this directory runs with `answer: false` and asks whether
the right documents came back. Nobody has ever measured what happens next -
and the user sees the answer, not the result list. A search that retrieves
the datasheet at rank 1 and then replies "the provided documents contain no
information about that" is a total failure that every retrieval metric here
scores as a success.

That failure is what this measures, and it needs no judge model to do it. The
labels already say which documents answer each question; the API already
reports which documents the answer cited. Comparing the two gives:

    cited        the answer cited a document the case expected - it used the
                 right source, whatever it then said about it
    ignored      an expected document was retrieved and the answer cited
                 none of them. This is the answer stage failing on its own,
                 with retrieval having done its job, and it is invisible to
                 every other metric in this directory
    hallucinated citation numbers the model invented, which the API already
                 counts and drops

Costs one answer per case, so this bills real tokens - unlike `run.py`, which
deliberately does not.

    python backend/evals/run_answers.py --out answers.json
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


def ask(base: str, key: dict, query: str, limit: int) -> dict:
    request = urllib.request.Request(
        f"{base}/api/search",
        data=json.dumps({"query": query, "limit": limit, "answer": True}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": key["cookie"],
            "X-CSRF-Token": key["csrf"],
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def matches(source: str, title: str, expected: dict) -> bool:
    """Same rule the retrieval runner uses, so the two scores are comparable."""
    if source != expected["source"]:
        return False
    want = expected["title"].strip()
    title = (title or "").strip()
    return title == want or title.startswith(f"{want}/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("LLMHELL_BASE", "http://localhost:8000"))
    parser.add_argument("--user", default=os.environ.get("LLMHELL_USER", "demo"))
    parser.add_argument("--password", default=os.environ.get("LLMHELL_PASSWORD", "demo-2026"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    with open(CASES, encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    key = sign_in(args.base, args.user, args.password)

    rows = []
    started = time.monotonic()
    for case in cases:
        try:
            payload = ask(args.base, key, case["query"], args.limit)
        except (urllib.error.URLError, TimeoutError) as exc:
            rows.append({"query": case["query"], "error": str(exc)})
            continue

        answer = payload.get("answer") or {}
        hits = payload.get("hits") or []
        citations = answer.get("citations") or []

        retrieved = any(
            matches(hit.get("source", ""), hit.get("title", ""), expected)
            for hit in hits
            for expected in case["expect"]
        )
        cited = any(
            matches(citation.get("source", ""), citation.get("title", ""), expected)
            for citation in citations
            for expected in case["expect"]
        )
        rows.append(
            {
                "query": case["query"],
                "retrieved": retrieved,
                "cited": cited,
                # Retrieval did its job and the answer did not use it. The one
                # failure every other metric here scores as a success.
                "ignored": retrieved and not cited,
                "citations": len(citations),
                "hallucinated": answer.get("hallucinated_citations", 0),
                "hits_used": answer.get("hits_used"),
                "hits_dropped": answer.get("hits_dropped"),
                "answer_chars": len(answer.get("text") or ""),
                "model": answer.get("model"),
            }
        )

    scored = [row for row in rows if "error" not in row]
    if not scored:
        raise SystemExit("no case produced an answer")

    retrieved = [row for row in scored if row["retrieved"]]
    summary = {
        "label": args.label,
        "cases": len(scored),
        "retrieved_the_answer": len(retrieved),
        "cited_the_answer": sum(1 for row in scored if row["cited"]),
        # The headline. Denominator is cases where retrieval succeeded,
        # because a case that retrieved nothing cannot have its answer blamed.
        "ignored_what_it_retrieved": sum(1 for row in retrieved if row["ignored"]),
        "ignored_rate": round(
            sum(1 for row in retrieved if row["ignored"]) / len(retrieved), 3
        )
        if retrieved
        else None,
        "hallucinated_citations": sum(row["hallucinated"] for row in scored),
        "cases_with_no_citations": sum(1 for row in scored if row["citations"] == 0),
        "median_answer_chars": statistics.median(row["answer_chars"] for row in scored),
        "total_hits_dropped": sum(row["hits_dropped"] or 0 for row in scored),
        "seconds": round(time.monotonic() - started, 1),
    }

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2) + "\n")

    print(f"cases                 {summary['cases']}")
    print(f"retrieved the answer  {summary['retrieved_the_answer']}")
    print(f"cited the answer      {summary['cited_the_answer']}")
    print(f"IGNORED what it found {summary['ignored_what_it_retrieved']}  (rate {summary['ignored_rate']})")
    print(f"no citations at all   {summary['cases_with_no_citations']}")
    print(f"hallucinated cites    {summary['hallucinated_citations']}")
    print(f"hits dropped (total)  {summary['total_hits_dropped']}")
    print(f"median answer chars   {summary['median_answer_chars']}")
    print(f"took                  {summary['seconds']}s")

    ignored = [row for row in scored if row["ignored"]]
    if ignored:
        print("\nretrieved but not cited:")
        for row in ignored:
            sys.stdout.buffer.write(
                f"  {row['citations']} cites, {row['answer_chars']:>5} chars  {row['query'][:56]}\n".encode()
            )


if __name__ == "__main__":
    main()
