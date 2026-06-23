from typing import Callable, Dict, List, Optional, Tuple

from .common import build_deployment_base_path, is_allowed_root_relative_url
from .runtime_urls import extract_html_urls, normalize_runtime_url_path, translate_external_to_upstream_path, translate_upstream_to_external_path


def sorted_unique(items: List[str]) -> List[str]:
    return sorted({item for item in items if item})


def runtime_subpath_findings_for_html(
    html: str,
    current_path: str,
    base_path: str,
    *,
    base_origin: Optional[str] = None,
) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    for ref in extract_html_urls(html):
        normalized = normalize_runtime_url_path(ref.url, current_path, base_origin=base_origin)
        if normalized is None or is_allowed_root_relative_url(normalized, base_path):
            continue
        findings.append(
            {
                "path": current_path,
                "line": ref.line,
                "tag": ref.tag,
                "attr": ref.attr,
                "url": ref.url,
                "message": f'Runtime HTML emits root-relative `{ref.attr}="{ref.url}"` outside deployment subpath `{base_path}`',
            }
        )
    return findings


def collect_runtime_follow_links(
    html: str,
    current_path: str,
    base_path: str,
    *,
    base_origin: str,
    limit: int = 5,
) -> List[str]:
    paths: List[str] = []
    for ref in extract_html_urls(html):
        if ref.attr != "href":
            continue
        normalized = normalize_runtime_url_path(ref.url, current_path, base_origin=base_origin)
        if normalized is None:
            continue
        if normalized == current_path:
            continue
        if normalized == base_path or normalized.startswith(f"{base_path}/"):
            paths.append(normalized)
        if len(paths) >= limit:
            break
    return sorted_unique(paths)


def run_runtime_subpath_audit(
    host_port: int,
    project_slug: str,
    proxy_mode: str,
    entry_path_hint: Optional[str] = None,
    entry_path_candidates: Optional[List[str]] = None,
    *,
    external_access_host: str,
    fetch_http_text: Callable[[int, str], Tuple[int, Dict[str, str], str, str]],
) -> Dict[str, object]:
    base_path = build_deployment_base_path(project_slug)
    base_origin = f"https://{external_access_host}"
    checked_paths: List[str] = []
    findings: List[Dict[str, object]] = []
    warnings: List[str] = []
    candidate_hints = entry_path_candidates or ([entry_path_hint] if entry_path_hint else ["/"])
    candidate_hints = [hint if hint is not None else "/" for hint in candidate_hints] or ["/"]
    selected_entry_hint = candidate_hints[0]
    status = 0
    headers: Dict[str, str] = {}
    html = ""
    final_path = ""
    external_final_path = ""
    for hint in candidate_hints:
        if proxy_mode == "preserve_prefix":
            external_entry_path = base_path
        else:
            if hint == "/":
                external_entry_path = f"{base_path}/"
            else:
                external_entry_path = f"{base_path}{hint if hint.startswith('/') else '/' + hint}"
        entry_path = translate_external_to_upstream_path(external_entry_path, base_path, proxy_mode)
        status, headers, html, final_path = fetch_http_text(host_port, entry_path)
        external_final_path = translate_upstream_to_external_path(final_path, base_path, proxy_mode)
        checked_paths.append(external_final_path)
        content_type = headers.get("content-type", "")
        if status < 400 and ("html" in content_type or "<html" in html.lower()):
            selected_entry_hint = hint
            break
    if status >= 400:
        findings.append(
            {
                "path": external_final_path,
                "line": 1,
                "tag": "document",
                "attr": "status",
                "url": external_final_path,
                "message": f"Runtime subpath audit received HTTP {status} for {external_final_path}",
            }
        )
        return {
            "checked_paths": sorted_unique(checked_paths),
            "findings": findings,
            "warnings": warnings,
            "entry_path_hint": selected_entry_hint,
            "entry_path_candidates": candidate_hints,
        }

    content_type = headers.get("content-type", "")
    if "html" not in content_type and "<html" not in html.lower():
        warnings.append(f"Runtime subpath audit skipped HTML extraction for {external_final_path} because response is not HTML.")
        return {
            "checked_paths": sorted_unique(checked_paths),
            "findings": findings,
            "warnings": warnings,
            "entry_path_hint": selected_entry_hint,
            "entry_path_candidates": candidate_hints,
        }

    findings.extend(runtime_subpath_findings_for_html(html, external_final_path, base_path, base_origin=base_origin))
    current_entry_path = external_final_path if proxy_mode == "strip_prefix" else base_path
    for candidate in collect_runtime_follow_links(html, current_entry_path, base_path, base_origin=base_origin):
        upstream_candidate = translate_external_to_upstream_path(candidate, base_path, proxy_mode)
        status, headers, child_html, child_final_path = fetch_http_text(host_port, upstream_candidate)
        external_child_final_path = translate_upstream_to_external_path(child_final_path, base_path, proxy_mode)
        checked_paths.append(external_child_final_path)
        if status >= 400:
            findings.append(
                {
                    "path": external_child_final_path,
                    "line": 1,
                    "tag": "document",
                    "attr": "status",
                    "url": external_child_final_path,
                    "message": f"Runtime subpath audit received HTTP {status} for {external_child_final_path} (upstream {upstream_candidate})",
                }
            )
            continue
        child_content_type = headers.get("content-type", "")
        if "html" not in child_content_type and "<html" not in child_html.lower():
            continue
        findings.extend(runtime_subpath_findings_for_html(child_html, external_child_final_path, base_path, base_origin=base_origin))

    return {
        "checked_paths": sorted_unique(checked_paths),
        "findings": findings,
        "warnings": warnings,
        "entry_path_hint": selected_entry_hint,
        "entry_path_candidates": candidate_hints,
    }
