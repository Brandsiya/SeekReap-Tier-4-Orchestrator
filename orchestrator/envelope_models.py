from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class DecisionEnvelope:
    task_id: str
    task_type: str
    inputs: Dict[str, Any]
    context: Dict[str, Any]
    policies_applied: List[str] = field(default_factory=list)
