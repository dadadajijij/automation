from pathlib import Path
import re
from typing import Callable, Dict, List, Optional

from .common import build_deployment_base_path, is_allowed_root_relative_url
from .models import SubpathPlan
from .build_output_policy import (
    BUILD_OUTPUT_AUDIT_POLICY_VERSION,
    build_output_finding_policy,
    recommended_build_output_mode,
)


def _build_output_patterns() -> tuple[tuple[re.Pattern[str], str], ...]:
    return (
        (re.compile(r'(?:href|src|action)=(["\'])(?P<path>/[^"\']*)\1'), "build_output_html_root_relative_url"),
        (re.compile(r'fetch\((["\'])(?P<path>/[^"\']*)\1'), "build_output_client_root_relative_url"),
        (re.compile(r'axios\.(?:get|post|put|delete|patch)\((["\'])(?P<path>/[^"\']*)\1'), "build_output_client_root_relative_url"),
        (re.compile(r'window\.location(?:\.href)?\s*=\s*(["\'])(?P<path>/[^"\']*)\1'), "build_output_client_root_relative_url"),
        (re.compile(r'window\.open\((["\'])(?P<path>/[^"\']*)\1'), "build_output_client_root_relative_url"),
        (re.compile(r'["\'](?P<path>/(?:assets|static|api)/[^"\']*)["\']'), "build_output_manifest_root_relative_url"),
    )


def detect_build_output_roots(repo_dir: Path, plan: SubpathPlan) -> List[Path]:
    candidates: List[Path] = []
    for project in plan.projects:
        if project.capabilities and "build_output_expected" not in project.capabilities:
            continue
        for root in (
            project.project_root / "dist",
            project.project_root / "build",
            project.project_root / "out",
        ):
            if root.is_dir():
                candidates.append(root.resolve())
    return sorted(set(candidates))


def summarize_build_output_findings(
    findings: List[Dict[str, object]],
    *,
    policy_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, object]:
    findings_by_code: Dict[str, int] = {}
    findings_by_category: Dict[str, int] = {}
    findings_by_severity: Dict[str, int] = {}
    enforcement_candidates: Dict[str, str] = {}
    enforcement_candidate_counts: Dict[str, int] = {}
    files_with_findings = set()
    for finding in findings:
        code = str(finding.get("code", "")).strip() or "unknown"
        policy = build_output_finding_policy(code, policy_overrides=policy_overrides)
        findings_by_code[code] = findings_by_code.get(code, 0) + 1
        category = policy["category"]
        findings_by_category[category] = findings_by_category.get(category, 0) + 1
        severity = str(finding.get("severity", "")).strip() or policy["severity"]
        findings_by_severity[severity] = findings_by_severity.get(severity, 0) + 1
        enforcement_candidates[code] = policy["enforcement_candidate"]
        enforcement_mode = enforcement_candidates[code]
        enforcement_candidate_counts[enforcement_mode] = enforcement_candidate_counts.get(enforcement_mode, 0) + 1
        file_name = str(finding.get("file", "")).strip()
        if file_name:
            files_with_findings.add(file_name)
    return {
        "policy_version": BUILD_OUTPUT_AUDIT_POLICY_VERSION,
        "total_findings": len(findings),
        "findings_by_code": dict(sorted(findings_by_code.items())),
        "findings_by_category": dict(sorted(findings_by_category.items())),
        "findings_by_severity": dict(sorted(findings_by_severity.items())),
        "enforcement_candidates": dict(sorted(enforcement_candidates.items())),
        "enforcement_candidate_counts": dict(sorted(enforcement_candidate_counts.items())),
        "recommended_mode": recommended_build_output_mode(enforcement_candidate_counts),
        "files_with_findings": sorted(files_with_findings),
    }


def scan_build_output_findings(
    output_root: Path,
    base_path: str,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text: Callable[[Path], str],
) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    seen = set()
    patterns = _build_output_patterns()
    for file_path in collect_matching_files(output_root, ["**/*.html", "**/*.js", "**/*.mjs", "**/*.css", "**/*.json", "*.html", "*.json"]):
        text = read_text(file_path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern, code in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                candidate = match.group("path")
                if is_allowed_root_relative_url(candidate, base_path):
                    continue
                finding_key = (file_path.relative_to(output_root).as_posix(), line_no, candidate, code)
                if finding_key in seen:
                    continue
                seen.add(finding_key)
                findings.append(
                    {
                        "file": file_path.relative_to(output_root).as_posix(),
                        "line": line_no,
                        "severity": "error",
                        "code": code,
                        "message": f'Build output emits root-relative URL `{candidate}` outside deployment subpath `{base_path}`',
                    }
                )
                break
    return findings


def run_build_output_subpath_audit(
    repo_dir: Path,
    project_slug: str,
    plan: SubpathPlan,
    policy_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text: Callable[[Path], str],
) -> Dict[str, object]:
    base_path = build_deployment_base_path(project_slug)
    output_roots = detect_build_output_roots(repo_dir, plan)
    findings: List[Dict[str, object]] = []
    for output_root in output_roots:
        findings.extend(
            scan_build_output_findings(
                output_root,
                base_path,
                collect_matching_files=collect_matching_files,
                read_text=read_text,
            )
        )
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code", "")).strip() or "unknown"
        policy = build_output_finding_policy(code, policy_overrides=policy_overrides)
        finding["severity"] = policy["severity"]
    summary = summarize_build_output_findings(findings, policy_overrides=policy_overrides)
    summary["output_roots_count"] = len(output_roots)
    return {
        "output_roots": [path.relative_to(repo_dir).as_posix() for path in output_roots],
        "findings": findings,
        "summary": summary,
        "warnings": [],
    }
