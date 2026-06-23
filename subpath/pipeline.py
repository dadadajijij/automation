from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import FrontendProjectStrategy, SubpathAuditFinding, SubpathPlan


def select_runtime_projects(plan: Optional[SubpathPlan]) -> Dict[str, object]:
    if plan is None or not plan.projects:
        return {
            "project_count": 0,
            "eligible_projects": [],
            "default_runtime_project": None,
            "entry_path_hint": None,
            "entry_path_candidates": [],
            "runtime_targets": [],
        }
    relevant_projects = [
        project
        for project in plan.projects
        if not project.capabilities or "html_entry" in project.capabilities or "runtime_root" in project.capabilities
    ]
    if not relevant_projects:
        return {
            "project_count": len(plan.projects),
            "eligible_projects": [],
            "default_runtime_project": None,
            "entry_path_hint": None,
            "entry_path_candidates": [],
            "runtime_targets": [],
        }
    default_runtime_project = next(
        (project for project in relevant_projects if project.project_id == plan.default_project),
        relevant_projects[0],
    )
    def runtime_target_for_project(project: FrontendProjectStrategy) -> Dict[str, object]:
        if project.runtime_entry_candidates:
            entry_path_candidates = list(project.runtime_entry_candidates)
            entry_path_hint = project.runtime_entry_hint or entry_path_candidates[0]
            return {
                "project_id": project.project_id,
                "entry_path_hint": entry_path_hint,
                "entry_path_candidates": entry_path_candidates,
            }
        entry_path_hint = "/"
        entry_path_candidates = ["/"]
        if project.framework in {"static_html", "generic"}:
            root_has_index = (project.project_root / "index.html").is_file()
            nested_runtime_entry_candidates: List[str] = []
            for runtime_root in project.runtime_roots:
                try:
                    relative_root = runtime_root.relative_to(project.project_root)
                except ValueError:
                    continue
                if relative_root == Path("."):
                    continue
                if relative_root.parts and relative_root.parts[0] in {"public", "static", "web", "client", "views", "templates", "src"}:
                    continue
                if (runtime_root / "index.html").is_file():
                    nested_runtime_entry_candidates.append(f"/{relative_root.as_posix().strip('/')}/")
            nested_runtime_entry_candidates = sorted({item for item in nested_runtime_entry_candidates if item})
            if not root_has_index and len(nested_runtime_entry_candidates) == 1:
                entry_path_hint = nested_runtime_entry_candidates[0]
                entry_path_candidates = [entry_path_hint, "/"]
        return {
            "project_id": project.project_id,
            "entry_path_hint": entry_path_hint,
            "entry_path_candidates": entry_path_candidates,
        }

    default_target = runtime_target_for_project(default_runtime_project)
    runtime_targets: List[Dict[str, object]] = [default_target]
    seen_candidate_sets = {tuple(default_target["entry_path_candidates"])}
    for project in relevant_projects:
        if project is default_runtime_project:
            continue
        target = runtime_target_for_project(project)
        candidate_set = tuple(target["entry_path_candidates"])
        if candidate_set in seen_candidate_sets:
            continue
        runtime_targets.append(target)
        seen_candidate_sets.add(candidate_set)
    return {
        "project_count": len(plan.projects),
        "eligible_projects": [project.project_id for project in relevant_projects],
        "default_runtime_project": default_runtime_project.project_id,
        "default_runtime_proxy_mode": default_runtime_project.proxy_mode,
        "entry_path_hint": default_target["entry_path_hint"],
        "entry_path_candidates": default_target["entry_path_candidates"],
        "runtime_targets": runtime_targets,
    }


def prepare_subpath_sources(
    repo_dir: Path,
    project_slug: str,
    *,
    build_subpath_plan: Callable[[Path], SubpathPlan],
    detect_runtime_root_evidence: Optional[Callable[[Path], object]] = None,
    apply_subpath_rewrites: Callable[[Path, Optional[str], SubpathPlan], List[str]],
    run_static_subpath_audit: Callable[[Path, str, SubpathPlan], Dict[str, object]],
    auto_fix_subpath_issues: Callable[[Path, str, SubpathPlan], List[str]],
    auto_fix_findings: Optional[Callable[[Path, str, List[Dict[str, object]], SubpathPlan], Dict[str, object]]] = None,
    sorted_unique: Callable[[List[str]], List[str]],
) -> Dict[str, object]:
    plan = build_subpath_plan(repo_dir)
    detection_evidence = detect_runtime_root_evidence(repo_dir) if detect_runtime_root_evidence is not None else None
    selection = select_runtime_projects(plan)
    proxy_mode = selection.get("default_runtime_proxy_mode")
    if not isinstance(proxy_mode, str) or not proxy_mode:
        default_project = next((project for project in plan.projects if project.project_id == plan.default_project), plan.projects[0]) if plan.projects else None
        proxy_mode = default_project.proxy_mode if default_project is not None else "strip_prefix"
    rewritten_files = apply_subpath_rewrites(repo_dir, project_slug, plan)
    static_subpath_audit = run_static_subpath_audit(repo_dir, project_slug, plan)
    static_audit_attempts: List[Dict[str, object]] = [static_subpath_audit]
    static_findings = static_subpath_audit.get("findings", [])
    auto_fixed_files: List[str] = []
    rewrite_report: Dict[str, object] = {"changed_files": [], "applied_codes": [], "grouped_findings": {}}
    if isinstance(static_findings, list) and static_findings:
        if auto_fix_findings is not None:
            rewrite_report = auto_fix_findings(repo_dir, project_slug, static_findings, plan)
            auto_fixed_files = list(rewrite_report.get("changed_files", []))
        else:
            auto_fixed_files = auto_fix_subpath_issues(repo_dir, project_slug, plan)
        if auto_fixed_files:
            static_subpath_audit = run_static_subpath_audit(repo_dir, project_slug, plan)
            static_audit_attempts.append(static_subpath_audit)
            static_findings = static_subpath_audit.get("findings", [])
            rewritten_files = sorted_unique(rewritten_files + auto_fixed_files)
    return {
        "plan": plan,
        "detection_evidence": detection_evidence,
        "proxy_mode": proxy_mode,
        "rewritten_files": rewritten_files,
        "auto_fixed_files": auto_fixed_files,
        "rewrite_report": rewrite_report,
        "static_subpath_audit": static_subpath_audit,
        "static_audit_attempts": static_audit_attempts,
        "static_findings": static_findings if isinstance(static_findings, list) else [],
    }


def runtime_subpath_phase(
    host_port: int,
    project_slug: str,
    proxy_mode: str,
    plan: Optional[SubpathPlan] = None,
    *,
    run_runtime_subpath_audit: Callable[[int, str, str, Optional[str], Optional[List[str]]], Dict[str, object]],
) -> Dict[str, object]:
    selection = select_runtime_projects(plan)
    project_count = int(selection["project_count"])
    eligible_project_ids = list(selection["eligible_projects"])
    default_runtime_project_id = selection["default_runtime_project"]
    if plan is not None and plan.projects:
        if not eligible_project_ids:
            runtime_subpath_audit = {
                "checked_paths": [],
                "findings": [],
                "warnings": ["Runtime subpath audit skipped because no project advertises html_entry or runtime_root capability."],
                "skipped_by_capability": True,
                "project_count": project_count,
                "eligible_projects": [],
                "default_runtime_project": None,
                "entry_path_hint": None,
                "entry_path_candidates": [],
                "audited_projects": [],
                "projects": [],
            }
            return {
                "runtime_subpath_audit": runtime_subpath_audit,
                "runtime_findings": [],
            }
    runtime_targets = list(selection.get("runtime_targets", []))
    project_results: List[Dict[str, object]] = []
    project_summaries: List[Dict[str, object]] = []
    combined_checked_paths: List[str] = []
    combined_findings: List[Dict[str, object]] = []
    combined_warnings: List[str] = []
    for target in runtime_targets:
        if not isinstance(target, dict):
            continue
        target_audit = run_runtime_subpath_audit(
            host_port,
            project_slug,
            proxy_mode,
            entry_path_hint=target.get("entry_path_hint"),
            entry_path_candidates=target.get("entry_path_candidates"),
        )
        if isinstance(target_audit, dict):
            target_audit.setdefault("project_id", target.get("project_id"))
            target_audit.setdefault("entry_path_hint", target.get("entry_path_hint"))
            target_audit.setdefault("entry_path_candidates", target.get("entry_path_candidates"))
            project_results.append(target_audit)
            project_summaries.append(
                {
                    "project_id": target_audit.get("project_id"),
                    "checked_paths": len(target_audit.get("checked_paths", [])) if isinstance(target_audit.get("checked_paths", []), list) else 0,
                    "findings": len(target_audit.get("findings", [])) if isinstance(target_audit.get("findings", []), list) else 0,
                    "warnings": len(target_audit.get("warnings", [])) if isinstance(target_audit.get("warnings", []), list) else 0,
                    "entry_path_hint": target_audit.get("entry_path_hint"),
                    "entry_path_candidates": len(target_audit.get("entry_path_candidates", [])) if isinstance(target_audit.get("entry_path_candidates", []), list) else 0,
                }
            )
            combined_checked_paths.extend(target_audit.get("checked_paths", []))
            target_findings = target_audit.get("findings", [])
            if isinstance(target_findings, list):
                for item in target_findings:
                    if isinstance(item, dict):
                        merged = dict(item)
                        merged.setdefault("project_id", target.get("project_id"))
                        combined_findings.append(merged)
            target_warnings = target_audit.get("warnings", [])
            if isinstance(target_warnings, list):
                combined_warnings.extend(str(item) for item in target_warnings)
    runtime_subpath_audit = {
        "checked_paths": sorted({item for item in combined_checked_paths if isinstance(item, str)}),
        "findings": combined_findings,
        "warnings": sorted({item for item in combined_warnings if item}),
        "project_count": project_count,
        "eligible_projects": eligible_project_ids,
        "default_runtime_project": default_runtime_project_id,
        "entry_path_hint": selection["entry_path_hint"],
        "entry_path_candidates": selection["entry_path_candidates"],
        "audited_projects": [result.get("project_id") for result in project_results if isinstance(result.get("project_id"), str)],
        "projects_with_findings": sorted(
            {
                result.get("project_id")
                for result in project_results
                if isinstance(result.get("project_id"), str)
                and isinstance(result.get("findings"), list)
                and result.get("findings")
            }
        ),
        "project_summaries": project_summaries,
        "projects": project_results,
    }
    runtime_findings = runtime_subpath_audit["findings"]
    return {
        "runtime_subpath_audit": runtime_subpath_audit,
        "runtime_findings": runtime_findings if isinstance(runtime_findings, list) else [],
    }


def findings_to_error_texts(findings: List[Dict[str, object]]) -> List[str]:
    errors: List[str] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        finding = SubpathAuditFinding(
            file=str(item.get("file", "")),
            line=int(item.get("line", 1)),
            severity=str(item.get("severity", "error")),
            code=str(item.get("code", "")),
            message=str(item.get("message", "")),
        )
        errors.append(f"{finding.file}:{finding.line} {finding.message}")
    return errors
