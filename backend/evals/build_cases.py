"""Turn the searches people already ran into draft evaluation cases.

Every answered search recorded which results the answer actually cited, and a
citation is an implicit relevance label: the model read that document and used
it. That is a free bootstrap for an evaluation set - no hand labelling - and
it is the only labelled data this deployment has.

**It is noisy, and the noise is the reason this script votes rather than
copies.** Asked what the auth-service README says, one run cited the
repository (right) and also `.env` and a weekly progress report (obviously
not). A document cited by one run out of five is a guess; one cited by four
out of five is a label. So identical questions are grouped, each cited
document gets a vote per run, and only those carrying most of the vote are
proposed - with the tally written into the file so a human reviewing it can
see exactly how strong each label is and overrule it.

The output is a *draft*. Nothing here decides what is correct; it decides what
is worth a person looking at.

Usage:

    docker compose exec -T postgres psql -U llmhell -d llmhell -A -t \\
      -c "SELECT json_agg(row_to_json(t))::text FROM (
            SELECT query, citations FROM search_queries
            WHERE citations IS NOT NULL) t;" > raw.json
    python backend/evals/build_cases.py raw.json backend/evals/cases.draft.json
"""

import json
import math
import re
import sys
from collections import Counter, OrderedDict

# A document has to carry at least this share of a question's runs to be
# proposed. Half is deliberate: below it, the citation is as likely to be the
# model reaching for something adjacent as it is to be the answer.
VOTE_SHARE = 0.5

# ...and at least two runs must agree, whatever the share works out to. Half of
# two is one, so a plain share let every citation through on a question asked
# twice - the vote did no work at exactly the sample size where it is needed
# most. Two questions agreeing is the weakest thing worth calling a label.
MIN_VOTES = 2

# Questions asked once have no vote to count, so their citations are proposed
# as they stand and are the ones most in need of review. Flagged, not hidden.
SINGLE_RUN_NOTE = "single run - no vote, review by hand"


def normalise(query: str) -> str:
    """Group the spellings of one question.

    People retype a question with different capitalisation and punctuation,
    and a stray `##` from a pasted prompt should not split a five-run vote
    into a four and a one.
    """
    lowered = query.strip().lower().lstrip("#").strip()
    return re.sub(r"[\s ]+", " ", lowered).rstrip("?!. ")


def build(rows: list[dict]) -> list[dict]:
    groups: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        citations = row.get("citations") or []
        if not citations:
            continue
        key = normalise(row["query"])
        group = groups.setdefault(key, {"query": row["query"].strip(), "runs": 0, "votes": Counter()})
        group["runs"] += 1
        # One vote per document per run, not per citation: an answer that
        # cites the same document three times has not voted three times.
        seen = {
            (citation.get("source") or "", (citation.get("title") or "").strip())
            for citation in citations
            if (citation.get("title") or "").strip()
        }
        for entry in seen:
            group["votes"][entry] += 1

    cases = []
    for group in groups.values():
        runs = group["runs"]
        threshold = 1 if runs == 1 else max(MIN_VOTES, math.ceil(runs * VOTE_SHARE))
        expected = [
            {"source": source, "title": title, "votes": count, "runs": runs}
            for (source, title), count in group["votes"].most_common()
            if count >= threshold
        ]
        rejected = [
            {"source": source, "title": title, "votes": count}
            for (source, title), count in group["votes"].most_common()
            if count < threshold
        ]
        if not expected:
            continue
        case = {
            "query": group["query"],
            "runs": runs,
            "expect": [{"source": e["source"], "title": e["title"]} for e in expected],
            "evidence": {
                "votes": {f"{e['source']}:{e['title']}": e["votes"] for e in expected},
                "outvoted": {f"{r['source']}:{r['title']}": r["votes"] for r in rejected},
            },
        }
        if runs == 1:
            case["review"] = SINGLE_RUN_NOTE
        cases.append(case)

    # Most-run questions first: those carry the strongest labels and are the
    # ones worth reading before the long tail of single runs.
    cases.sort(key=lambda case: (-case["runs"], case["query"]))
    return cases


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as handle:
        rows = json.load(handle)
    cases = build(rows)
    payload = {
        "note": (
            "Draft, mined from recorded citations by build_cases.py. Labels are "
            "implicit and noisy - review `evidence` before trusting a case, and "
            "delete what the answer got wrong."
        ),
        "cases": cases,
    }
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
    multi = sum(1 for case in cases if case["runs"] > 1)
    print(f"{len(cases)} draft cases ({multi} with a real vote) -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
