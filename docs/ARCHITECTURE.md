# Tier-4 Orchestration Layer Architecture

## Overview
Tier-4 acts as the orchestration layer:
- Receives Decision Envelopes
- Applies policy rules
- Routes tasks to Tier-3 Execution Layer

## Components
- orchestrator_core.py : Main orchestration engine
- decision_router.py   : Task routing
- policy_gate.py       : Policy enforcement
- tier3_client.py      : Tier-3 API interface
- envelope_models.py   : Decision Envelope v1 definition

## Pipeline Flow
DecisionEnvelope -> PolicyGate -> DecisionRouter -> Tier3Client -> Result

## Notes
- Scaffold only: logic TBD
- Designed to maintain stable contract with Tier-3
