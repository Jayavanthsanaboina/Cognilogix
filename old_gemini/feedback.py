"""
Feedback loop agent: Add newly resolved maintenance cases back into ChromaDB.
"""

from typing import Dict, Any, List
import uuid
from datetime import datetime

from kb.retriever import KnowledgeBase

kb = KnowledgeBase()

def log_resolved_case(
    complaint: str,
    equipment_type: str,
    root_cause: str,
    fix_steps: List[str],
    parts_used: List[str],
    cost_inr: int,
    downtime_hours: float,
    urgency: str = "Medium",
    location: str = "Unspecified",
    asset_id: str = "AST-NEW"
) -> Dict[str, Any]:
    """Upsert a newly resolved technician report back into the Knowledge Base."""
    
    new_record_id = f"FB-{uuid.uuid4().hex[:6].upper()}"
    
    new_record = {
        "record_id": new_record_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "asset_id": asset_id,
        "equipment_type": equipment_type,
        "location": location,
        "complaint": complaint,
        "root_cause": root_cause,
        "fix_steps": fix_steps,
        "parts_used": parts_used,
        "cost_inr": cost_inr,
        "downtime_hours": downtime_hours,
        "urgency": urgency,
        "safety_critical": False,
        "prior_failures_on_asset": 0,
        "technician_notes": "Log added via feedback loop.",
        "source": "feedback_loop",
        "language": "en"
    }
    
    # Save into ChromaDB
    kb.add_cases([new_record])
    
    return {
        "status": "success",
        "message": f"Successfully added case {new_record_id} to knowledge base.",
        "total_kb_cases": kb.count(),
        "record": new_record
    }

if __name__ == "__main__":
    print(f"Initial KB Count: {kb.count()}")
    
    res = log_resolved_case(
        complaint="Lift door jerking violently on 3rd floor",
        equipment_type="Elevator / Lift",
        root_cause="Worn guide shoes on elevator door hanger",
        fix_steps=["Isolate elevator power", "Inspect door hanger assembly", "Replace guide shoes", "Re-align tracks"],
        parts_used=["Door guide shoes", "Lubricant"],
        cost_inr=1800,
        downtime_hours=1.5
    )
    
    print(res["message"])
    print(f"Updated KB Count: {res['total_kb_cases']}")