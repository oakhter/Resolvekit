RESPONDER_PROMPT_VERSION = "responder:v3.3-source-grounded"
EVALUATOR_PROMPT_VERSION = "evaluator:v3.3-qa-triad"

PROMPT_REGISTRY = {
    "responder": {
        "version": RESPONDER_PROMPT_VERSION,
        "rollback": "responder:v3.1-source-grounded",
        "golden_eval_required": True,
    },
    "evaluator": {
        "version": EVALUATOR_PROMPT_VERSION,
        "rollback": "evaluator:v3.1-faithfulness",
        "golden_eval_required": True,
    },
}


def prompt_versions(model_provider: str = "") -> dict:
    return {
        name: {
            "version": details["version"],
            "model_provider": model_provider,
            "rollback": details["rollback"],
            "golden_eval_required": details["golden_eval_required"],
        }
        for name, details in PROMPT_REGISTRY.items()
    }
