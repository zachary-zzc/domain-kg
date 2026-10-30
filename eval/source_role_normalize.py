"""Deterministic diagnostic recovery of saved judge responses, without API calls.

Retains original verdicts and full reasons. Long reasons up to 20K characters do
not invalidate other rows. Unsupported facts receive zero reference credit even
if the model filled matches incorrectly. Missing/duplicate IDs, unknown fields,
invalid reference IDs and truncated/failed completions still fail closed.
Original strict outcomes and every normalization remain separately available.
"""

import copy

from eval.dual_path_data import json_object
from eval.source_role_expand import parse_wide_judgments


def normalize_judgments(raw, fact_ids, reference_ids):
    import json

    value = json_object(raw)
    if not isinstance(value, dict) or set(value) != {"facts", "references"}:
        raise ValueError("invalid envelope")
    candidate = copy.deepcopy(value)
    changes, reasons = [], {}
    for group in ("facts", "references"):
        if not isinstance(candidate[group], list):
            raise ValueError("invalid rows")
        for item in candidate[group]:
            if not isinstance(item, dict):
                raise ValueError("invalid row")
            reason = item.get("reason")
            ident = item.get("id")
            if isinstance(reason, str) and len(reason) > 1200:
                if len(reason) > 20000 or not isinstance(ident, str):
                    raise ValueError("reason exceeds recovery bound")
                reasons[group, ident] = reason
                item["reason"] = reason[:1200]
                changes.append(
                    {
                        "group": group,
                        "id": ident,
                        "kind": "long_reason_preserved",
                        "characters": len(reason),
                    }
                )
            if group == "facts" and item.get("verdict") != "supported" and item.get("matches"):
                matches = item["matches"]
                if (
                    not isinstance(matches, list)
                    or any(not isinstance(m, str) for m in matches)
                    or not set(matches) <= reference_ids
                    or len(matches) != len(set(matches))
                ):
                    raise ValueError("invalid reference mapping")
                item["matches"] = []
                changes.append(
                    {
                        "group": group,
                        "id": ident,
                        "kind": "unsupported_zero_credit",
                        "discarded_matches": matches,
                    }
                )
    parsed = parse_wide_judgments(json.dumps(candidate), fact_ids, reference_ids)
    for group in ("facts", "references"):
        for item in parsed[group]:
            if (group, item["id"]) in reasons:
                item["reason"] = reasons[group, item["id"]]
    return parsed, changes


def normalize_evidence_judgment(raw, source_ids, has_paths):
    import json

    from eval.source_role_evidence import EvidenceJudgment, parse_evidence_judgment

    # Validate all supplied fields before applying the deterministic empty-path rule.
    original = json_object(raw)
    if not isinstance(original, dict):
        raise ValueError("invalid evidence envelope")
    candidate = copy.deepcopy(original)
    changes = []
    reason = original.get("reason")
    if isinstance(reason, str) and len(reason) > 2500:
        if len(reason) > 20000:
            raise ValueError("reason exceeds recovery bound")
        candidate["reason"] = reason[:2500]
        changes.append({"kind": "long_reason_preserved", "characters": len(reason)})
    value = EvidenceJudgment.model_validate(candidate).model_dump()
    if not has_paths and value["path_status"] != "none":
        changes.append({"kind": "no_supplied_paths", "original_path_status": value["path_status"]})
        value["path_status"] = "none"
    result = parse_evidence_judgment(json.dumps(value), source_ids, has_paths)
    result["reason"] = original["reason"]
    return result, changes
