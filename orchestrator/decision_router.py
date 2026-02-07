from .envelope_models import DecisionEnvelope

class DecisionRouter:
    def route(self, envelope: DecisionEnvelope) -> DecisionEnvelope:
        routing_map = {
            "demo_task": "demo_pipeline",
            "analytics_task": "analytics_pipeline",
            "report_task": "report_pipeline"
        }
        envelope.context["pipeline"] = routing_map.get(
            envelope.task_type, "default_pipeline"
        )
        if envelope.context.get("priority") == "high":
            envelope.context["pipeline"] += "_high_priority"
        return envelope
