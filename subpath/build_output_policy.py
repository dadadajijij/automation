from typing import Dict, Optional


BUILD_OUTPUT_AUDIT_POLICY_VERSION = "v1"
DEFAULT_BUILD_OUTPUT_FINDING_POLICY: Dict[str, Dict[str, str]] = {
    "build_output_html_root_relative_url": {
        "category": "html",
        "severity": "error",
        "enforcement_candidate": "future_blocker",
    },
    "build_output_client_root_relative_url": {
        "category": "client",
        "severity": "error",
        "enforcement_candidate": "observe",
    },
    "build_output_manifest_root_relative_url": {
        "category": "manifest",
        "severity": "error",
        "enforcement_candidate": "observe",
    },
}
DEFAULT_BUILD_OUTPUT_FALLBACK_POLICY = {
    "category": "other",
    "severity": "error",
    "enforcement_candidate": "observe",
}


def normalize_build_output_policy(
    policy_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Dict[str, str]]:
    merged: Dict[str, Dict[str, str]] = {
        code: {
            "category": policy["category"],
            "severity": policy["severity"],
            "enforcement_candidate": policy["enforcement_candidate"],
        }
        for code, policy in DEFAULT_BUILD_OUTPUT_FINDING_POLICY.items()
    }
    if not isinstance(policy_overrides, dict):
        return merged
    for code, override in policy_overrides.items():
        if not isinstance(code, str) or not isinstance(override, dict):
            continue
        base = dict(DEFAULT_BUILD_OUTPUT_FALLBACK_POLICY)
        base.update(merged.get(code, {}))
        for key in ("category", "severity", "enforcement_candidate"):
            value = override.get(key)
            if isinstance(value, str) and value.strip():
                base[key] = value.strip()
        merged[code] = base
    return merged


def build_output_finding_policy(
    code: str,
    policy_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, str]:
    normalized = normalize_build_output_policy(policy_overrides)
    return dict(normalized.get(code, DEFAULT_BUILD_OUTPUT_FALLBACK_POLICY))


def recommended_build_output_mode(enforcement_candidate_counts: Dict[str, int]) -> str:
    if enforcement_candidate_counts.get("enforce"):
        return "enforce"
    if enforcement_candidate_counts.get("future_blocker") and enforcement_candidate_counts.get("observe"):
        return "mixed_shadow"
    if enforcement_candidate_counts.get("future_blocker"):
        return "candidate_for_enforce"
    return "shadow_only"
