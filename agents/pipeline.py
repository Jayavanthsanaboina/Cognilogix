"""
Multi-agent pipeline (LangGraph).

  intake -> retrieve -> [enough evidence?] -- yes --> diagnose -> plan_fix -> explain
                                |
                                +------------ no  --> escalate ------------> explain

Public entry point:  diagnose_complaint(text, equipment_hint=None)

Design rules:
  * The LLM never invents numbers. Cost, time, urgency and fix steps are computed
    from past cases; the LLM only translates, re-ranks and explains.
  * If past cases do not match well, the system says so and escalates to a human.
"""
import difflib
import json
import math
import operator
import statistics
import time
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from kb.retriever import KnowledgeBase
from .llm import LLMConfigError, chat_json

# ------------------------------------------------------------------ settings
TOP_K = 6                   # similar cases retrieved per complaint
SIM_TEMPERATURE = 0.02      # smaller = the best match dominates the cause vote more
ESCALATE_BELOW_SHARE = 0.25  # leading cause holds less than this share of the evidence -> escalate
LOW_CONFIDENCE_BELOW = 0.60  # below this share the answer is flagged "verify on site first"
LLM_WEIGHT = 0.6             # how much the LLM's judgement counts vs. the case evidence
URGENCY_ORDER = ["Low", "Medium", "High", "Critical"]


# ------------------------------------------------------------- knowledge base
@lru_cache(maxsize=1)
def get_kb():
    return KnowledgeBase()


def equipment_types():
    metas = get_kb().collection.get(include=["metadatas"])["metadatas"]
    return sorted({m["equipment_type"] for m in metas})


def cases_for_cause(cause):
    """Every case in the knowledge base with this root cause (used for cost/time stats)."""
    result = get_kb().collection.get(where={"root_cause": cause}, include=["metadatas"])
    cases = []
    for meta in result["metadatas"]:
        case = dict(meta)
        case["fix_steps"] = json.loads(case["fix_steps"])
        case["parts_used"] = json.loads(case["parts_used"])
        cases.append(case)
    return cases


# ---------------------------------------------------------------------- state
class PipelineState(TypedDict, total=False):
    complaint: str
    equipment_hint: str
    language: str
    english_text: str
    equipment_type: str
    symptoms: list
    safety_flag: bool
    specific: bool
    low_confidence: bool
    cases: list
    candidates: list
    evidence_ok: bool
    diagnosis: dict
    recommendation: dict
    explanation: dict
    trace: Annotated[list, operator.add]   # each agent appends one entry


# -------------------------------------------------------------------- helpers
def _entry(agent, summary, started):
    return {"agent": agent, "summary": summary, "ms": int((time.time() - started) * 1000)}


def _closest(name, options):
    """Match a name to one of the options, ignoring case and small typos."""
    lowered = {str(o).lower(): o for o in options}
    key = str(name or "").strip().lower()
    if key in lowered:
        return lowered[key]
    if key:   # e.g. "Elevator / Lift" should match "Elevator"
        for low, original in lowered.items():
            if low in key or (len(key) >= 4 and key in low):
                return original
    match = difflib.get_close_matches(key, list(lowered), n=1, cutoff=0.8)
    return lowered[match[0]] if match else None


def _stats(values, digits=0):
    median = round(statistics.median(values), digits)
    return {"min": min(values), "median": int(median) if digits == 0 else median, "max": max(values)}


def _urgency_rank(urgency):
    return URGENCY_ORDER.index(urgency) if urgency in URGENCY_ORDER else 0


def _build_candidates(cases):
    """Group retrieved cases by root cause and give each cause an evidence share."""
    if not cases:
        return []
    top_sim = max(c["similarity"] for c in cases)
    weights, grouped = defaultdict(float), defaultdict(list)
    for c in cases:
        weights[c["root_cause"]] += math.exp((c["similarity"] - top_sim) / SIM_TEMPERATURE)
        grouped[c["root_cause"]].append(c)
    total = sum(weights.values())
    candidates = []
    for cause, weight in weights.items():
        ordered = sorted(grouped[cause], key=lambda c: -c["similarity"])
        candidates.append({
            "cause": cause,
            "vote_share": weight / total,
            "cases": [{"record_id": c["record_id"], "similarity": c["similarity"],
                       "complaint": c["complaint"], "technician_notes": c["technician_notes"],
                       "safety_critical": c["safety_critical"]} for c in ordered],
        })
    candidates.sort(key=lambda c: -c["vote_share"])
    return candidates


# -------------------------------------------------------------------- prompts
def _intake_prompt(types):
    return (
        "You are the intake agent of a campus facility maintenance system. You receive one "
        "maintenance complaint, possibly written in English, Hindi, Telugu, Hinglish or another "
        "language. Reply with a single JSON object with exactly these keys: "
        '"language" (the language the complaint is written in, e.g. "English", "Hindi", "Telugu"; '
        'use "Hinglish" when Hindi is written in Roman/English letters), '
        '"english_text" (a faithful English translation that keeps every technical detail; the same text if it is already English), '
        f'"equipment_type" (exactly one of: {", ".join(types)}, or "Unknown"), '
        '"symptoms" (a list of short English symptom phrases), '
        '"safety_flag" (true if the complaint mentions any risk of electric shock, fire, smoke, '
        "burning smell, sparks, gas, people trapped or structural danger; otherwise false), "
        '"specific_enough" (true if the complaint describes a concrete problem with some equipment; '
        'false if it is vague, such as "something is wrong", with no equipment or symptom).'
    )


DIAGNOSE_SYSTEM = (
    "You are the diagnosis agent of a facility maintenance system. You receive a new complaint and "
    "CANDIDATE root causes taken from similar past maintenance cases. Rank the candidates by how well "
    "they explain the new complaint. Use ONLY the candidates provided, copy each cause text exactly, "
    "and never invent a cause. Base your judgement on the case evidence. Reply with a single JSON "
    'object: "ranked" (a list of objects with "cause", "confidence" from 0 to 1, and "reasoning" of '
    'one or two sentences that cites case IDs) and "check_first" (one short sentence: what the '
    "technician should verify on site to tell the top causes apart)."
)


def _explain_prompt(language):
    return (
        "You are the explainer agent of a facility maintenance system. Explain the diagnosis to a "
        f"non-technical facility manager in plain language. Write every text value in {language}, "
        "using the same script the user wrote in (for Hinglish, use Roman letters). Use only the "
        "facts provided; never invent numbers, causes or case IDs. Money is in Indian rupees. "
        f"Use only {language}: do not mix in words from any other language. "
        "The main cause you state MUST be primary_cause, the first item of top_causes; other "
        "causes may only be mentioned as alternatives. "
        "If low_confidence is true, say that several causes are possible, name the top two, and "
        "say the technician must verify on site first (use verify_first and action). "
        "Never write field names such as primary_cause, low_confidence or verify_first in the text. "
        'Reply with a single JSON object: "summary" (one or two sentences: the likely cause and what '
        'to do), "reasoning_chain" (a list of 3 to 5 short steps: what was reported, which past cases '
        "matched, why this cause, and the recommended fix with cost, time and urgency), "
        '"safety_note" (a short safety warning if safety_critical is true, otherwise an empty string). '
        "If insufficient_evidence is true, say clearly that past cases do not match well; follow "
        "the action given (ask for more details or send a technician)."
    )


# ---------------------------------------------------------------------- nodes
def intake(state):
    started = time.time()
    complaint = state["complaint"].strip()
    types = equipment_types()
    note = ""
    try:
        out = chat_json(_intake_prompt(types), complaint)
    except LLMConfigError:
        raise
    except Exception as error:
        out, note = {}, f" (LLM unavailable, used raw text: {str(error)[:60]})"
    language = str(out.get("language") or "English")
    english = str(out.get("english_text") or complaint)
    equipment = _closest(state.get("equipment_hint") or out.get("equipment_type"), types)
    symptoms = out.get("symptoms")
    symptoms = [str(s) for s in symptoms if s] if isinstance(symptoms, list) else []
    safety = bool(out.get("safety_flag"))
    specific = out.get("specific_enough")
    specific = True if specific is None else (specific if isinstance(specific, bool)
                                              else str(specific).strip().lower() != "false")
    summary = (f"{language}; equipment: {equipment or 'unknown'}; "
               f"safety flag: {'yes' if safety else 'no'}; "
               f"specific: {'yes' if specific else 'no'}{note}")
    return {"language": language, "english_text": english, "equipment_type": equipment,
            "symptoms": symptoms, "safety_flag": safety, "specific": specific,
            "trace": [_entry("intake", summary, started)]}


def retrieve(state):
    started = time.time()
    kb = get_kb()
    query, equipment = state["english_text"], state.get("equipment_type")
    cases = kb.search(query, k=TOP_K, equipment_type=equipment)
    if len(cases) < 3 and equipment:          # filter too narrow: search everything
        cases = kb.search(query, k=TOP_K)
    candidates = _build_candidates(cases)
    share = candidates[0]["vote_share"] if candidates else 0.0
    ok = bool(cases) and state.get("specific", True) and share >= ESCALATE_BELOW_SHARE
    low_confidence = share < LOW_CONFIDENCE_BELOW
    if cases:
        summary = (f"{len(cases)} similar cases; best match {cases[0]['similarity']:.2f}; "
                   f"leading cause holds {candidates[0]['vote_share']:.0%} of the evidence")
    else:
        summary = "no similar cases found"
    return {"cases": cases, "candidates": candidates, "evidence_ok": ok,
            "low_confidence": low_confidence,
            "trace": [_entry("retrieve", summary, started)]}


def route_after_retrieve(state):
    return "diagnose" if state["evidence_ok"] else "escalate"


def diagnose(state):
    started = time.time()
    candidates = state["candidates"][:4]
    payload = {
        "complaint_english": state["english_text"],
        "symptoms": state.get("symptoms", []),
        "candidates": [{
            "cause": c["cause"], "evidence_share": round(c["vote_share"], 2),
            "similar_cases": [{"id": x["record_id"], "similarity": x["similarity"],
                               "complaint": x["complaint"],
                               "technician_notes": x["technician_notes"]} for x in c["cases"][:3]],
        } for c in candidates],
    }
    note = ""
    try:
        out = chat_json(DIAGNOSE_SYSTEM, json.dumps(payload, ensure_ascii=False))
    except LLMConfigError:
        raise
    except Exception:
        out, note = {}, " (LLM unavailable, ranked by case evidence only)"

    by_cause = {c["cause"]: c for c in candidates}
    llm = {}
    ranked_out = out.get("ranked")
    for item in ranked_out if isinstance(ranked_out, list) else []:
        if not isinstance(item, dict):
            continue
        name = _closest(item.get("cause"), by_cause)
        if name and name not in llm:
            try:
                conf = min(max(float(item.get("confidence")), 0.0), 1.0)
            except (TypeError, ValueError):
                conf = None
            llm[name] = {"confidence": conf, "reasoning": str(item.get("reasoning") or "")}

    # Final score = case evidence blended with the LLM's judgement, normalised to sum to 1.
    scored = []
    for c in candidates:
        conf = llm.get(c["cause"], {}).get("confidence")
        score = c["vote_share"] if conf is None else (1 - LLM_WEIGHT) * c["vote_share"] + LLM_WEIGHT * conf
        scored.append((score, c))
    scored.sort(key=lambda pair: -pair[0])
    total = sum(score for score, _ in scored) or 1.0
    ranked = [{
        "cause": c["cause"],
        "confidence": round(score / total, 2),
        "evidence_share": round(c["vote_share"], 2),
        "reasoning": (llm.get(c["cause"], {}).get("reasoning")
                      or f"{len(c['cases'])} of the retrieved similar cases had this cause."),
        "supporting_cases": [x["record_id"] for x in c["cases"]],
    } for score, c in scored]
    check_first = str(out.get("check_first") or "")
    low = bool(state.get("low_confidence"))
    summary = (f"top cause: {ranked[0]['cause']} ({ranked[0]['confidence']:.0%})"
               f"{' [low confidence: verify on site]' if low else ''}{note}")
    return {"diagnosis": {"insufficient_evidence": False, "low_confidence": low,
                          "ranked": ranked, "check_first": check_first},
            "trace": [_entry("diagnose", summary, started)]}


def plan_fix(state):
    started = time.time()
    cause = state["diagnosis"]["ranked"][0]["cause"]
    retrieved = [c for c in state["cases"] if c["root_cause"] == cause]
    history = cases_for_cause(cause) or retrieved
    steps_source = retrieved[0] if retrieved else history[0]
    urgency = Counter(c["urgency"] for c in history).most_common(1)[0][0]
    safety = bool(state.get("safety_flag")) or any(c["safety_critical"] for c in history)
    if safety and _urgency_rank(urgency) < _urgency_rank("High"):
        urgency = "High"
    parts = [p for p, _ in Counter(p for c in history for p in c["parts_used"]).most_common(3)]
    diagnosis = state["diagnosis"]
    low = bool(diagnosis.get("low_confidence"))
    action = ""
    if low:
        action = "Verify the cause on site before repairing"
        if diagnosis.get("check_first"):
            action += ": " + diagnosis["check_first"]
    recommendation = {
        "root_cause": cause,
        "action": action,
        "provisional": low,
        "fix_steps": steps_source["fix_steps"],
        "parts": parts,
        "cost_inr": _stats([c["cost_inr"] for c in history]),
        "downtime_hours": _stats([c["downtime_hours"] for c in history], digits=1),
        "urgency": urgency,
        "safety_critical": safety,
        "based_on_cases": len(history),
    }
    summary = (f"median Rs {recommendation['cost_inr']['median']}, "
               f"{recommendation['downtime_hours']['median']} h, urgency {urgency}, "
               f"based on {len(history)} past cases")
    return {"recommendation": recommendation, "trace": [_entry("plan_fix", summary, started)]}


def escalate(state):
    started = time.time()
    candidates = state.get("candidates", [])
    ranked = [{
        "cause": c["cause"], "confidence": 0.0,   # no trustworthy confidence when evidence is weak
        "evidence_share": round(c["vote_share"], 2),
        "reasoning": "Weak match with past cases; shown only as a possible lead.",
        "supporting_cases": [x["record_id"] for x in c["cases"]],
    } for c in candidates[:3]]
    safety = bool(state.get("safety_flag"))   # weak matches say nothing reliable about safety
    urgency = "High" if safety else "Medium"
    if state.get("specific", True):
        action = "Escalate to a senior technician for on-site inspection"
    else:
        action = ("Ask the reporter which equipment is affected and what exactly happens, "
                  "then send a technician to inspect")
    recommendation = {
        "root_cause": None,
        "action": action,
        "provisional": True,
        "fix_steps": [], "parts": [], "cost_inr": None, "downtime_hours": None,
        "urgency": urgency, "safety_critical": safety, "based_on_cases": 0,
    }
    return {"diagnosis": {"insufficient_evidence": True, "low_confidence": True,
                          "ranked": ranked, "check_first": ""},
            "recommendation": recommendation,
            "trace": [_entry("escalate", "evidence too weak; sent to a human technician", started)]}


def _fallback_explanation(state):
    diag, rec = state["diagnosis"], state["recommendation"]
    safety_note = ("Safety-critical: keep people away from the equipment until a technician "
                   "has checked it." if rec.get("safety_critical") else "")
    if diag["insufficient_evidence"]:
        return {"summary": "Past cases do not match this complaint well, so a technician "
                           "must inspect it on site.",
                "reasoning_chain": ["The complaint was compared with past maintenance cases.",
                                    "No cause was supported strongly enough by similar cases.",
                                    "Escalate to a senior technician for on-site inspection."],
                "safety_note": safety_note}
    top, cost = diag["ranked"][0], rec["cost_inr"]
    caution = ([f"Several causes are possible, so verify on site first. {diag['check_first']}".strip()]
               if diag.get("low_confidence") else [])
    return {"summary": f"Most likely cause: {top['cause']}.",
            "reasoning_chain": caution + [
                f"{len(top['supporting_cases'])} similar past cases point to this cause "
                f"({', '.join(top['supporting_cases'][:3])}).",
                f"Recommended fix: {'; '.join(rec['fix_steps'][:3])}.",
                f"Typical cost Rs {cost['min']}-{cost['max']}, about "
                f"{rec['downtime_hours']['median']} hours, urgency {rec['urgency']}."],
            "safety_note": safety_note}


def explain(state):
    started = time.time()
    diag, rec = state["diagnosis"], state["recommendation"]
    facts = {
        "complaint_english": state["english_text"],
        "insufficient_evidence": diag["insufficient_evidence"],
        "low_confidence": diag.get("low_confidence", False),
        "primary_cause": None if diag["insufficient_evidence"] else diag["ranked"][0]["cause"],
        "top_causes": [{k: r[k] for k in ("cause", "confidence", "reasoning", "supporting_cases")}
                       for r in diag["ranked"][:3]],
        "verify_first": diag.get("check_first", ""),
        "recommendation": {k: rec.get(k) for k in ("fix_steps", "parts", "cost_inr",
                                                   "downtime_hours", "urgency",
                                                   "safety_critical", "action")},
    }
    note = ""
    try:
        out = chat_json(_explain_prompt(state.get("language", "English")),
                        json.dumps(facts, ensure_ascii=False))
        chain = out.get("reasoning_chain")
        explanation = {"summary": str(out.get("summary") or ""),
                       "reasoning_chain": [str(s) for s in chain] if isinstance(chain, list) else [],
                       "safety_note": str(out.get("safety_note") or "")}
        if not explanation["summary"] or not explanation["reasoning_chain"]:
            raise ValueError("incomplete explanation")
    except LLMConfigError:
        raise
    except Exception:
        explanation, note = _fallback_explanation(state), " (template fallback)"
    return {"explanation": explanation,
            "trace": [_entry("explain", f"written in {state.get('language', 'English')}{note}", started)]}


# ---------------------------------------------------------------------- graph
def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("intake", intake)
    graph.add_node("retrieve", retrieve)
    graph.add_node("diagnose", diagnose)
    graph.add_node("plan_fix", plan_fix)
    graph.add_node("escalate", escalate)
    graph.add_node("explain", explain)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "retrieve")
    graph.add_conditional_edges("retrieve", route_after_retrieve,
                                {"diagnose": "diagnose", "escalate": "escalate"})
    graph.add_edge("diagnose", "plan_fix")
    graph.add_edge("plan_fix", "explain")
    graph.add_edge("escalate", "explain")
    graph.add_edge("explain", END)
    return graph.compile()


_graph = None


def diagnose_complaint(complaint, equipment_hint=None):
    """Run one complaint through all agents and return the full final state."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    state = {"complaint": complaint, "trace": []}
    if equipment_hint:
        state["equipment_hint"] = equipment_hint
    return _graph.invoke(state)