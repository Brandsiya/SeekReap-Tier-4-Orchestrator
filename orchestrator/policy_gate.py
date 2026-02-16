from orchestrator.envelope_models import DecisionEnvelope

class PolicyGate:
    allowed_task_types = ["demo_task", "analytics_task", "report_task"]

    def validate(self, envelope: DecisionEnvelope) -> bool:
        return envelope.task_type in self.allowed_task_types
