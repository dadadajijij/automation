from pathlib import Path
from typing import Callable, Dict, List, Optional

from .common import build_deployment_base_path
from .models import FrontendProjectStrategy, SubpathPlan


def group_subpath_findings_by_code(findings: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for item in findings:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip() or "unknown"
        grouped.setdefault(code, []).append(item)
    return grouped


def default_project_for_plan(plan: SubpathPlan) -> Optional[FrontendProjectStrategy]:
    if not plan.projects:
        return None
    return next((project for project in plan.projects if project.project_id == plan.default_project), plan.projects[0])


def default_strategy_from_plan(plan: SubpathPlan) -> Dict[str, str]:
    project = default_project_for_plan(plan)
    if project is None:
        return {"framework": "generic", "proxy_mode": "strip_prefix", "adapter": "static_rewrite"}
    return {
        "framework": project.framework,
        "proxy_mode": project.proxy_mode,
        "adapter": project.adapter,
    }


def auto_fix_findings(
    repo_dir: Path,
    project_slug: str,
    findings: List[Dict[str, object]],
    plan: SubpathPlan,
    *,
    auto_fix_nextjs_subpath_issues_fn: Callable[[Path, str], List[str]],
    find_subpath_audit_targets: Callable[[Path, SubpathPlan], List[Path]],
    rewrite_frontend_subpath_urls: Callable[[Path, str, Path], bool],
    rewrite_frontend_html_attribute_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_html_link_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_html_script_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_html_form_action_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_client_request_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_request_api_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_navigation_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_eventsource_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    rewrite_frontend_return_value_urls: Optional[Callable[[Path, str, Path], bool]] = None,
    sorted_unique: Callable[[List[str]], List[str]],
) -> Dict[str, object]:
    strategy = default_strategy_from_plan(plan)
    grouped = group_subpath_findings_by_code(findings)
    changed: List[str] = []
    applied_codes: List[str] = []
    applied_codes_by_file: Dict[str, List[str]] = {}
    applied_codes_by_reason: Dict[str, List[str]] = {}
    unchanged_reason_overrides: Dict[str, str] = {}
    framework = strategy.get("framework")
    adapter = str(strategy.get("adapter", "")).strip()
    project_capabilities = set()
    if plan.projects:
        default_project = next((project for project in plan.projects if project.project_id == plan.default_project), plan.projects[0])
        project_capabilities = set(default_project.capabilities)
    supported_codes = {
        "nextjs": {"nextjs_raw_anchor_root_href", "nextjs_form_root_action", "root_relative_client_url"},
        "static_rewrite": {"root_relative_html_url", "root_relative_client_url"},
    }
    framework_specific_codes = {
        "nextjs": {"nextjs_raw_anchor_root_href", "nextjs_form_root_action"},
    }
    build_output_codes = {
        "build_output_html_root_relative_url",
        "build_output_client_root_relative_url",
        "build_output_manifest_root_relative_url",
    }
    if framework == "nextjs" and (not project_capabilities or "client_code" in project_capabilities) and any(code in grouped for code in ("nextjs_raw_anchor_root_href", "nextjs_form_root_action", "root_relative_client_url")):
        changed.extend(auto_fix_nextjs_subpath_issues_fn(repo_dir, project_slug))
        if changed:
            codes = [code for code in grouped if code in {"nextjs_raw_anchor_root_href", "nextjs_form_root_action", "root_relative_client_url"}]
            applied_codes.extend(codes)
            applied_codes_by_reason.setdefault("framework_specific_rewrite", []).extend(code for code in codes if code not in applied_codes_by_reason.get("framework_specific_rewrite", []))
            for file_name in changed:
                applied_codes_by_file.setdefault(file_name, []).extend(code for code in codes if code not in applied_codes_by_file.get(file_name, []))
        else:
            for code in grouped:
                if code in {"nextjs_raw_anchor_root_href", "nextjs_form_root_action", "root_relative_client_url"}:
                    unchanged_reason_overrides[code] = "rewriter_made_no_changes"
    generic_rewrite_adapters = {"static_rewrite", "vite", "vue_cli", "cra", "nextjs"}
    supports_html_fix = not project_capabilities or "html_entry" in project_capabilities
    supports_client_fix = not project_capabilities or "client_code" in project_capabilities
    if adapter in generic_rewrite_adapters:
        if "root_relative_html_url" in grouped and not supports_html_fix:
            unchanged_reason_overrides.setdefault("root_relative_html_url", "file_type_not_supported")
        if "root_relative_client_url" in grouped and not supports_client_fix:
            unchanged_reason_overrides.setdefault("root_relative_client_url", "file_type_not_supported")
    if adapter in generic_rewrite_adapters and any(
        (
            code == "root_relative_html_url" and supports_html_fix
        ) or (
            code == "root_relative_client_url" and supports_client_fix
        )
        for code in grouped
    ):
        base_path = build_deployment_base_path(project_slug)
        candidate_targets = {path.relative_to(repo_dir).as_posix(): path for path in find_subpath_audit_targets(repo_dir, plan)}
        static_rewrite_specs = (
            ("root_relative_html_url", "html_link_rewrite", lambda file_name, item: supports_html_fix and file_name.endswith(".html") and str(item.get("detail_kind", "")) == "html_link", rewrite_frontend_html_link_urls or rewrite_frontend_html_attribute_urls),
            ("root_relative_html_url", "html_script_rewrite", lambda file_name, item: supports_html_fix and file_name.endswith(".html") and str(item.get("detail_kind", "")) in {"html_script", "html_asset"}, rewrite_frontend_html_script_urls or rewrite_frontend_html_attribute_urls),
            ("root_relative_html_url", "html_form_action_rewrite", lambda file_name, item: supports_html_fix and file_name.endswith(".html") and str(item.get("detail_kind", "")) == "html_form_action", rewrite_frontend_html_form_action_urls or rewrite_frontend_html_attribute_urls),
            ("root_relative_html_url", "html_attribute_rewrite", lambda file_name, item: supports_html_fix and file_name.endswith(".html") and not str(item.get("detail_kind", "")).strip(), rewrite_frontend_html_attribute_urls),
            ("root_relative_client_url", "request_api_rewrite", lambda file_name, item: supports_client_fix and file_name.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) and (str(item.get("detail_kind", "")) == "request_api" or not str(item.get("detail_kind", "")).strip()), rewrite_frontend_request_api_urls or rewrite_frontend_client_request_urls),
            ("root_relative_client_url", "navigation_rewrite", lambda file_name, item: supports_client_fix and file_name.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) and str(item.get("detail_kind", "")) == "navigation", rewrite_frontend_navigation_urls or rewrite_frontend_client_request_urls),
            ("root_relative_client_url", "eventsource_rewrite", lambda file_name, item: supports_client_fix and file_name.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) and str(item.get("detail_kind", "")) == "eventsource", rewrite_frontend_eventsource_urls or rewrite_frontend_client_request_urls),
            ("root_relative_client_url", "return_value_rewrite", lambda file_name, item: supports_client_fix and file_name.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) and str(item.get("detail_kind", "")) == "return_value", rewrite_frontend_return_value_urls or rewrite_frontend_client_request_urls),
        )
        for code, reason, predicate, rewriter in static_rewrite_specs:
            touched_for_code: List[str] = []
            relevant_files = sorted(
                {
                    str(item.get("file", "")).strip()
                    for item in grouped.get(code, [])
                    if isinstance(item, dict) and str(item.get("file", "")).strip()
                }
            )
            targeted_files = sorted(
                {
                    str(item.get("file", "")).strip()
                    for item in grouped.get(code, [])
                    if isinstance(item, dict) and str(item.get("file", "")).strip() and predicate(str(item.get("file", "")).strip(), item)
                }
            )
            for file_name in targeted_files:
                file_path = candidate_targets.get(file_name)
                if file_path is None:
                    continue
                if rewriter is not None:
                    rewritten = rewriter(file_path, base_path, repo_dir)
                else:
                    rewritten = rewrite_frontend_subpath_urls(file_path, base_path, repo_dir)
                if rewritten:
                    changed.append(file_name)
                    touched_for_code.append(file_name)
            if touched_for_code:
                applied_codes.append(code)
                applied_codes_by_reason.setdefault(reason, []).append(code)
                for file_name in touched_for_code:
                    applied_codes_by_file.setdefault(file_name, []).append(code)
            elif grouped.get(code):
                if code not in unchanged_reason_overrides:
                    if code == "root_relative_html_url" and not supports_html_fix:
                        unchanged_reason_overrides[code] = "file_type_not_supported"
                    elif code == "root_relative_client_url" and not supports_client_fix:
                        unchanged_reason_overrides[code] = "file_type_not_supported"
                    elif targeted_files and not any(file_name in candidate_targets for file_name in targeted_files):
                        unchanged_reason_overrides[code] = "target_not_discovered"
                    elif not targeted_files and relevant_files:
                        unchanged_reason_overrides[code] = "file_type_not_supported"
                    elif targeted_files:
                        unchanged_reason_overrides[code] = "rewriter_made_no_changes"
    applied_codes = sorted({code for code in applied_codes if code})
    unchanged_codes = sorted(code for code in grouped if code not in applied_codes)
    unchanged_codes_by_reason: Dict[str, List[str]] = {}
    unchanged_codes_by_reason_and_file: Dict[str, Dict[str, List[str]]] = {}
    for code in unchanged_codes:
        files = sorted(
            {
                str(item.get("file", "")).strip()
                for item in grouped.get(code, [])
                if isinstance(item, dict) and str(item.get("file", "")).strip()
            }
        )
        if framework == "nextjs":
            known_codes = supported_codes["nextjs"]
        elif adapter in generic_rewrite_adapters:
            known_codes = supported_codes["static_rewrite"]
        else:
            known_codes = set()
        if code in build_output_codes:
            reason = "build_output_only"
        elif any(code in codes and framework != owner for owner, codes in framework_specific_codes.items()):
            reason = "framework_specific_only"
        elif code not in known_codes:
            reason = "no_matching_fixer"
        else:
            reason = unchanged_reason_overrides.get(code, "no_target_files_changed")
        unchanged_codes_by_reason.setdefault(reason, []).append(code)
        unchanged_codes_by_reason_and_file.setdefault(reason, {})
        for file_name in files:
            unchanged_codes_by_reason_and_file[reason].setdefault(file_name, []).append(code)
    return {
        "changed_files": sorted_unique(changed),
        "applied_codes": applied_codes,
        "applied_codes_by_file": {key: sorted(value) for key, value in sorted(applied_codes_by_file.items())},
        "applied_codes_by_reason": {key: sorted(value) for key, value in sorted(applied_codes_by_reason.items())},
        "unchanged_codes": unchanged_codes,
        "unchanged_codes_by_reason": {key: sorted(value) for key, value in sorted(unchanged_codes_by_reason.items())},
        "unchanged_codes_by_reason_and_file": {
            reason: {file_name: sorted(values) for file_name, values in sorted(file_map.items())}
            for reason, file_map in sorted(unchanged_codes_by_reason_and_file.items())
        },
        "grouped_findings": grouped,
    }


def auto_fix_subpath_issues(
    repo_dir: Path,
    project_slug: str,
    plan: SubpathPlan,
    *,
    auto_fix_nextjs_subpath_issues_fn: Callable[[Path, str], List[str]],
    find_subpath_audit_targets: Callable[[Path, SubpathPlan], List[Path]],
    rewrite_frontend_subpath_urls: Callable[[Path, str, Path], bool],
    sorted_unique: Callable[[List[str]], List[str]],
) -> List[str]:
    strategy = default_strategy_from_plan(plan)
    framework = strategy.get("framework")
    if framework == "nextjs":
        return auto_fix_nextjs_subpath_issues_fn(repo_dir, project_slug)
    if strategy.get("adapter") == "static_rewrite":
        base_path = build_deployment_base_path(project_slug)
        changed: List[str] = []
        for file_path in find_subpath_audit_targets(repo_dir, plan):
            if rewrite_frontend_subpath_urls(file_path, base_path, repo_dir):
                changed.append(file_path.relative_to(repo_dir).as_posix())
        return sorted_unique(changed)
    return []


def apply_subpath_rewrites(
    repo_dir: Path,
    project_slug: Optional[str],
    *,
    plan: SubpathPlan,
    apply_framework_config_adapters: Callable[[SubpathPlan, str], List[str]],
    apply_nextjs_subpath_adapter_fn: Callable[[Path, str], List[str]],
    apply_vite_subpath_adapter_fn: Callable[[Path, str], List[str]],
    ensure_vue_cli_public_path_fn: Callable[[Path, str], List[str]],
    ensure_cra_homepage_fn: Callable[[Path, str], List[str]],
    find_frontend_rewrite_targets: Callable[[Path], List[Path]],
    rewrite_frontend_subpath_urls: Callable[[Path, str, Path], bool],
) -> List[str]:
    if not isinstance(project_slug, str) or not project_slug.strip():
        return []
    changed: List[str] = apply_framework_config_adapters(plan, project_slug)
    base_path = build_deployment_base_path(project_slug)
    for project in plan.projects:
        if "config_adapter" not in project.capabilities and "client_code" not in project.capabilities and "html_entry" not in project.capabilities:
            continue
        if project.source_adapter == "nextjs":
            changed.extend(apply_nextjs_subpath_adapter_fn(repo_dir, project_slug))
            continue
        if project.source_adapter == "vite":
            changed.extend(apply_vite_subpath_adapter_fn(repo_dir, project_slug))
            continue
        if project.source_adapter == "vue_cli":
            changed.extend(ensure_vue_cli_public_path_fn(repo_dir, project_slug))
            continue
        if project.source_adapter == "cra":
            changed.extend(ensure_cra_homepage_fn(repo_dir, project_slug))
            continue
    for file_path in find_frontend_rewrite_targets(repo_dir):
        if rewrite_frontend_subpath_urls(file_path, base_path, repo_dir):
            changed.append(file_path.relative_to(repo_dir).as_posix())
    return sorted({item for item in changed if item})
