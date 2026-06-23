import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from .common import build_deployment_base_path, is_allowed_root_relative_url
from .models import FrontendProjectStrategy, SubpathPlan
from .models import (
    HtmlUrlExtractor,
    SubpathAuditFinding,
    SUBPATH_ALLOWED_ROOT_COMMENT,
    SUBPATH_CLIENT_METHOD_PATTERNS,
    SUBPATH_TEMPLATE_ALLOWLIST_FIELDS,
)


SUBPATH_CLIENT_DIR_MARKERS = ("components", "client", "web", "static", "public", "views", "templates")


def _sorted_unique_paths(items: Iterable[Path]) -> List[Path]:
    return sorted(set(items))


def _paths_under_project(paths: List[Path], project: FrontendProjectStrategy) -> List[Path]:
    scoped: List[Path] = []
    for path in paths:
        try:
            path.resolve().relative_to(project.project_root.resolve())
        except ValueError:
            continue
        scoped.append(path)
    return scoped


def default_project_for_plan(plan: SubpathPlan) -> Optional[FrontendProjectStrategy]:
    if not plan.projects:
        return None
    return next((project for project in plan.projects if project.project_id == plan.default_project), plan.projects[0])


def is_runtime_root_relative_file(
    file_path: Path,
    repo_dir: Path,
    *,
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
) -> bool:
    file_resolved = file_path.resolve()
    for runtime_root in detect_frontend_runtime_roots(repo_dir):
        try:
            file_resolved.relative_to(runtime_root)
        except ValueError:
            continue
        return True
    return False


def is_server_side_code_file(
    file_path: Path,
    repo_dir: Path,
    *,
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
) -> bool:
    relative = file_path.relative_to(repo_dir).as_posix()
    parts = relative.split("/")
    if relative.startswith(("server/", "backend/", "api/")):
        return True
    if any(part in {"scripts", "services"} for part in parts):
        return True
    if file_path.suffix == ".py":
        return True
    basename = file_path.name
    if basename in {"server.js", "server.ts", "app.ts", "api.py", "main.py"}:
        return True
    if basename == "app.js" and not is_runtime_root_relative_file(
        file_path,
        repo_dir,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    ):
        return True
    return False


def discover_runtime_root_frontend_files(
    repo_dir: Path,
    *,
    include_code_files: bool,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    patterns = ["**/*.html"]
    if include_code_files:
        patterns.extend(["**/*.js", "**/*.mjs"])
    targets: List[Path] = []
    seen: Set[Path] = set()
    for runtime_root in detect_frontend_runtime_roots(repo_dir):
        try:
            relative_root = runtime_root.relative_to(repo_dir.resolve())
        except ValueError:
            continue
        if relative_root == Path("."):
            continue
        for suffix_pattern in patterns:
            for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{suffix_pattern}"]):
                relative = file_path.relative_to(repo_dir).as_posix()
                if any(part in relative.split("/") for part in ("node_modules", ".next", "dist", "build")):
                    continue
                if relative.startswith(("server/", "backend/", "api/")):
                    continue
                if is_server_side_code_file(
                    file_path,
                    repo_dir,
                    detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                ):
                    continue
                if file_path not in seen:
                    targets.append(file_path)
                    seen.add(file_path)
    return targets


def _base_audit_targets_for_framework(
    repo_dir: Path,
    framework: str,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    patterns = [
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.js",
        "src/**/*.jsx",
        "app/**/*.ts",
        "app/**/*.tsx",
        "app/**/*.js",
        "app/**/*.jsx",
        "pages/**/*.ts",
        "pages/**/*.tsx",
        "pages/**/*.js",
        "pages/**/*.jsx",
        "components/**/*.ts",
        "components/**/*.tsx",
        "components/**/*.js",
        "components/**/*.jsx",
        "public/**/*.html",
        "public/**/*.js",
        "public/**/*.mjs",
        "static/**/*.html",
        "static/**/*.js",
        "static/**/*.mjs",
        "client/**/*.html",
        "client/**/*.js",
        "client/**/*.mjs",
        "web/**/*.html",
        "web/**/*.js",
        "web/**/*.mjs",
        "views/**/*.html",
        "views/**/*.js",
        "views/**/*.mjs",
        "templates/**/*.html",
        "templates/**/*.js",
        "templates/**/*.mjs",
        "*.html",
    ]
    targets: List[Path] = []
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        if any(part in relative.split("/") for part in ("node_modules", ".next", "dist", "build")):
            continue
        if relative.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")):
            continue
        if relative.startswith(("server/", "backend/", "api/")):
            continue
        if "/pages/api/" in f"/{relative}" or relative.endswith(("/route.ts", "/route.js", "/route.tsx", "/route.jsx")):
            continue
        if is_server_side_code_file(
            file_path,
            repo_dir,
            detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        ):
            continue
        if framework == "nextjs" and relative.endswith((".ts", ".tsx", ".js", ".jsx", ".html")):
            targets.append(file_path)
            continue
        if file_path.suffix in {".html", ".js", ".mjs"}:
            targets.append(file_path)
    targets.extend(
        discover_runtime_root_frontend_files(
            repo_dir,
            include_code_files=True,
            collect_matching_files=collect_matching_files,
            detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        )
    )
    if framework == "vite":
        for project_root in vite_project_roots(repo_dir):
            try:
                relative_root = project_root.relative_to(repo_dir.resolve())
            except ValueError:
                continue
            for pattern in ("index.html", "src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.jsx", "src/**/*.mjs"):
                for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{pattern}"]):
                    if is_server_side_code_file(
                        file_path,
                        repo_dir,
                        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                    ):
                        continue
                    targets.append(file_path)
    return _sorted_unique_paths(targets)


def find_project_audit_targets(
    repo_dir: Path,
    project: FrontendProjectStrategy,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    targets = _base_audit_targets_for_framework(
        repo_dir,
        project.framework,
        collect_matching_files=collect_matching_files,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        vite_project_roots=vite_project_roots,
    )
    for source_root in project.source_roots if "declaration" in project.evidence else ():
        try:
            relative_root = source_root.relative_to(repo_dir.resolve())
        except ValueError:
            continue
        for pattern in ("**/*.html", "**/*.js", "**/*.mjs", "**/*.ts", "**/*.tsx", "**/*.jsx"):
            for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{pattern}"]):
                if is_server_side_code_file(
                    file_path,
                    repo_dir,
                    detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                ):
                    continue
                targets.append(file_path)
    return _paths_under_project(_sorted_unique_paths(targets), project)


def find_subpath_audit_targets(
    repo_dir: Path,
    plan: SubpathPlan,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    targets: List[Path] = []
    for project in plan.projects:
        if project.capabilities and "code_scan" not in project.capabilities and "html_entry" not in project.capabilities:
            continue
        targets.extend(
            find_project_audit_targets(
                repo_dir,
                project,
                collect_matching_files=collect_matching_files,
                detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                vite_project_roots=vite_project_roots,
            )
        )
    return _sorted_unique_paths(targets)


def find_plan_audit_targets(
    repo_dir: Path,
    plan: SubpathPlan,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    return find_subpath_audit_targets(
        repo_dir,
        plan,
        collect_matching_files=collect_matching_files,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        vite_project_roots=vite_project_roots,
    )


def is_allowed_vite_root_html_url(
    url: str,
    file_path: Path,
    repo_dir: Path,
    *,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    stripped = url.strip()
    if not stripped.startswith("/src/"):
        return False
    if file_path.name != "index.html":
        return False
    for project_root in vite_project_roots(repo_dir):
        try:
            file_path.resolve().relative_to(project_root)
        except ValueError:
            continue
        source_candidate = project_root / stripped.lstrip("/")
        return source_candidate.is_file()
    return False


def is_browser_facing_source(
    file_path: Path,
    repo_dir: Path,
    text: str,
    *,
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
) -> bool:
    relative = file_path.relative_to(repo_dir).as_posix()
    if file_path.suffix == ".html":
        return True
    if re.search(r"^\s*['\"]use client['\"]\s*;?\s*$", text, flags=re.MULTILINE):
        return True
    if is_runtime_root_relative_file(
        file_path,
        repo_dir,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    ) and not is_server_side_code_file(
        file_path,
        repo_dir,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    ):
        return True
    parts = set(relative.split("/"))
    return any(marker in parts for marker in SUBPATH_CLIENT_DIR_MARKERS)


def scan_subpath_findings(
    file_path: Path,
    project: FrontendProjectStrategy,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[SubpathAuditFinding]:
    text = read_text(file_path)
    if SUBPATH_ALLOWED_ROOT_COMMENT in text:
        return []
    relative = file_path.relative_to(repo_dir).as_posix()
    findings: List[SubpathAuditFinding] = []
    browser_facing = is_browser_facing_source(
        file_path,
        repo_dir,
        text,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
    )

    if file_path.suffix == ".html":
        parser = HtmlUrlExtractor()
        parser.feed(text)
        for ref in parser.references:
            if project.framework == "vite" and is_allowed_vite_root_html_url(
                ref.url,
                file_path,
                repo_dir,
                vite_project_roots=vite_project_roots,
            ):
                continue
            if is_allowed_root_relative_url(ref.url, base_path):
                continue
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=ref.line,
                    severity="error",
                    code="root_relative_html_url",
                    message=f'Root-relative HTML attribute `{ref.attr}="{ref.url}"` is incompatible with deployment subpath `{base_path}`',
                    detail_kind={
                        "href": "html_link",
                        "src": "html_script" if ref.url.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) or "/src/" in ref.url else "html_asset",
                        "action": "html_form_action",
                    }.get(ref.attr, "html_attribute"),
                )
            )
        return findings

    if project.framework == "nextjs":
        for match in re.finditer(r'<a\b[^>]*\bhref\s*=\s*(["\'])(?P<url>/[^"\']*)\1', text):
            url = match.group("url")
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=line,
                    severity="error",
                    code="nextjs_raw_anchor_root_href",
                    message=f'Use Next navigation instead of raw `<a href="{url}">` for subpath deployment `{base_path}`',
                )
            )
        for match in re.finditer(r'<form\b[^>]*\baction\s*=\s*(["\'])(?P<url>/[^"\']*)\1', text):
            url = match.group("url")
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=line,
                    severity="error",
                    code="nextjs_form_root_action",
                    message=f'Root-relative form action `{url}` is incompatible with deployment subpath `{base_path}`',
                )
            )
        for field_name in SUBPATH_TEMPLATE_ALLOWLIST_FIELDS:
            text = re.sub(rf'\b{field_name}\s*:\s*(["\'`])/(.*?)\1', "", text, flags=re.DOTALL)

    if browser_facing:
        detail_kinds = (
            "request_api",
            "request_api",
            "eventsource",
            "request_api",
            "request_api",
            "navigation",
            "navigation",
            "navigation",
        )
        for pattern, detail_kind in zip(SUBPATH_CLIENT_METHOD_PATTERNS, detail_kinds):
            for match in pattern.finditer(text):
                url = f"/{match.group('path')}"
                if is_allowed_root_relative_url(url, base_path):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    SubpathAuditFinding(
                        file=relative,
                        line=line,
                        severity="error",
                        code="root_relative_client_url",
                        message=f'Root-relative browser URL `{url}` is incompatible with deployment subpath `{base_path}`',
                        detail_kind=detail_kind,
                    )
                )
        for match in re.finditer(r'\breturn\s+(?P<url>(["\'`])/[^"\'`\n;]*\2)', text):
            raw = match.group("url")
            quote = raw[0]
            url = raw.strip(quote)
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                SubpathAuditFinding(
                    file=relative,
                    line=line,
                    severity="error",
                    code="root_relative_client_url",
                    message=f'Root-relative browser URL `{url}` is incompatible with deployment subpath `{base_path}`',
                    detail_kind="return_value",
                )
            )
    return findings


def run_static_subpath_audit(
    repo_dir: Path,
    project_slug: str,
    plan: SubpathPlan,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    vite_project_roots: Callable[[Path], List[Path]],
    read_text: Callable[[Path], str],
) -> Dict[str, object]:
    base_path = build_deployment_base_path(project_slug)
    targets = find_subpath_audit_targets(
        repo_dir,
        plan,
        collect_matching_files=collect_matching_files,
        detect_frontend_runtime_roots=detect_frontend_runtime_roots,
        vite_project_roots=vite_project_roots,
    )
    findings: List[SubpathAuditFinding] = []
    project_lookup: Dict[str, FrontendProjectStrategy] = {}
    for project in plan.projects:
        for file_path in find_project_audit_targets(
            repo_dir,
            project,
            collect_matching_files=collect_matching_files,
            detect_frontend_runtime_roots=detect_frontend_runtime_roots,
            vite_project_roots=vite_project_roots,
        ):
            project_lookup[file_path.relative_to(repo_dir).as_posix()] = project
    default_project = default_project_for_plan(plan)
    for file_path in targets:
        relative_path = file_path.relative_to(repo_dir).as_posix()
        project = project_lookup.get(relative_path, default_project)
        if project is None:
            continue
        findings.extend(
            scan_subpath_findings(
                file_path,
                project,
                base_path,
                repo_dir,
                read_text=read_text,
                detect_frontend_runtime_roots=detect_frontend_runtime_roots,
                vite_project_roots=vite_project_roots,
            )
        )
    return {
        "framework": default_project.framework if default_project is not None else None,
        "proxy_mode": default_project.proxy_mode if default_project is not None else None,
        "scanned_files": [path.relative_to(repo_dir).as_posix() for path in targets],
        "findings": [
            {
                "file": item.file,
                "line": item.line,
                "severity": item.severity,
                "code": item.code,
                "message": item.message,
                "detail_kind": item.detail_kind,
            }
            for item in findings
        ],
        "warnings": [],
    }
