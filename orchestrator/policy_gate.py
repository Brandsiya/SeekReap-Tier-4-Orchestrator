from .envelope_models import DecisionEnvelope

class PolicyGate:
    allowed_task_types = ["demo_task", "analytics_task", "report_task"]

    def validate(self, envelope: DecisionEnvelope) -> bool:
        valid = envelope.task_type in self.allowed_task_types
        if not valid:
            return False

        envelope.policies_applied.append("task_type_check")

        if "param" in envelope.inputs:
            envelope.policies_applied.append("input_check")
        else:
            return False

        return True
