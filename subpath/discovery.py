import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from .common import slugify
from .models import FrameworkCandidate, FrontendProjectStrategy, RuntimeRootEvidence, SubpathPlan


def resolve_entrypoint_relative_dir(entry_file: Path, args_text: str) -> Optional[Path]:
    quoted_parts = re.findall(r'["\']([^"\']+)["\']', args_text)
    if not quoted_parts:
        return None
    candidate = entry_file.parent
    for part in quoted_parts:
        candidate = candidate / Path(part)
    resolved = candidate.resolve()
    return resolved if resolved.exists() and resolved.is_dir() else None


def resolve_entrypoint_relative_dir_hint(entry_file: Path, args_text: str) -> Optional[Path]:
    quoted_parts = re.findall(r'["\']([^"\']+)["\']', args_text)
    if not quoted_parts:
        return None
    candidate = entry_file.parent
    for part in quoted_parts:
        candidate = candidate / Path(part)
    return candidate.resolve()


def detect_static_root_hints_from_node_entry(
    repo_dir: Path,
    entry_file: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[Path]:
    text = read_text_if_exists(entry_file)
    if not text:
        return []

    repo_root = repo_dir.resolve()
    variable_dirs: Dict[str, Path] = {}
    hints: List[Path] = []
    seen: Set[Path] = set()

    assignment_pattern = re.compile(r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*;?')
    for match in assignment_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir(entry_file, match.group(2))
        if resolved is None:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        variable_dirs[match.group(1)] = resolved

    inline_pattern = re.compile(r'(?:express\.static|serveStatic|koaStatic|root\s*:)\s*[\(\s]*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*\)?')
    for match in inline_pattern.finditer(text):
        resolved = resolve_entrypoint_relative_dir(entry_file, match.group(1))
        if resolved is None or resolved in seen:
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        hints.append(resolved)
        seen.add(resolved)

    variable_pattern = re.compile(r'(?:express\.static|serveStatic|koaStatic)\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)|root\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)')
    for match in variable_pattern.finditer(text):
        variable_name = match.group(1) or match.group(2)
        if not variable_name:
            continue
        resolved = variable_dirs.get(variable_name)
        if resolved is None or resolved in seen:
            continue
        hints.append(resolved)
        seen.add(resolved)

    return hints


def detect_node_runtime_root_evidence_from_entry(
    repo_dir: Path,
    entry_file: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    entry_text = read_text_if_exists(entry_file) or ""
    for runtime_root in detect_static_root_hints_from_node_entry(
        repo_dir,
        entry_file,
        read_text_if_exists=read_text_if_exists,
    ):
        evidence.append(
            RuntimeRootEvidence(
                path=runtime_root,
                source="node_static_call",
                confidence="high",
                detail=f"detected from node entry {entry_file.relative_to(repo_dir).as_posix()}",
            )
        )
    if not evidence:
        repo_root = repo_dir.resolve()
        assignment_pattern = re.compile(r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*path\.(?:join|resolve)\(\s*__dirname\s*,\s*(.*?)\)\s*;?')
        variable_dirs: Dict[str, Path] = {}
        for match in assignment_pattern.finditer(entry_text):
            resolved = resolve_entrypoint_relative_dir_hint(entry_file, match.group(2))
            if resolved is None:
                continue
            try:
                resolved.relative_to(repo_root)
            except ValueError:
                continue
            variable_dirs[match.group(1)] = resolved

        root_property_pattern = re.compile(r'root\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)')
        for match in root_property_pattern.finditer(entry_text):
            resolved = variable_dirs.get(match.group(1))
            if resolved is None:
                continue
            evidence.append(
                RuntimeRootEvidence(
                    path=resolved,
                    source="node_static_root_reference",
                    confidence="high",
                    detail=f"referenced by static root in node entry {entry_file.relative_to(repo_dir).as_posix()}",
                )
            )
    return evidence


def workspace_frontend_package_dirs(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    discover_workspace_packages: Callable[[Path, Dict[str, object]], List[Dict[str, object]]],
) -> List[Path]:
    root_package = parse_package_json(repo_dir)
    workspace_packages = discover_workspace_packages(repo_dir, root_package)
    targets: List[Path] = []
    for package_info in workspace_packages:
        package_dir = package_info.get("package_dir")
        dependencies = package_info.get("dependencies", [])
        if not isinstance(package_dir, Path) or not isinstance(dependencies, list):
            continue
        dep_set = {str(dep).lower() for dep in dependencies}
        if dep_set.intersection({"vite", "react", "react-dom", "vue", "svelte", "@vitejs/plugin-react"}):
            targets.append(package_dir.resolve())
            continue
        scripts = package_info.get("scripts", {})
        if isinstance(scripts, dict) and any(isinstance(value, str) and "vite" in value for value in scripts.values()):
            targets.append(package_dir.resolve())
    return sorted(set(targets))


def discover_frontend_packages(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    discover_workspace_packages: Callable[[Path, Dict[str, object]], List[Dict[str, object]]],
) -> List[Path]:
    packages: List[Path] = [repo_dir.resolve()]
    packages.extend(
        workspace_frontend_package_dirs(
            repo_dir,
            parse_package_json=parse_package_json,
            discover_workspace_packages=discover_workspace_packages,
        )
    )
    return sorted(set(packages))


def vite_project_roots(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
) -> List[Path]:
    targets: List[Path] = []
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    if (
        "vite" in dependencies
        or "vite" in dev_dependencies
        or any((repo_dir / candidate).exists() for candidate in ("vite.config.ts", "vite.config.js", "vite.config.mjs"))
        or any(isinstance(value, str) and "vite" in value for value in scripts.values())
    ):
        targets.append(repo_dir.resolve())
    targets.extend(workspace_frontend_package_dirs_fn(repo_dir))
    return sorted(set(targets))


def collect_python_frontend_hint_files(
    repo_dir: Path,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> List[Path]:
    patterns = [
        "server.py",
        "app.py",
        "main.py",
        "api.py",
        "asgi.py",
        "wsgi.py",
        "*/server.py",
        "*/app.py",
        "*/main.py",
        "*/api.py",
        "*/asgi.py",
        "*/wsgi.py",
        "*/*/server.py",
        "*/*/app.py",
        "*/*/main.py",
        "*/*/api.py",
        "*/*/asgi.py",
        "*/*/wsgi.py",
        "app/**/*.py",
        "backend/**/*.py",
        "server/**/*.py",
        "src/**/*.py",
    ]
    targets: List[Path] = []
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        if any(part in relative.split("/") for part in ("tests", "vendor", ".venv", "node_modules", "__pycache__", "docs")):
            continue
        targets.append(file_path)
    return sorted(set(targets))


def normalize_python_hint_path(expr: str) -> Optional[str]:
    quoted_parts = [part.strip() for part in re.findall(r'["\']([^"\']+)["\']', expr) if part.strip()]
    if not quoted_parts:
        return None
    if len(quoted_parts) == 1:
        return quoted_parts[0]
    return Path(*quoted_parts).as_posix()


def resolve_python_hint_directory(repo_dir: Path, source_file: Path, path_text: str) -> List[Path]:
    raw_path = path_text.strip()
    if not raw_path or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_path):
        return []
    repo_root = repo_dir.resolve()
    resolved_dirs: List[Path] = []
    seen: Set[Path] = set()

    def add_candidate(candidate: Path) -> None:
        resolved = candidate.resolve()
        if not resolved.is_dir():
            return
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            return
        if resolved not in seen:
            resolved_dirs.append(resolved)
            seen.add(resolved)

    candidate_path = Path(raw_path)
    if candidate_path.is_absolute():
        add_candidate(candidate_path)
        return resolved_dirs

    if raw_path in {".", ".."} or raw_path.startswith(("./", "../")):
        add_candidate(source_file.parent.resolve() / candidate_path)
        return resolved_dirs

    current = source_file.parent.resolve()
    while True:
        add_candidate(current / candidate_path)
        if current == repo_root or current.parent == current:
            break
        current = current.parent
    add_candidate(repo_root / candidate_path)
    return resolved_dirs


def detect_frontend_root_hints_from_python_file(
    repo_dir: Path,
    source_file: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[Path]:
    text = read_text_if_exists(source_file)
    if not text:
        return []

    patterns = (
        re.compile(r'Jinja2Templates\(\s*directory\s*=\s*(?P<expr>[^)\n]+)\)'),
        re.compile(r'StaticFiles\(\s*directory\s*=\s*(?P<expr>[^)\n]+)\)'),
        re.compile(r'\btemplate_folder\s*=\s*(?P<expr>[^,\)\n]+)'),
        re.compile(r'\bstatic_folder\s*=\s*(?P<expr>[^,\)\n]+)'),
    )
    hints: List[Path] = []
    seen: Set[Path] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            path_text = normalize_python_hint_path(match.group("expr"))
            if not path_text:
                continue
            for resolved in resolve_python_hint_directory(repo_dir, source_file, path_text):
                if resolved not in seen:
                    hints.append(resolved)
                    seen.add(resolved)
    return hints


def detect_python_runtime_root_evidence_from_file(
    repo_dir: Path,
    source_file: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    for runtime_root in detect_frontend_root_hints_from_python_file(
        repo_dir,
        source_file,
        read_text_if_exists=read_text_if_exists,
    ):
        source_kind = "python_template_decl" if runtime_root.name in {"views", "templates"} else "python_static_decl"
        evidence.append(
            RuntimeRootEvidence(
                path=runtime_root,
                source=source_kind,
                confidence="high",
                detail=f"detected from python file {source_file.relative_to(repo_dir).as_posix()}",
            )
        )
    return evidence


def detect_workspace_runtime_root_evidence(
    repo_dir: Path,
    *,
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    for workspace_dir in workspace_frontend_package_dirs_fn(repo_dir):
        for anchor in ("src", "static", "public", "client", "web"):
            candidate = (workspace_dir / anchor).resolve()
            if candidate.is_dir():
                evidence.append(
                    RuntimeRootEvidence(
                        path=candidate,
                        source="workspace_anchor",
                        confidence="medium",
                        detail=f"workspace frontend package anchor {workspace_dir.relative_to(repo_dir).as_posix()}",
                    )
                )
    return evidence


def detect_directory_hint_evidence(repo_dir: Path) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    for anchor in ("src", "static", "public", "client", "web", "views", "templates", "audioqas/web/static"):
        candidate = (repo_dir / anchor).resolve()
        if candidate.is_dir():
            evidence.append(
                RuntimeRootEvidence(
                    path=candidate,
                    source="directory_hint",
                    confidence="low",
                    detail=f"repo contains directory anchor {anchor}",
                )
            )
    return evidence


def detect_html_cluster_evidence(
    repo_dir: Path,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    seen: Set[Path] = set()
    html_files = collect_matching_files(repo_dir, ["*.html", "**/*.html"])
    for cluster_root in sorted({html_file.parent.resolve() for html_file in html_files}):
        if cluster_root in seen:
            continue
        html_pages = collect_matching_files(cluster_root, ["*.html"])
        if not html_pages:
            continue
        has_index_html = any(page.name == "index.html" for page in html_pages)
        sibling_assets = collect_matching_files(cluster_root, ["*.js", "*.mjs", "*.css"])
        nested_html_pages = collect_matching_files(cluster_root, ["*/*.html", "*/*/*.html"])
        shared_asset_dirs = []
        for asset_dir_name in ("assets", "static", "public"):
            asset_dir = (cluster_root / asset_dir_name).resolve()
            if not asset_dir.is_dir():
                continue
            if collect_matching_files(asset_dir, ["**/*.js", "**/*.mjs", "**/*.css"]):
                shared_asset_dirs.append(asset_dir_name)
        signals: List[str] = []
        if has_index_html and sibling_assets:
            signals.append("index_html_with_sibling_assets")
        if len(html_pages) >= 2:
            signals.append("multiple_html_pages")
        if has_index_html and nested_html_pages:
            signals.append("index_html_with_nested_pages")
        if shared_asset_dirs:
            signals.append(f"shared_asset_dirs={','.join(shared_asset_dirs)}")
        if not signals:
            continue
        evidence.append(
            RuntimeRootEvidence(
                path=cluster_root,
                source="html_cluster",
                confidence="medium",
                detail=f"directory looks like html cluster ({'; '.join(signals)}): {cluster_root.relative_to(repo_dir).as_posix()}",
            )
        )
        seen.add(cluster_root)
    return evidence


def detect_runtime_root_evidence(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
    collect_python_frontend_hint_files_fn: Callable[[Path], List[Path]],
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> List[RuntimeRootEvidence]:
    evidence: List[RuntimeRootEvidence] = []
    package = parse_package_json(repo_dir)
    package_scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    for entry_file in detect_node_entry_script_paths(repo_dir, package_scripts):
        evidence.extend(
            detect_node_runtime_root_evidence_from_entry(
                repo_dir,
                entry_file,
                read_text_if_exists=read_text_if_exists,
            )
        )
    for source_file in collect_python_frontend_hint_files_fn(repo_dir):
        evidence.extend(
            detect_python_runtime_root_evidence_from_file(
                repo_dir,
                source_file,
                read_text_if_exists=read_text_if_exists,
            )
        )
    evidence.extend(detect_workspace_runtime_root_evidence(repo_dir, workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs_fn))
    evidence.extend(detect_html_cluster_evidence(repo_dir, collect_matching_files=collect_matching_files))
    evidence.extend(detect_directory_hint_evidence(repo_dir))
    return evidence


def classify_runtime_root_groups(
    evidence: List[RuntimeRootEvidence],
) -> Dict[str, tuple[Path, ...]]:
    service_roots: List[Path] = []
    page_cluster_roots: List[Path] = []
    hinted_roots: List[Path] = []
    service_seen: Set[Path] = set()
    page_cluster_seen: Set[Path] = set()
    hinted_seen: Set[Path] = set()
    for item in evidence:
        if item.source == "html_cluster":
            if item.path not in service_seen and item.path not in page_cluster_seen:
                page_cluster_roots.append(item.path)
                page_cluster_seen.add(item.path)
            continue
        if item.confidence == "high":
            if item.path not in service_seen:
                service_roots.append(item.path)
                service_seen.add(item.path)
            continue
        if item.path in service_seen or item.path in page_cluster_seen or item.path in hinted_seen:
            continue
        hinted_roots.append(item.path)
        hinted_seen.add(item.path)
    return {
        "service_roots": tuple(service_roots),
        "page_cluster_roots": tuple(page_cluster_roots),
        "hinted_roots": tuple(hinted_roots),
    }


def classify_runtime_roots(
    evidence: List[RuntimeRootEvidence],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    groups = classify_runtime_root_groups(evidence)
    confirmed = tuple([*groups["service_roots"], *groups["page_cluster_roots"]])
    hinted = groups["hinted_roots"]
    return confirmed, hinted


def detect_framework_candidates(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    detect_frontend_runtime_roots_fn: Callable[[Path], List[Path]],
    vite_project_roots_fn: Callable[[Path], List[Path]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    discover_workspace_packages: Optional[Callable[[Path, Dict[str, object]], List[Dict[str, object]]]] = None,
) -> List[FrameworkCandidate]:
    candidates: List[FrameworkCandidate] = []
    package_roots = [repo_dir.resolve()]
    if discover_workspace_packages is not None:
        package_roots = discover_frontend_packages(
            repo_dir,
            parse_package_json=parse_package_json,
            discover_workspace_packages=discover_workspace_packages,
        )
    for package_root in package_roots:
        if is_nextjs_project(package_root, parse_package_json=parse_package_json):
            candidates.append(FrameworkCandidate("nextjs", package_root, "high", ("next dependency or config detected",)))
        elif is_vite_project(package_root, vite_project_roots_fn=vite_project_roots_fn):
            candidates.append(FrameworkCandidate("vite", package_root, "high", ("vite dependency/config/workspace detected",)))
        elif is_vue_cli_project(package_root, parse_package_json=parse_package_json):
            candidates.append(FrameworkCandidate("vue_cli", package_root, "high", ("vue cli dependency/config detected",)))
        elif is_create_react_app_project(package_root, parse_package_json=parse_package_json):
            candidates.append(FrameworkCandidate("cra", package_root, "high", ("react-scripts dependency detected",)))
        elif package_root == repo_dir.resolve() and is_express_static_project(
            repo_dir,
            parse_package_json=parse_package_json,
            detect_node_entry_script_paths=detect_node_entry_script_paths,
            detect_frontend_runtime_roots_fn=detect_frontend_runtime_roots_fn,
        ):
            candidates.append(FrameworkCandidate("express_static", package_root, "medium", ("node entry plus runtime roots detected",)))
        elif package_root == repo_dir.resolve() and is_static_html_project(repo_dir, collect_matching_files=collect_matching_files):
            candidates.append(FrameworkCandidate("static_html", package_root, "medium", ("html files detected",)))
        else:
            candidates.append(FrameworkCandidate("generic", package_root, "low", ("fallback generic project",)))
    return candidates


def build_frontend_project_strategy(
    repo_dir: Path,
    candidate: FrameworkCandidate,
    confirmed_runtime_roots: tuple[Path, ...],
    hinted_runtime_roots: tuple[Path, ...],
) -> FrontendProjectStrategy:
    framework = candidate.framework
    if framework == "nextjs":
        proxy_mode = "preserve_prefix"
        adapter = "nextjs"
        source_adapter = "nextjs"
    elif framework == "vite":
        proxy_mode = "strip_prefix" if confirmed_runtime_roots else "preserve_prefix"
        adapter = "vite"
        source_adapter = "static_rewrite"
    elif framework == "vue_cli":
        proxy_mode = "preserve_prefix"
        adapter = "vue_cli"
        source_adapter = "static_rewrite"
    elif framework == "cra":
        proxy_mode = "preserve_prefix"
        adapter = "cra"
        source_adapter = "static_rewrite"
    else:
        proxy_mode = "strip_prefix"
        adapter = "static_rewrite"
        source_adapter = "static_rewrite"

    runtime_roots = confirmed_runtime_roots or hinted_runtime_roots
    source_roots = tuple(root for root in runtime_roots if root.exists())
    config_files: List[Path] = []
    for filename in ("next.config.ts", "next.config.mjs", "next.config.js", "vite.config.ts", "vite.config.js", "vite.config.mjs", "vue.config.js", "package.json"):
        candidate_path = candidate.package_dir / filename
        if candidate_path.is_file():
            config_files.append(candidate_path.resolve())

    project_id = slugify(candidate.package_dir.relative_to(repo_dir).as_posix()) if candidate.package_dir != repo_dir.resolve() else "root"
    capabilities: List[str] = []
    if framework in {"nextjs", "vite", "vue_cli", "cra"}:
        capabilities.append("code_scan")
        capabilities.append("client_code")
    if runtime_roots:
        capabilities.append("runtime_root")
    if runtime_roots or framework in {"static_html", "express_static", "generic"}:
        capabilities.append("html_entry")
    if config_files:
        capabilities.append("config_adapter")
    if framework in {"vite", "vue_cli", "cra", "static_html", "generic"}:
        capabilities.append("build_output_expected")
    return FrontendProjectStrategy(
        project_id=project_id,
        framework=framework,
        proxy_mode=proxy_mode,
        adapter=adapter,
        source_adapter=source_adapter,
        project_root=candidate.package_dir,
        source_roots=source_roots,
        runtime_roots=runtime_roots,
        config_files=tuple(config_files),
        capabilities=tuple(dict.fromkeys(capabilities)),
        evidence=tuple(candidate.reasons),
        runtime_entry_hint=None,
        runtime_entry_candidates=(),
    )


def build_frontend_project_strategies(
    repo_dir: Path,
    candidates: List[FrameworkCandidate],
    evidence: List[RuntimeRootEvidence],
) -> tuple[FrontendProjectStrategy, ...]:
    groups = classify_runtime_root_groups(evidence)
    confirmed = tuple([*groups["service_roots"], *groups["page_cluster_roots"]])
    hinted = groups["hinted_roots"]
    projects: List[FrontendProjectStrategy] = []
    for candidate in candidates:
        projects.append(build_frontend_project_strategy(repo_dir, candidate, confirmed, hinted))
    return tuple(projects)


def _default_adapter_for_framework(framework: str) -> str:
    if framework in {"nextjs", "vite", "vue_cli", "cra"}:
        return framework
    return "static_rewrite"


def _default_source_adapter_for_framework(framework: str, source_adapter: Optional[str] = None) -> str:
    if isinstance(source_adapter, str) and source_adapter.strip():
        return source_adapter.strip()
    if framework == "nextjs":
        return "nextjs"
    return "static_rewrite"


def _capabilities_for_declared_project(
    framework: str,
    runtime_roots: tuple[Path, ...],
    source_roots: tuple[Path, ...],
    config_files: tuple[Path, ...],
) -> tuple[str, ...]:
    capabilities: List[str] = []
    if framework in {"nextjs", "vite", "vue_cli", "cra"}:
        capabilities.extend(["code_scan", "client_code"])
    if runtime_roots:
        capabilities.append("runtime_root")
        capabilities.append("html_entry")
    if source_roots and framework in {"nextjs", "vite", "vue_cli", "cra"}:
        capabilities.append("client_code")
    if config_files:
        capabilities.append("config_adapter")
    if framework in {"vite", "vue_cli", "cra", "static_html", "generic"}:
        capabilities.append("build_output_expected")
    return tuple(dict.fromkeys(capabilities))


def _build_declared_subpath_plan(
    repo_dir: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> Optional[SubpathPlan]:
    declaration_path = repo_dir / ".ka" / "subpath.json"
    raw = read_text_if_exists(declaration_path)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    projects_payload = payload.get("projects")
    if not isinstance(projects_payload, list):
        return None
    projects: List[FrontendProjectStrategy] = []
    for index, item in enumerate(projects_payload):
        if not isinstance(item, dict):
            continue
        root_text = str(item.get("root", "")).strip()
        framework = str(item.get("framework", "")).strip()
        proxy_mode = str(item.get("proxy_mode", "")).strip()
        if not root_text or not framework or not proxy_mode:
            continue
        project_root = (repo_dir / root_text).resolve() if root_text not in {".", ""} else repo_dir.resolve()
        if not project_root.exists() or not project_root.is_dir():
            continue
        adapter = _default_adapter_for_framework(framework) if not str(item.get("adapter", "")).strip() else str(item.get("adapter", "")).strip()
        source_adapter = _default_source_adapter_for_framework(framework, str(item.get("source_adapter", "")).strip() or None)
        source_roots: List[Path] = []
        for rel in item.get("source_roots", []) if isinstance(item.get("source_roots"), list) else []:
            candidate = (repo_dir / str(rel)).resolve()
            if candidate.exists():
                source_roots.append(candidate)
        runtime_roots: List[Path] = []
        for rel in item.get("runtime_roots", []) if isinstance(item.get("runtime_roots"), list) else []:
            candidate = (repo_dir / str(rel)).resolve()
            if candidate.exists():
                runtime_roots.append(candidate)
        config_files: List[Path] = []
        config_file_value = item.get("config_file")
        if isinstance(config_file_value, str) and config_file_value.strip():
            candidate = (repo_dir / config_file_value.strip()).resolve()
            if candidate.exists():
                config_files.append(candidate)
        runtime_entry_hint = item.get("runtime_entry_hint")
        if not isinstance(runtime_entry_hint, str) or not runtime_entry_hint.strip():
            runtime_entry_hint = None
        runtime_entry_candidates: List[str] = []
        for value in item.get("runtime_entry_candidates", []) if isinstance(item.get("runtime_entry_candidates"), list) else []:
            if isinstance(value, str) and value.strip():
                runtime_entry_candidates.append(value.strip())
        if runtime_entry_hint and runtime_entry_hint not in runtime_entry_candidates:
            runtime_entry_candidates.insert(0, runtime_entry_hint)
        project_id = slugify(project_root.relative_to(repo_dir).as_posix()) if project_root != repo_dir.resolve() else "root"
        projects.append(
            FrontendProjectStrategy(
                project_id=project_id or f"declared-{index}",
                framework=framework,
                proxy_mode=proxy_mode,
                adapter=adapter,
                source_adapter=source_adapter,
                project_root=project_root,
                source_roots=tuple(source_roots),
                runtime_roots=tuple(runtime_roots),
                config_files=tuple(config_files),
                capabilities=_capabilities_for_declared_project(framework, tuple(runtime_roots), tuple(source_roots), tuple(config_files)),
                evidence=("declaration",),
                runtime_entry_hint=runtime_entry_hint,
                runtime_entry_candidates=tuple(runtime_entry_candidates),
            )
        )
    if not projects:
        return None
    default_project = payload.get("default_project")
    if not isinstance(default_project, str) or default_project not in {project.project_id for project in projects}:
        default_project = projects[0].project_id
    return SubpathPlan(
        projects=tuple(projects),
        default_project=default_project,
        notes=("strategy derived from declaration",),
    )


def _declared_runtime_roots(
    repo_dir: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> tuple[Path, ...]:
    declared_plan = _build_declared_subpath_plan(
        repo_dir,
        read_text_if_exists=read_text_if_exists,
    )
    if declared_plan is None:
        return ()
    runtime_roots: List[Path] = []
    seen: Set[Path] = set()
    for project in declared_plan.projects:
        for runtime_root in project.runtime_roots:
            if runtime_root not in seen:
                runtime_roots.append(runtime_root)
                seen.add(runtime_root)
    return tuple(runtime_roots)


def build_subpath_plan(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
    collect_python_frontend_hint_files_fn: Callable[[Path], List[Path]],
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
    vite_project_roots_fn: Callable[[Path], List[Path]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    discover_workspace_packages: Optional[Callable[[Path, Dict[str, object]], List[Dict[str, object]]]] = None,
) -> SubpathPlan:
    declared_plan = _build_declared_subpath_plan(
        repo_dir,
        read_text_if_exists=read_text_if_exists,
    )
    if declared_plan is not None:
        return declared_plan
    workspace_packages_adapter = discover_workspace_packages or (lambda repo, root_pkg: [])
    collect_python_frontend_hint_files_adapter = lambda path: collect_python_frontend_hint_files(
        path,
        collect_matching_files=collect_matching_files,
    )
    workspace_frontend_package_dirs_adapter = lambda path: workspace_frontend_package_dirs(
        path,
        parse_package_json=parse_package_json,
        discover_workspace_packages=workspace_packages_adapter,
    )
    detect_frontend_root_hints_from_python_file_adapter = lambda repo, source: detect_frontend_root_hints_from_python_file(
        repo,
        source,
        read_text_if_exists=read_text_if_exists,
    )
    detect_static_root_hints_from_node_entry_adapter = lambda repo, entry: detect_static_root_hints_from_node_entry(
        repo,
        entry,
        read_text_if_exists=read_text_if_exists,
    )
    detect_frontend_runtime_roots_adapter = lambda path: detect_frontend_runtime_roots(
        path,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        detect_static_root_hints_from_node_entry_fn=detect_static_root_hints_from_node_entry_adapter,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files_adapter,
        detect_frontend_root_hints_from_python_file_fn=detect_frontend_root_hints_from_python_file_adapter,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs_adapter,
        read_text_if_exists=read_text_if_exists,
        collect_matching_files=collect_matching_files,
    )
    evidence = detect_runtime_root_evidence(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files_adapter,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs_adapter,
        collect_matching_files=collect_matching_files,
    )
    candidates = detect_framework_candidates(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        detect_frontend_runtime_roots_fn=detect_frontend_runtime_roots_adapter,
        vite_project_roots_fn=vite_project_roots_fn,
        collect_matching_files=collect_matching_files,
        discover_workspace_packages=discover_workspace_packages,
    )
    projects = build_frontend_project_strategies(repo_dir, candidates, evidence)
    default_project = projects[0].project_id if projects else None
    return SubpathPlan(
        projects=projects,
        default_project=default_project,
        notes=("strategy derived from plan",),
    )


def detect_frontend_runtime_roots(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    detect_static_root_hints_from_node_entry_fn: Callable[[Path, Path], List[Path]],
    collect_python_frontend_hint_files_fn: Callable[[Path], List[Path]],
    detect_frontend_root_hints_from_python_file_fn: Callable[[Path, Path], List[Path]],
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> List[Path]:
    declared_runtime_roots = _declared_runtime_roots(
        repo_dir,
        read_text_if_exists=read_text_if_exists,
    )
    if declared_runtime_roots:
        return list(declared_runtime_roots)
    evidence = detect_runtime_root_evidence(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files_fn,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs_fn,
        collect_matching_files=collect_matching_files,
    )
    groups = classify_runtime_root_groups(evidence)
    return [*groups["service_roots"], *groups["page_cluster_roots"], *groups["hinted_roots"]]


def detect_frontend_runtime_root_groups(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    detect_static_root_hints_from_node_entry_fn: Callable[[Path, Path], List[Path]],
    collect_python_frontend_hint_files_fn: Callable[[Path], List[Path]],
    detect_frontend_root_hints_from_python_file_fn: Callable[[Path, Path], List[Path]],
    workspace_frontend_package_dirs_fn: Callable[[Path], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> Dict[str, tuple[Path, ...]]:
    declared_runtime_roots = _declared_runtime_roots(
        repo_dir,
        read_text_if_exists=read_text_if_exists,
    )
    if declared_runtime_roots:
        return {
            "service_roots": tuple(declared_runtime_roots),
            "page_cluster_roots": (),
            "hinted_roots": (),
        }
    evidence = detect_runtime_root_evidence(
        repo_dir,
        parse_package_json=parse_package_json,
        detect_node_entry_script_paths=detect_node_entry_script_paths,
        read_text_if_exists=read_text_if_exists,
        collect_python_frontend_hint_files_fn=collect_python_frontend_hint_files_fn,
        workspace_frontend_package_dirs_fn=workspace_frontend_package_dirs_fn,
        collect_matching_files=collect_matching_files,
    )
    return classify_runtime_root_groups(evidence)


def is_nextjs_project(repo_dir: Path, *, parse_package_json: Callable[[Path], Dict[str, object]]) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    if "next" in dependencies or "next" in dev_dependencies:
        return True
    return any((repo_dir / candidate).exists() for candidate in ("next.config.js", "next.config.mjs", "next.config.ts"))


def is_vite_project(repo_dir: Path, *, vite_project_roots_fn: Callable[[Path], List[Path]]) -> bool:
    return bool(vite_project_roots_fn(repo_dir))


def is_create_react_app_project(repo_dir: Path, *, parse_package_json: Callable[[Path], Dict[str, object]]) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    return "react-scripts" in dependencies or "react-scripts" in dev_dependencies


def is_vue_cli_project(repo_dir: Path, *, parse_package_json: Callable[[Path], Dict[str, object]]) -> bool:
    package = parse_package_json(repo_dir)
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    if "@vue/cli-service" in dependencies or "@vue/cli-service" in dev_dependencies:
        return True
    return (repo_dir / "vue.config.js").exists()


def is_express_static_project(
    repo_dir: Path,
    *,
    parse_package_json: Callable[[Path], Dict[str, object]],
    detect_node_entry_script_paths: Callable[[Path, Dict[str, object]], List[Path]],
    detect_frontend_runtime_roots_fn: Callable[[Path], List[Path]],
) -> bool:
    package = parse_package_json(repo_dir)
    package_scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    return bool(detect_node_entry_script_paths(repo_dir, package_scripts) and detect_frontend_runtime_roots_fn(repo_dir))


def is_static_html_project(repo_dir: Path, *, collect_matching_files: Callable[[Path, List[str]], List[Path]]) -> bool:
    return bool(collect_matching_files(repo_dir, ["*.html", "src/**/*.html", "public/**/*.html", "static/**/*.html", "web/**/*.html"]))

