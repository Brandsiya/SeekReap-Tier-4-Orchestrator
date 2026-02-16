from fastapi import FastAPI
from pydantic import BaseModel
import asyncio

from orchestrator.orchestrator_core import OrchestratorCore
from orchestrator.envelope_models import DecisionEnvelope

app = FastAPI()
core = OrchestratorCore()

class Envelope(BaseModel):
    task_id: str
    task_type: str
    inputs: dict
    context: dict = {}

@app.post("/process")
def process_envelope(envelope: Envelope):
    """Synchronous FastAPI endpoint that runs async orchestration"""
    envelope_dict = envelope.dict()
    async def runner():
        decision_envelope = DecisionEnvelope(**envelope_dict)
        return await core.run_task(decision_envelope)
    
    return asyncio.run(runner())
