"""Compare fixed code-generation tasks using syntax and an AST interpreter."""
import argparse
import json
from pathlib import Path
try:
    from .config import load_config, configure_tensorflow_environment
    from .code_quality import check_function
except ImportError:
    from config import load_config, configure_tensorflow_environment
    from code_quality import check_function

configure_tensorflow_environment()
from tensorflow import keras
try:
    from .generate import generate
    from .model import configure_cpu
except ImportError:
    from generate import generate
    from model import configure_cpu

SETTINGS = load_config("benchmark")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint", "output"):
        parser.add_argument(f"--{name}", default=SETTINGS["cli"][name])
    parser.add_argument("--threads", type=int, default=SETTINGS["cli"]["threads"])
    args = parser.parse_args()
    configure_cpu(args.threads)
    model = keras.models.load_model(args.checkpoint, compile=False)
    results = []
    for task in SETTINGS["tasks"]:
        source = generate(model, task["prompt"], **SETTINGS["generation"])
        result = {"name": task["name"], "prompt": task["prompt"], "source": source,
                  **check_function(source, task["name"], task["cases"])}
        results.append(result)
        print(f"{task['name']}: syntax={result['syntax_valid']} behavior={result['behavior_status']}", flush=True)
    report = {"checkpoint": args.checkpoint, "generation": SETTINGS["generation"], "results": results,
              "syntax_passes": sum(item["syntax_valid"] for item in results),
              "behavior_passes": sum(item["behavior_status"] == "passed" for item in results),
              "total": len(results),
              "caveat": "Fixed exercise checks, not a general correctness or vulnerability benchmark. Generated Python is never executed."}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report saved to {destination}")


if __name__ == "__main__":
    main()
