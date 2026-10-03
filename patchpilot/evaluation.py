"""Small, transparent development benchmark; not a production accuracy estimate."""
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from .reviewer import review

DATASET_VERSION = "development-v1"
_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / f"{DATASET_VERSION}.json"


def cases():
    """Load fresh labeled cases so caller mutations cannot alter subsequent runs."""
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def demo():
    source = '''import subprocess

def add_item(item, cart=[]):
    cart.append(item)
    return cart

def calculate_discount(expression):
    return eval(expression)

def run_receipt(command):
    return subprocess.run(command, shell=True)
'''
    lines = source.splitlines()
    diff = "diff --git a/shopping/cart.py b/shopping/cart.py\nnew file mode 100644\n--- /dev/null\n+++ b/shopping/cart.py\n@@ -0,0 +1,%d @@\n" % len(lines)
    diff += "\n".join("+" + line for line in lines) + "\n"
    return {"title": "Add cart utilities and receipt generation", "diff": diff,
            "files": {"shopping/cart.py": source}}


def _identity(finding):
    return (finding.get("path"), finding.get("line"), finding.get("rule"))


def evaluate(use_ai=False):
    """Score static exact rule/path/line matches; keep AI findings unscored.

    Static and combined static+AI runs are distinct modes. The synthetic fixtures
    were selected during development and are deliberately small and public.
    """
    started = perf_counter()
    rows = []
    tp = fp = fn = clean_passes = clean_total = 0
    category_counts = {}
    ai_unscored = 0
    clean_excluded = 0
    actual_modes = set()
    warnings = []
    for case in cases():
        case_started = perf_counter()
        result = review(case["diff"], case["files"], use_ai)
        findings = result["findings"]
        static_findings = [f for f in findings if f.get("source", "static") == "static"]
        ai_count = len(findings) - len(static_findings)
        ai_unscored += ai_count
        actual_modes.add(result.get("mode", "unknown"))
        warnings.extend(result.get("warnings", []))
        expected = {_identity(item) for item in case["expected"]}
        observed = {_identity(item) for item in static_findings}
        tp += len(expected & observed)
        fp += len(observed - expected)
        fn += len(expected - observed)
        for identity in expected | observed:
            counts = category_counts.setdefault(identity[2], [0, 0, 0])
            counts[0 if identity in expected & observed else 1 if identity in observed else 2] += 1
        passed = expected == observed
        if "unsupported-partial-context" in case.get("tags", []):
            clean_excluded += 1
        if not expected and "unsupported-partial-context" not in case.get("tags", []):
            clean_total += 1
            clean_passes += int(passed)
        rows.append({"id": case["id"], "name": case["name"],
                     "expected": case["expected"], "findings": findings,
                     "passed": passed, "ai_findings_unscored": ai_count,
                     "duration_ms": round((perf_counter() - case_started) * 1000, 2),
                     "tags": case.get("tags", []), "warnings": result.get("warnings", [])})
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    categories = []
    for rule, (category_tp, category_fp, category_fn) in sorted(category_counts.items()):
        categories.append({"rule": rule, "true_positives": category_tp,
                           "false_positives": category_fp, "false_negatives": category_fn,
                           "precision": round(category_tp / (category_tp + category_fp), 4) if category_tp + category_fp else 0.0,
                           "recall": round(category_tp / (category_tp + category_fn), 4) if category_tp + category_fn else 0.0})
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "true_positives": tp, "false_positives": fp,
            "false_negatives": fn, "per_category": categories,
            "metrics_scope": "static findings only; exact rule/path/line labels",
            "ai_findings_unscored": ai_unscored,
            "clean_controls": {"total": clean_total, "passed": clean_passes,
                               "failed": clean_total - clean_passes, "excluded": clean_excluded},
            "clean_pass_rate": round(clean_passes / clean_total, 4) if clean_total else 0.0,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "cases": rows, "mode": "static+ai" if use_ai else "static",
            "actual_modes": sorted(actual_modes), "warnings": sorted(set(warnings)),
            "dataset_version": DATASET_VERSION,
            "limitations": [
                "Public synthetic development fixtures, not a held-out benchmark or production accuracy estimate.",
                "Labels cover selected static patterns only; passing cannot establish that a PR is bug-free.",
                "Only static findings are scored. AI findings are unscored and require human semantic adjudication; no AI precision or recall is claimed.",
                "Unsupported partial context is a robustness control, excluded from clean-pass rate.",
                "Optional AI results depend on configured model and service availability; inspect actual_modes and warnings.",
            ],
            "evaluated_at": datetime.now(timezone.utc).isoformat()}
