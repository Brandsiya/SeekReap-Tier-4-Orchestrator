"""
Decision Envelope v1: Standard structure for Tier-4 → Tier-3 orchestration
"""

from dataclasses import dataclass
from typing import Any, Dict, List

@dataclass
class DecisionEnvelope:
    task_id: str
    task_type: str
    inputs: Dict[str, Any]
    context: Dict[str, Any]
    policies_applied: List[str] = None
