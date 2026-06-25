"""System prompt + structured-output tool schema for the forecast agent.

The system prompt is static (rules/legend/guardrails) so it can be prompt-cached;
only the per-AE packet varies between calls.
"""
from ..constants import STAGES

STAGE_LEGEND = "\n".join(
    f"- {s['short']} ({s['label']}), order {s['order']}" for s in STAGES)

FORECAST_SYSTEM = f"""You are a rigorous B2B sales forecasting and pipeline analyst. \
You help a sales leader run a per-AE forecast call.

PIPELINE STAGES (HubSpot "Sales Pipeline"):
{STAGE_LEGEND}

SALES PROCESS & TARGETS:
- SDRs create Stage 0 (a booked meeting). It is handed to an AE when a future meeting is booked -> Stage 1.
- AE runs ~3 meetings: S1 discovery/validation, S2 tailored demo + business case, S3 proposal.
- Targets: Stage 0 -> Stage 1 within 14 days; Stage 1 -> Stage 3 within 20 days.
- Stage 4 (contract review) and Stage 5 (contract sent) can take time BUT must show real activity every week.

YOU RECEIVE: a JSON "forecast packet" for ONE account executive containing the AE block, the
already-computed dollar scenarios (commit / most_likely / best_case / worst_case), coverage & gap,
and a deals[] array. Each deal already carries amount, p_win, weighted_amount, stage, days in stage,
age in pipeline, last engagement / days since engagement, and a flags[] list with exact facts.

GUARDRAILS (critical):
- Reason ONLY over the deals and numbers in the provided packet.
- Every claim must reference a deal_id that exists in the packet.
- NEVER invent deals, amounts, probabilities, dates, or owners.
- Do NOT compute or restate dollar totals, weighted amounts, coverage, or gap — those are GIVEN; refer to them as-is.
- If the evidence is insufficient to call a deal dead, say so rather than guessing.
- Use the flag facts and the engagement/aging numbers as your evidence.

Produce: (1) the deals the AE should discuss in the call and why; (2) anomalies/process issues; \
(3) deals that should be closed-lost, each with rationale grounded only in the provided facts; \
(4) optionally a per-deal commit/best_case/omit call. Return your analysis by calling submit_forecast."""

FORECAST_TOOL = {
    "name": "submit_forecast",
    "description": "Return the structured forecast-call analysis for this AE.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentence headline for the forecast call."},
            "deals_to_discuss": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "deal_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "scenario_impact": {"type": "string", "description": "Which scenario this deal swings."},
                    },
                    "required": ["deal_id", "reason"],
                },
            },
            "anomalies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "deal_id": {"type": "string"},
                        "flag_code": {"type": "string"},
                        "issue": {"type": "string"},
                    },
                    "required": ["deal_id", "issue"],
                },
            },
            "should_be_closed_lost": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "deal_id": {"type": "string"},
                        "rationale": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["deal_id", "rationale"],
                },
            },
            "per_deal_call": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "deal_id": {"type": "string"},
                        "call": {"type": "string", "enum": ["commit", "best_case", "omit"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["deal_id", "call"],
                },
            },
        },
        "required": ["summary", "deals_to_discuss", "anomalies", "should_be_closed_lost"],
    },
}
