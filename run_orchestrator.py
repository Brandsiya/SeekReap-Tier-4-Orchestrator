#!/usr/bin/env python3
import asyncio
from orchestrator.orchestrator_core import OrchestratorCore
from orchestrator.envelope_models import DecisionEnvelope

async def main():
    core = OrchestratorCore()

    envelopes = [
        DecisionEnvelope(task_id=f"task_{i:03}", task_type="demo_task", inputs={"param": i},
                         context={"user": f"user_{i}", "priority": "high" if i % 2 == 0 else "normal"})
        for i in range(1, 6)
    ]

    results = await core.run_batch(envelopes)
    for r in results:
        print(r)

if __name__ == "__main__":
    asyncio.run(main())
