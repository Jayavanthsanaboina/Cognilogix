"""
Build the knowledge base from data/records.json, then re-add the cases
that technicians confirmed through the dashboard (data/feedback.jsonl).

Run from the project root:   python -m kb.build_kb
"""
import json

from .config import DATA_DIR
from .retriever import KnowledgeBase


def load_feedback():
    path = DATA_DIR / "feedback.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    return records


def main():
    records = json.loads((DATA_DIR / "records.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase(reset=True)
    print(f"Embedding {len(records)} records (the first run downloads the model)...")
    kb.add_cases(records)

    confirmed = load_feedback()
    if confirmed:
        print(f"Re-adding {len(confirmed)} cases confirmed by technicians...")
        kb.add_cases(confirmed)

    print(f"Done. Knowledge base now holds {kb.count()} cases.")


if __name__ == "__main__":
    main()