from .envelope_models import DecisionEnvelope
from .policy_gate import PolicyGate
from .decision_router import DecisionRouter
from .tier3_client import Tier3Client
import logging
import asyncio
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

class OrchestratorCore:
    def __init__(self):
        self.policy_gate = PolicyGate()
        self.decision_router = DecisionRouter()
        self.tier3_client = Tier3Client()

    async def run_task(self, envelope: DecisionEnvelope) -> dict:
        start_time = datetime.utcnow()
        logging.info(f"Starting orchestration for task {envelope.task_id}")

        if not self.policy_gate.validate(envelope):
            logging.warning(f"Policy violation for task {envelope.task_id}")
            return {"status": "rejected", "reason": "Policy violation"}

        routed_envelope = self.decision_router.route(envelope)
        logging.info(f"Task {envelope.task_id} routed to pipeline: {routed_envelope.context['pipeline']}")

        result = await self.tier3_client.execute_task({
            "task_id": routed_envelope.task_id,
            "task_type": routed_envelope.task_type,
            "inputs": routed_envelope.inputs,
            "context": routed_envelope.context
        })

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        logging.info(f"Task {envelope.task_id} completed in {duration}s with status: {result['status']}")

        # Save persistent log
        with open("orchestrator_task_log.txt", "a") as f:
            f.write(f"{datetime.utcnow()} | {envelope.task_id} | {routed_envelope.context['pipeline']} | {result['status']} | {duration}s\n")

        return {"status": "completed", "result": result, "envelope": routed_envelope}

    async def run_batch(self, envelopes):
        # sort by priority: high > default
        envelopes.sort(key=lambda e: e.context.get("priority") != "high")
        tasks = [self.run_task(env) for env in envelopes]
        return await asyncio.gather(*tasks)
