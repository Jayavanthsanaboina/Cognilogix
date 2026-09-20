"""
Generate the synthetic maintenance dataset.

Outputs (in this folder):
  records.json   - knowledge-base records (loaded into ChromaDB in step 2)
  records.csv    - same data, easy to open in Excel
  holdout.json   - records kept OUT of the knowledge base, used for evaluation

Run:  python generate_dataset.py
"""
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from failure_patterns import PATTERNS

SEED = 42
N_RECORDS = 280
MIN_PER_MODE = 5      # every failure mode gets at least this many records
END_DATE = date(2026, 9, 10)
START_DATE = END_DATE - timedelta(days=730)
OUT_DIR = Path(__file__).parent

BUILDINGS = ["Block A", "Block B", "Block C", "Block D", "Library", "Admin Building",
             "Auditorium", "Cafeteria", "Hostel 1", "Hostel 2", "Hostel 3",
             "Lab Complex", "Sports Complex"]
FLOORS = ["Ground floor", "1st floor", "2nd floor", "3rd floor"]
REPORTERS = ["faculty member", "hostel warden", "lab assistant", "security guard",
             "student", "admin staff", "housekeeping supervisor"]
SINCE = ["yesterday", "this morning", "two days ago", "last week", "the past few hours",
         "last night"]

TEMPLATES_WITH_LOC = [
    "{s} in {loc}.",
    "{loc}: {s}. Please check.",
    "{s}. Happening since {since}. Location: {loc}.",
    "Complaint from {who}: {s} ({loc}).",
    "{s} near {loc}, started {since}.",
]
TEMPLATES_NO_LOC = [
    "{s}.",
    "{s}, started {since}.",
    "{s}. Reported by {who}.",
]
URGENT_TEMPLATE = "Urgent - {s} at {loc}."

NOTE_CLOSERS = [
    "Tested after repair; operating normally.",
    "Verified operation with the user before closing the ticket.",
    "Unit monitored for an hour after repair; no issues.",
    "Closed after a successful test run.",
]


def cap_first(text):
    return text[0].upper() + text[1:]


def build_assets(rng):
    """Create assets per equipment type; a few 'problem assets' fail far more often."""
    assets, weights = {}, {}
    for p in PATTERNS:
        locs = p.get("locations")
        items = []
        for i in range(1, p["n_assets"] + 1):
            if locs:
                loc = locs[(i - 1) % len(locs)]
            else:
                loc = f"{rng.choice(BUILDINGS)}, {rng.choice(FLOORS)}"
            items.append({"asset_id": f"{p['prefix']}-{i:03d}", "location": loc})
        assets[p["prefix"]] = items
        weights[p["prefix"]] = [rng.choice([1, 1, 1, 2, 4]) for _ in items]
    return assets, weights


def build_complaint(rng, mode, location):
    symptoms = mode["symptoms"]
    k = 2 if (len(symptoms) > 1 and rng.random() < 0.3) else 1
    picked = rng.sample(symptoms, k)
    s = cap_first(picked[0])
    if k == 2:
        s += ", also " + picked[1]
    who, since = rng.choice(REPORTERS), rng.choice(SINCE)
    if rng.random() < 0.85:
        options = list(TEMPLATES_WITH_LOC)
        if mode["urgency"] in ("High", "Critical"):
            options.append(URGENT_TEMPLATE)
        template = rng.choice(options)
    else:
        template = rng.choice(TEMPLATES_NO_LOC)
    return template.format(s=s, loc=location, who=who, since=since)


def round_to(value, step):
    return round(value / step) * step


def generate():
    rng = random.Random(SEED)
    assets, asset_weights = build_assets(rng)
    pattern_weights = [p["n_assets"] for p in PATTERNS]
    span_days = (END_DATE - START_DATE).days

    # Guarantee every failure mode has enough examples, then fill the rest
    # in proportion to how many assets each equipment type has (AC dominates).
    picks = [(p, mode) for p in PATTERNS for mode in p["modes"] for _ in range(MIN_PER_MODE)]
    while len(picks) < N_RECORDS:
        p = rng.choices(PATTERNS, weights=pattern_weights)[0]
        picks.append((p, rng.choice(p["modes"])))
    rng.shuffle(picks)

    raw = []
    for p, mode in picks:
        asset = rng.choices(assets[p["prefix"]], weights=asset_weights[p["prefix"]])[0]
        raw.append(dict(
            date=START_DATE + timedelta(days=rng.randint(0, span_days)),
            pattern=p, mode=mode, asset=asset,
        ))
    raw.sort(key=lambda r: r["date"])

    history_any = defaultdict(int)
    history_same = defaultdict(int)
    records = []
    for n, r in enumerate(raw, start=1):
        p, mode, asset = r["pattern"], r["mode"], r["asset"]
        aid, cause = asset["asset_id"], mode["cause"]
        prior_any, prior_same = history_any[aid], history_same[(aid, cause)]

        notes = f"Found {cause[0].lower() + cause[1:]}. {mode['fix'][0]}."
        if prior_same:
            notes += f" Recurring issue - {prior_same} similar failure(s) earlier on this asset."
        notes += " " + rng.choice(NOTE_CLOSERS)

        records.append({
            "record_id": f"MR-{n:04d}",
            "date": r["date"].isoformat(),
            "asset_id": aid,
            "equipment_type": p["equipment"],
            "location": asset["location"],
            "reported_by": rng.choice(REPORTERS),
            "complaint": build_complaint(rng, mode, asset["location"]),
            "root_cause": cause,
            "fix_steps": mode["fix"],
            "parts_used": rng.sample(mode["parts"], rng.randint(1, len(mode["parts"]))),
            "cost_inr": int(round_to(rng.uniform(*mode["cost"]), 50)),
            "downtime_hours": round_to(rng.uniform(*mode["hours"]), 0.5),
            "urgency": mode["urgency"],
            "safety_critical": mode["safety"],
            "prior_failures_on_asset": prior_any,
            "prior_same_cause_on_asset": prior_same,
            "technician_notes": notes,
            "status": "resolved",
            "source": "seed",
        })
        history_any[aid] += 1
        history_same[(aid, cause)] += 1
    return records, rng


def main():
    records, rng = generate()

    # Stratified holdout: exactly one record per failure mode is kept out for evaluation.
    by_cause = defaultdict(list)
    for r in records:
        by_cause[r["root_cause"]].append(r["record_id"])
    holdout_ids = {rng.choice(ids) for ids in by_cause.values()}
    holdout = [r for r in records if r["record_id"] in holdout_ids]
    kb = [r for r in records if r["record_id"] not in holdout_ids]

    (OUT_DIR / "records.json").write_text(json.dumps(kb, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "holdout.json").write_text(json.dumps(holdout, indent=2, ensure_ascii=False), encoding="utf-8")

    with open(OUT_DIR / "records.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(kb[0].keys()))
        writer.writeheader()
        for row in kb:
            row = dict(row)
            row["fix_steps"] = " | ".join(row["fix_steps"])
            row["parts_used"] = ", ".join(row["parts_used"])
            writer.writerow(row)

    causes = Counter(r["root_cause"] for r in kb)
    repeat_assets = Counter(r["asset_id"] for r in kb)
    print(f"Knowledge-base records : {len(kb)}")
    print(f"Holdout (eval) records : {len(holdout)}")
    print(f"Equipment types        : {len(set(r['equipment_type'] for r in kb))}")
    print(f"Distinct root causes   : {len(causes)} "
          f"(min {min(causes.values())}, max {max(causes.values())} records each)")
    print(f"Assets with 4+ failures: {sum(1 for c in repeat_assets.values() if c >= 4)}")
    print("Urgency mix            :", dict(Counter(r["urgency"] for r in kb)))
    print(f"Safety-critical cases  : {sum(r['safety_critical'] for r in kb)}")
    print("\nSample records:")
    for r in kb[:3]:
        print(f"  [{r['record_id']}] {r['equipment_type']} - {r['complaint']}")
        print(f"      cause: {r['root_cause']} | Rs {r['cost_inr']} | {r['downtime_hours']}h | {r['urgency']}")


if __name__ == "__main__":
    main()