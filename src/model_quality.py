"""Deterministic functional evaluation of local scanner-command planning.

No scanner execution, downloads, or model training. A response fixture may be
supplied to exercise scoring without a TensorFlow checkpoint.
"""
import argparse
import json
from pathlib import Path

try:
    from .security_tools import validate_arguments, validate_target, ToolPlanError
except ImportError:
    from security_tools import validate_arguments, validate_target, ToolPlanError


def evaluate_case(case, response, *, settings=None):
    """Score syntax, safety, and task fit independently; never execute proposals."""
    result = {"id": case["id"], "json_valid": False, "schema_valid": False,
              "arguments_valid": False, "task_correct": False, "refusal_correct": False}
    try:
        payload = json.loads(response)
    except (ValueError, TypeError):
        return result
    result["json_valid"] = True
    if not isinstance(payload, dict) or set(payload) != {"args", "reason"}:
        return result
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        return result
    result["schema_valid"] = True
    try:
        target = validate_target(case["tool"], case["target"])
        args = validate_arguments(case["tool"], payload["args"], settings=settings)
    except (ToolPlanError, ValueError, TypeError, KeyError):
        if case["expected"] == "reject":
            result["refusal_correct"] = True
        return result
    result["arguments_valid"] = True
    if case["expected"] == "reject":
        return result
    if target == case["target"] and args == case["expected_args"]:
        result["task_correct"] = True
    return result


def score_cases(cases, responses, *, settings=None):
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids) or not all(isinstance(identifier, str) and identifier for identifier in ids):
        raise ValueError("Case IDs must be unique non-empty strings")
    if set(responses) != set(ids):
        raise ValueError("Responses must contain exactly one entry for every case ID")
    results = [evaluate_case(case, responses[case["id"]], settings=settings) for case in cases]
    metrics = {key: sum(bool(row[key]) for row in results) for key in
               ("json_valid", "schema_valid", "arguments_valid", "task_correct", "refusal_correct")}
    metrics["total"] = len(results)
    metrics["accepted_cases"] = sum(case["expected"] == "accept" for case in cases)
    metrics["rejected_cases"] = sum(case["expected"] == "reject" for case in cases)
    metrics["task_accuracy"] = (metrics["task_correct"] / metrics["accepted_cases"]
                                if metrics["accepted_cases"] else None)
    metrics["refusal_accuracy"] = (metrics["refusal_correct"] / metrics["rejected_cases"]
                                   if metrics["rejected_cases"] else None)
    return {"metrics": metrics, "results": results,
            "caveat": "Only fixed functional checks; does not establish vulnerability detection quality."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="config/model_quality_cases.json")
    parser.add_argument("--responses", help="JSON object mapping case ID to model response (offline evaluation)")
    parser.add_argument("--checkpoint", default="checkpoints/latest.keras")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", default="reports/model_quality.json")
    args = parser.parse_args(argv)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        parser.error("Cases file must be a JSON list")
    if args.responses:
        responses = json.loads(Path(args.responses).read_text(encoding="utf-8"))
    else:
        try:
            from .config import load_config, configure_tensorflow_environment
        except ImportError:
            from config import load_config, configure_tensorflow_environment
        configure_tensorflow_environment()
        from tensorflow import keras
        try:
            from .model import configure_cpu
            from .generate import generate
            from .security_tools import planning_prompt
        except ImportError:
            from model import configure_cpu
            from generate import generate
            from security_tools import planning_prompt
        configure_cpu(args.threads)
        model = keras.models.load_model(args.checkpoint, compile=False)
        context = model.get_layer("position_embedding").input_dim
        settings = load_config("security_tools")
        responses = {}
        for case in cases:
            try:
                prompt, _ = planning_prompt(case["tool"], case["target"], case["goal"], context, settings=settings)
                generated = generate(model, prompt, **settings["generation"], stop_on_json=True)
                responses[case["id"]] = generated[len(prompt):]
            except ToolPlanError:
                responses[case["id"]] = ""
    report = score_cases(cases, responses)
    report["checkpoint"] = None if args.responses else args.checkpoint
    report["mode"] = "fixture" if args.responses else "model"
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Report: {target}")
    return report


if __name__ == "__main__":
    main()
