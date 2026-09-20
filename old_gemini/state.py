"""
State definition for the Facility Maintenance Agent graph.
"""

from typing import TypedDict, List, Dict, Any, Optional

class AgentState(TypedDict):
    complaint: str
    equipment_type: Optional[str]
    retrieved_cases: List[Dict[str, Any]]
    diagnosis: Dict[str, Any]
    feedback_added: bool