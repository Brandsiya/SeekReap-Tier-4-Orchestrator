import unittest
from orchestrator.orchestrator_core import OrchestratorCore
from orchestrator.envelope_models import DecisionEnvelope

class TestOrchestrator(unittest.TestCase):
    def test_pipeline_success(self):
        core = OrchestratorCore()
        envelope = DecisionEnvelope(
            task_id="task_001",
            task_type="demo_task",
            inputs={"param": 123},
            context={"user": "tester"}
        )
        result = core.run(envelope)
        self.assertEqual(result["status"], "completed")
        self.assertIn("task_type_check", envelope.policies_applied)

    def test_policy_rejection(self):
        core = OrchestratorCore()
        envelope = DecisionEnvelope(
            task_id="task_002",
            task_type="invalid_task",
            inputs={"param": 123},
            context={"user": "tester"}
        )
        result = core.run(envelope)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "Policy violation")

if __name__ == "__main__":
    unittest.main()
