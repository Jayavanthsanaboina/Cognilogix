"""
Backend + dashboard server (step 5: with the technician feedback loop).

Run from the project root (the folder that contains kb/, agents/ and frontend/):

    python -m uvicorn api:app --port 8000

then open http://127.0.0.1:8000 in your browser.
"""
import json
import statistics
import threading
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.llm import MODEL, LLMConfigError
from agents.pipeline import build_graph, equipment_types, get_kb
from kb.config import BASE_DIR, DATA_DIR, QUERY_PREFIX
from kb.retriever import embed

FRONTEND_DIR = BASE_DIR / "frontend"
HISTORY_FILE = DATA_DIR / "history.jsonl"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
MAX_COMPLAINT_CHARS = 1000

CASE_FIELDS = ("record_id", "similarity", "root_cause", "equipment_type", "location",
               "complaint", "cost_inr", "downtime_hours", "urgency", "date")
LANGUAGE_CODES = {"english": "en", "telugu": "te", "hindi": "hi", "hinglish": "hinglish"}
URGENCY_LEVELS = ("Low", "Medium", "High", "Critical")

graph = None
history = []            # every finished diagnosis, oldest first
feedback_records = []   # every confirmed case added to the knowledge base
feedback_by_run = {}    # run id -> short summary, so each diagnosis is rated only once
feedback_lock = threading.Lock()


def load_history():
    if HISTORY_FILE.exists():
        for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            try:
                history.append(json.loads(line))
            except ValueError:
                pass


def load_feedback():
    if FEEDBACK_FILE.exists():
        for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            feedback_records.append(rec)
            feedback_by_run[rec.get("run_id")] = feedback_summary(rec)


def feedback_summary(rec):
    return {"record_id": rec["record_id"], "cause": rec["root_cause"],
            "verdict": rec.get("verdict", "incorrect")}


def save_history(result):
    history.append(result)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


@asynccontextmanager
async def lifespan(app):
    global graph
    graph = build_graph()
    get_kb()
    embed(["warm up"], QUERY_PREFIX)     # loads the embedding model now, so the first request is fast
    load_history()
    load_feedback()
    yield


app = FastAPI(title="Facility decision-support agent", lifespan=lifespan)


class DiagnoseRequest(BaseModel):
    complaint: str
    equipment_hint: Optional[str] = None


class FeedbackRequest(BaseModel):
    run_id: str
    verdict: str                          # "correct" or "incorrect"
    cause: Optional[str] = None           # the real cause, when the verdict is "incorrect"
    equipment_type: Optional[str] = None  # only needed when the agents could not identify it
    cost_inr: Optional[float] = None      # optional: what the repair really cost
    downtime_hours: Optional[float] = None
    fix_steps: Optional[str] = None       # optional: one step per line
    notes: Optional[str] = None


def build_result(state):
    """Keep only what the dashboard needs."""
    return {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now().isoformat(timespec="seconds"),
        "complaint": state["complaint"],
        "language": state.get("language", "English"),
        "english_text": state.get("english_text", ""),
        "equipment_type": state.get("equipment_type"),
        "safety_flag": bool(state.get("safety_flag")),
        "specific": bool(state.get("specific", True)),
        "cases": [{k: c.get(k) for k in CASE_FIELDS} for c in state.get("cases", [])],
        "diagnosis": state["diagnosis"],
        "recommendation": state["recommendation"],
        "explanation": state["explanation"],
        "trace": state["trace"],
    }


def as_line(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def run_stream(complaint, equipment_hint):
    """Run the agent graph and yield one JSON line per finished agent, then the result."""
    state = {"complaint": complaint, "trace": []}
    if equipment_hint:
        state["equipment_hint"] = equipment_hint
    merged = dict(state)
    try:
        for chunk in graph.stream(state, stream_mode="updates"):
            for node, update in chunk.items():
                if not update:
                    continue
                for key, value in update.items():
                    merged[key] = merged["trace"] + value if key == "trace" else value
                entry = (update.get("trace") or [{}])[0]
                yield as_line({"type": "step", "agent": node,
                               "summary": entry.get("summary", ""), "ms": entry.get("ms", 0)})
        result = build_result(merged)
        save_history(result)
        yield as_line({"type": "result", "data": result})
    except LLMConfigError as error:
        yield as_line({"type": "error", "message": str(error)})
    except Exception as error:
        yield as_line({"type": "error", "message": f"The diagnosis failed: {error}"})


# ------------------------------------------------------------------ feedback helpers
def find_run(run_id):
    for r in history:
        if r["id"] == run_id:
            return r
    return None


def past_cases_for(equipment, cause=None):
    """Metadata of the past repairs for one equipment type (and optionally one cause)."""
    where = {"equipment_type": equipment}
    if cause:
        where = {"$and": [{"equipment_type": equipment}, {"root_cause": cause}]}
    got = get_kb().collection.get(where=where, include=["metadatas"])
    return got.get("metadatas") or []


def template_for(equipment, cause):
    """Typical fix, cost and time for a cause that already exists in the knowledge base."""
    metas = past_cases_for(equipment, cause)
    if not metas:
        return None
    first = metas[0]
    return {
        "fix_steps": json.loads(first["fix_steps"]),
        "parts": json.loads(first["parts_used"]),
        "cost": statistics.median(m["cost_inr"] for m in metas),
        "hours": round(statistics.median(m["downtime_hours"] for m in metas), 1),
        "urgency": first["urgency"],
        "safety": bool(first["safety_critical"]),
    }


# ------------------------------------------------------------------ endpoints
@app.get("/api/stats")
def stats():
    return {"kb_cases": get_kb().count(), "model": MODEL,
            "equipment_types": equipment_types(), "runs": len(history)}


@app.get("/api/history")
def list_history():
    items = []
    for r in reversed(history[-50:]):
        d = r["diagnosis"]
        items.append({
            "id": r["id"], "created": r["created"], "complaint": r["complaint"],
            "language": r["language"], "equipment_type": r["equipment_type"],
            "escalated": d["insufficient_evidence"],
            "top_cause": None if d["insufficient_evidence"] else d["ranked"][0]["cause"],
            "urgency": r["recommendation"]["urgency"],
        })
    return items


@app.get("/api/history/{run_id}")
def get_run(run_id: str):
    run = find_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That diagnosis is no longer in the history.")
    return {**run, "feedback": feedback_by_run.get(run_id)}


@app.get("/api/causes")
def causes(equipment: str):
    """Causes already known for one equipment type, most common first."""
    counts = Counter(m["root_cause"] for m in past_cases_for(equipment))
    return [cause for cause, _ in counts.most_common()]


@app.post("/api/diagnose/stream")
def diagnose_stream(req: DiagnoseRequest):
    complaint = req.complaint.strip()
    if not complaint:
        raise HTTPException(status_code=400, detail="Describe the problem first.")
    if len(complaint) > MAX_COMPLAINT_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"Keep the description under {MAX_COMPLAINT_CHARS} characters.")
    return StreamingResponse(run_stream(complaint, req.equipment_hint),
                             media_type="application/x-ndjson")


@app.post("/api/feedback")
def post_feedback(req: FeedbackRequest):
    """Technician confirms or corrects a diagnosis; the confirmed case joins the knowledge base."""
    run = find_run(req.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That diagnosis is no longer in the history.")
    if req.run_id in feedback_by_run:
        raise HTTPException(status_code=409, detail="This diagnosis already has feedback.")

    diagnosis, plan = run["diagnosis"], run["recommendation"]
    weak = diagnosis["insufficient_evidence"]
    top_cause = None if weak or not diagnosis["ranked"] else diagnosis["ranked"][0]["cause"]

    if req.verdict == "correct":
        if not top_cause:
            raise HTTPException(status_code=400,
                                detail="No cause was proposed, so there is nothing to confirm. Enter the real cause.")
        cause = top_cause
    elif req.verdict == "incorrect":
        cause = (req.cause or "").strip()
        if not cause:
            raise HTTPException(status_code=400, detail="Choose or type the real cause.")
    else:
        raise HTTPException(status_code=400, detail="Verdict must be 'correct' or 'incorrect'.")
    if len(cause) > 120:
        raise HTTPException(status_code=400, detail="Keep the cause under 120 characters.")

    equipment = run.get("equipment_type")
    if not equipment:
        equipment = (req.equipment_type or "").strip()
        if equipment not in equipment_types():
            raise HTTPException(status_code=400, detail="Choose the equipment type.")

    # Fill in fix, cost and time: from the plan if the cause is the one proposed,
    # else from past repairs with that cause, else from what the technician typed.
    steps, parts, cost, hours = [], [], None, None
    urgency = plan.get("urgency") or "Medium"
    safety = bool(run.get("safety_flag"))
    if cause == top_cause:
        steps, parts = plan.get("fix_steps") or [], plan.get("parts") or []
        cost = (plan.get("cost_inr") or {}).get("median")
        hours = (plan.get("downtime_hours") or {}).get("median")
        safety = bool(plan.get("safety_critical"))
    else:
        known = template_for(equipment, cause)
        if known:
            steps, parts, cost, hours = known["fix_steps"], known["parts"], known["cost"], known["hours"]
            urgency, safety = known["urgency"], known["safety"]
    if req.cost_inr is not None:
        cost = req.cost_inr
    if req.downtime_hours is not None:
        hours = req.downtime_hours
    typed_steps = [s.strip() for s in (req.fix_steps or "").splitlines() if s.strip()]
    if typed_steps:
        steps = typed_steps[:10]

    if cost is None or hours is None:
        raise HTTPException(status_code=400,
                            detail="This cause is new to the knowledge base. Enter the actual cost and time.")
    if cost < 0 or hours < 0:
        raise HTTPException(status_code=400, detail="Cost and time cannot be negative.")
    if urgency not in URGENCY_LEVELS:
        urgency = "Medium"

    language = str(run.get("language", "English")).lower()
    notes = f"Confirmed by a technician on the dashboard (run {run['id']}). English: {run.get('english_text', '')}"
    if req.notes:
        notes += f" Note: {req.notes.strip()[:300]}"

    with feedback_lock:
        if req.run_id in feedback_by_run:
            raise HTTPException(status_code=409, detail="This diagnosis already has feedback.")
        record = {
            "record_id": f"FB-{len(feedback_records) + 1:04d}",
            "date": date.today().isoformat(),
            "asset_id": "FIELD-REPORT",
            "equipment_type": equipment,
            "location": "Field report",
            "complaint": run["complaint"],
            "root_cause": cause,
            "fix_steps": steps,
            "parts_used": parts,
            "cost_inr": int(round(cost)),
            "downtime_hours": float(hours),
            "urgency": urgency,
            "safety_critical": safety,
            "prior_failures_on_asset": 0,
            "technician_notes": notes,
            "source": "feedback",
            "language": LANGUAGE_CODES.get(language, "en"),
        }
        get_kb().add_cases([record])          # searchable from the very next complaint
        stored = {**record, "run_id": req.run_id, "verdict": req.verdict,
                  "created": datetime.now().isoformat(timespec="seconds")}
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
        feedback_records.append(stored)
        feedback_by_run[req.run_id] = feedback_summary(stored)

    return {"record_id": record["record_id"], "cause": cause, "verdict": req.verdict,
            "kb_cases": get_kb().count()}


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")