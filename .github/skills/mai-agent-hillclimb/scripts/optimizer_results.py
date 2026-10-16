"""Identity-aware native search reporting, not a held-out serving comparison."""

import json

from evaluator_adapter import _hillclimb_response_text
from journey import value_digest


def case_key(query, reference):
    reference = "" if reference is None else reference
    if not isinstance(query, str) or not isinstance(reference, str):
        raise TypeError("Native examples require string queries and references")
    try:
        reference = json.loads(reference)
    except ValueError:
        pass
    return value_digest({"query": query.replace("\r\n", "\n"), "reference": reference})


def case_map(rows):
    result = {}
    for row in rows:
        reference = row.get("ground_truth")
        reference = "" if reference is None else reference
        key = case_key(row["query"], reference)
        if key in result:
            raise ValueError("Ambiguous duplicate optimizer query/reference identity")
        try:
            reference = json.loads(reference)
        except ValueError:
            reference = {}
        case_id = row.get("name") or row.get("id")
        if not case_id and isinstance(reference, dict):
            case_id = reference.get("id")
        result[key] = case_id or f"sha256:{key}"
    return result


def build_report(optimization, candidates, runs, outputs):
    identities = optimization.get("case_map")
    if identities is None:
        source = optimization["config"]["plan"]["optimization_configuration"][
            "evaluation_configuration"
        ]["training_set"]["source"]
        if source["type"] != "inline":
            raise ValueError("No stable case mapping is recorded for this dataset")
        identities = case_map(source["test_cases"])
    stages, sets = [], []
    for candidate in candidates:
        evaluation = candidate.get("evaluation") or {}
        run_id = evaluation.get("eval_run_id")
        if not run_id:
            raise ValueError("Candidate has no recorded evaluation run")
        run, rows = runs[run_id], outputs[run_id]
        imported = []
        for row in rows:
            item = row.get("datasource_item") or {}
            key = case_key(item.get("query"), item.get("ground_truth"))
            if key not in identities:
                raise ValueError("Native output does not match the frozen optimizer examples")
            imported.append(
                {
                    "case_id": identities[key],
                    "case_fingerprint": key,
                    "native_row_id": row["datasource_item_id"],
                    "status": row["status"],
                    "output_text": _hillclimb_response_text(item["sample.output_text"])
                    if "sample.output_text" in item
                    else None,
                    "evaluator_components": [
                        {
                            "name": metric.get("name"),
                            "score": metric.get("score"),
                            "passed": metric.get("passed"),
                        }
                        for metric in row.get("results", [])
                    ],
                    "raw_usage": (row.get("sample") or {}).get("usage"),
                }
            )
        ids = {row["case_id"] for row in imported}
        if len(ids) != len(imported):
            raise ValueError("Repeated native case IDs need an explicit rollout mapping")
        sets.append(ids)
        content = ((run.get("data_source") or {}).get("source") or {}).get("content")
        scheduled = None
        if isinstance(content, list):
            scheduled = set()
            for entry in content:
                item = entry["item"]
                key = case_key(item["query"], item.get("ground_truth"))
                if key not in identities:
                    raise ValueError("Native schedule differs from frozen optimizer examples")
                scheduled.add(identities[key])
        if scheduled is not None and not ids.issubset(scheduled):
            raise ValueError("Native outputs are not a subset of their recorded schedule")
        stages.append(
            {
                "candidate_id": candidate["candidate_id"],
                "name": candidate.get("name"),
                "optimizer_composite_score": evaluation.get("score"),
                "shared_evaluator_scores": evaluation.get("evaluator_scores"),
                "reported_average_tokens": evaluation.get("avg_tokens"),
                "reported_average_latency_ms": evaluation.get("avg_latency_ms"),
                "run_status": run["status"],
                "scheduled_output_count": (run.get("result_counts") or {}).get("total"),
                "returned_case_count": len(ids),
                "returned_case_ids": sorted(ids),
                "unobserved_scheduled_case_ids": sorted(scheduled - ids)
                if scheduled is not None
                else None,
                "outputs": imported,
            }
        )
    return {
        "scope": "optimizer_search_only",
        "held_out_validation": False,
        "matched_serving_comparison": False,
        "different_case_subsets": any(ids != sets[0] for ids in sets[1:]) if sets else False,
        "warning": "Composite scores include task criteria; native averages across adaptive subsets "
        "are not matched serving or cost-savings evidence.",
        "source_case_count": len(identities),
        "candidates": stages,
    }
