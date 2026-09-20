"""
LangGraph Workflow Definition for Maintenance Triage & Diagnosis.
"""

import json
from typing import Dict, Any
from langgraph.graph import StateGraph, END

from kb.retriever import KnowledgeBase
from agents.llm import get_llm
from agents.state import AgentState

# Load knowledge base & LLM instances
kb = KnowledgeBase()
llm = get_llm()

def retrieve_node(state: AgentState) -> Dict[str, Any]:
    """Search vector database for past maintenance cases."""
    complaint = state["complaint"]
    equipment = state.get("equipment_type")
    
    cases = kb.search(query=complaint, k=3, equipment_type=equipment)
    return {"retrieved_cases": cases}

def diagnose_node(state: AgentState) -> Dict[str, Any]:
    """Generate structured maintenance guidance using Groq LLM."""
    complaint = state["complaint"]
    cases = state.get("retrieved_cases", [])
    
    # Format retrieved cases for prompt context
    context = ""
    for idx, c in enumerate(cases, 1):
        context += f"\nCase {idx} (Similarity: {c['similarity']}):\n"
        context += f"  Equipment: {c['equipment_type']}\n"
        context += f"  Complaint: {c['complaint']}\n"
        context += f"  Root Cause: {c['root_cause']}\n"
        context += f"  Fix Steps: {c['fix_steps']}\n"
        context += f"  Parts Used: {c['parts_used']}\n"

    prompt = f"""
You are an expert facility maintenance diagnostic agent.
Analyze the following user complaint and past similar maintenance cases to produce a diagnosis.

User Complaint: {complaint}

Historical Context:
{context if context else 'No prior matching cases found.'}

Respond ONLY with a valid JSON object formatted exactly as follows (no markdown backticks or extra text):
{{
  "likely_root_cause": "Brief summary of root cause",
  "recommended_fix_steps": ["Step 1", "Step 2"],
  "parts_needed": ["Part 1", "Part 2"],
  "estimated_cost_inr": 1500,
  "urgency": "High/Medium/Low",
  "safety_critical": false
}}
"""

    response = llm.invoke(prompt)
    
    # Clean output text in case model adds quotes/markdown
    clean_json_str = response.content.strip().strip("```json").strip("```").strip()
    
    try:
        diagnosis_data = json.loads(clean_json_str)
    except json.JSONDecodeError:
        diagnosis_data = {
            "raw_response": response.content,
            "error": "Failed to parse JSON response"
        }
        
    return {"diagnosis": diagnosis_data}

def build_workflow():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("diagnose", diagnose_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "diagnose")
    workflow.add_edge("diagnose", END)
    
    return workflow.compile()

app = build_workflow()

if __name__ == "__main__":
    test_input = {
        "complaint": "AC is not cooling and making loud rattling noise",
        "equipment_type": None,
        "retrieved_cases": [],
        "diagnosis": {},
        "feedback_added": False
    }
    result = app.invoke(test_input)
    print("\n--- RETRIEVED CASES ---")
    print(json.dumps(result["retrieved_cases"], indent=2))
    print("\n--- DIAGNOSIS OUTPUT ---")
    print(json.dumps(result["diagnosis"], indent=2))