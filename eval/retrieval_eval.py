"""
Measure retrieval quality on the holdout set (cases NOT in the knowledge base).

For each holdout complaint we retrieve the top-k cases and check whether they
share the true root cause.

Run from the project root:   python -m eval.retrieval_eval
"""
import json

from kb.config import DATA_DIR
from kb.retriever import KnowledgeBase

K = 5


def main():
    holdout = json.loads((DATA_DIR / "holdout.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase()

    top1 = any_hit = 0
    precision_sum = 0.0
    misses = []
    for rec in holdout:
        cases = kb.search(rec["complaint"], k=K)
        causes = [c["root_cause"] for c in cases]
        match = [c == rec["root_cause"] for c in causes]
        top1 += bool(match and match[0])
        any_hit += any(match)
        precision_sum += sum(match) / max(len(match), 1)
        if not any(match):
            misses.append((rec["complaint"], rec["root_cause"], causes[:1]))

    n = len(holdout)
    print(f"Holdout complaints : {n}")
    print(f"Top-1 accuracy     : {top1 / n:.0%}  (best match has the right cause)")
    print(f"Hit@{K}             : {any_hit / n:.0%}  (right cause appears in the top {K})")
    print(f"Precision@{K}       : {precision_sum / n:.0%}  (share of top {K} with the right cause)")
    if misses:
        print("\nCases with no correct result in the top", K)
        for complaint, truth, got in misses:
            print(f"  - {complaint}\n      expected: {truth}\n      got top1: {got}")


if __name__ == "__main__":
    main()