"""Read repository configuration independently of the working directory."""
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from string import Formatter

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def configure_tensorflow_environment():
    """Set CPU/logging policy before importing TensorFlow in an entry point."""
    settings = load_config("tensorflow")
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", str(settings["cpp_min_log_level"]))


class ConfigError(ValueError):
    """A configuration file is missing or invalid."""


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc


def _validate(value, schema, location):
    kind = schema["type"]
    if isinstance(kind, list):
        for option in kind:
            try:
                _validate(value, {**schema, "type": option}, location)
                return
            except ConfigError:
                continue
        raise ConfigError(f"Invalid configuration {location}: expected one of {kind}")
    valid = {
        "null": lambda: value is None,
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str) and bool(value),
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
    }[kind]()
    if not valid:
        raise ConfigError(f"Invalid configuration {location}: expected {kind}")
    if kind == "object":
        properties = schema["properties"]
        missing, unknown = properties.keys() - value.keys(), value.keys() - properties.keys()
        if missing or unknown:
            raise ConfigError(f"Invalid configuration {location}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}")
        for key, item in value.items():
            _validate(item, properties[key], f"{location}.{key}")
    elif kind == "array":
        if len(value) < schema.get("min_items", 0):
            raise ConfigError(f"Invalid configuration {location}: list must not be empty")
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{location}[{index}]")
    elif kind in ("integer", "number"):
        for rule, compare in (
            ("minimum", lambda limit: value >= limit),
            ("maximum", lambda limit: value <= limit),
            ("exclusive_minimum", lambda limit: value > limit),
            ("exclusive_maximum", lambda limit: value < limit),
        ):
            if rule in schema and not compare(schema[rule]):
                raise ConfigError(f"Invalid configuration {location}: {rule} {schema[rule]}")


@lru_cache(maxsize=None)
def load_config(name):
    """Load and validate a configuration section once per process.

    Files resolve from the repository, while configured relative output/input
    paths retain their original meaning relative to the working directory.
    Restart the command after editing configuration.
    """
    schema_path = CONFIG_DIR / "schema.json"
    schemas = _read_json(schema_path)
    if name not in schemas:
        raise ConfigError(f"Unknown configuration section: {name}")
    path = CONFIG_DIR / f"{name}.json"
    value = _read_json(path)
    return validate_config(name, value)


def validate_config(name, value):
    """Validate proposed settings without writing files or populating the cache."""
    schemas = _read_json(CONFIG_DIR / "schema.json")
    if name not in schemas:
        raise ConfigError(f"Unknown configuration section: {name}")
    path = CONFIG_DIR / f"{name}.json"
    _validate(value, schemas[name], str(path))
    patterns = {
        "content_safety": [(value.get("secret_content"), "secret_content")],
        "data": [(value.get("policy", {}).get("skip_name"), "policy.skip_name")],
        "import_dataset": [(value.get("policy", {}).get("sensitive"), "policy.sensitive")],
    }
    for pattern, key in patterns.get(name, []):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"Invalid configuration {path}.{key}: {exc}") from exc
    if name == "prompts":
        for key in ("risk_phrases", "safe_phrases", "risk_phrases_banner", "safe_phrases_banner"):
            templates = value[key] if isinstance(value[key], list) else [value[key]]
            for template in templates:
                try:
                    for _, field, spec, conversion in Formatter().parse(template):
                        if field is not None and (field not in {"service", "banner_sample"} or spec or conversion):
                            raise ValueError("use only {service} and {banner_sample} placeholders")
                except ValueError as exc:
                    raise ConfigError(f"Invalid configuration {path}.{key}: {exc}") from exc
    if name == "model":
        model = value["functions"]["build_model"]
        if model["context"] < 2 or model["d_model"] < 4 or model["d_model"] % model["heads"]:
            raise ConfigError(f"Invalid configuration {path}: incompatible context/d_model/heads")
    if name == "train":
        cli = value["cli"]
        if cli["context"] < 2 or cli["dim"] < 4 or cli["dim"] % cli["heads"]:
            raise ConfigError(f"Invalid configuration {path}: incompatible context/dim/heads")
    if name == "local_vuln_scan":
        runtime = value["runtime"]
        if runtime["high_risk_delta"] <= runtime["medium_risk_delta"]:
            raise ConfigError(f"Invalid configuration {path}: high_risk_delta must exceed medium_risk_delta")
    if name == "web":
        if value["server"]["host"] not in {"127.0.0.1", "localhost"}:
            raise ConfigError(f"Invalid configuration {path}: the web interface must bind to loopback")
        identifiers = [profile["id"] for profile in value["profiles"]]
        if len(set(identifiers)) != len(identifiers):
            raise ConfigError(f"Invalid configuration {path}: profile IDs must be unique")
        if any(alias in identifiers or target not in identifiers
               for alias, target in value["profile_aliases"].items()):
            raise ConfigError(f"Invalid configuration {path}: profile aliases must reference canonical IDs")
        local_paths = [value["runtime"]["log_dir"]]
        local_paths.extend(profile[key] for profile in value["profiles"] for key in ("corpus", "out"))
        if any(Path(raw).is_absolute() or ".." in Path(raw).parts for raw in local_paths):
            raise ConfigError(f"Invalid configuration {path}: web paths must stay inside the project")
    if name == "security_tools":
        identifiers = [entry["id"] for entry in value["templates"]]
        if not identifiers or len(set(identifiers)) != len(identifiers) or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', identifier) for identifier in identifiers):
            raise ConfigError(f"Invalid configuration {path}: template IDs must be unique local identifiers")
        entries = value["templates"]
        if any(not re.fullmatch(r'[a-f0-9]{64}', entry["sha256"]) for entry in entries) or not re.fullmatch(r'[a-f0-9]{64}', value["runtime"]["nuclei_config_sha256"]):
            raise ConfigError(f"Invalid configuration {path}: expected SHA-256 fingerprints")
        local_paths = [entry["path"] for entry in entries] + [value["runtime"][key] for key in ('reports_dir', 'nuclei_config')]
        if any(Path(raw).is_absolute() or '..' in Path(raw).parts for raw in local_paths):
            raise ConfigError(f"Invalid configuration {path}: security tool files must stay inside the project")
    return value
