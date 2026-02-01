"""
Orchestrator Core v2: Wires DecisionEnvelope → PolicyGate → DecisionRouter → Tier3Client
"""

from .envelope_models import DecisionEnvelope
from .policy_gate import PolicyGate
from .decision_router import DecisionRouter
from .tier3_client import Tier3Client

class OrchestratorCore:
    def __init__(self):
        self.policy_gate = PolicyGate()
        self.decision_router = DecisionRouter()
        self.tier3_client = Tier3Client()

    def run(self, envelope: DecisionEnvelope) -> dict:
        """
        Main orchestration pipeline:
        1. Validate envelope via PolicyGate
        2. Route envelope via DecisionRouter
        3. Execute task through Tier3Client
        """
        # 1️⃣ Apply policies
        if not self.policy_gate.validate(envelope):
            return {"status": "rejected", "reason": "Policy violation"}

        # 2️⃣ Route decision
        routed_envelope = self.decision_router.route(envelope)

        # 3️⃣ Send to Tier-3
        result = self.tier3_client.execute_task({
            "task_id": routed_envelope.task_id,
            "task_type": routed_envelope.task_type,
            "inputs": routed_envelope.inputs,
            "context": routed_envelope.context,
        })

        # 4️⃣ Attach result to envelope and return
        return {"status": "completed", "result": result, "envelope": routed_envelope}
