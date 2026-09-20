import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from .common import build_deployment_base_path, is_allowed_root_relative_url
from .models import (
    SUBPATH_ALLOWED_ROOT_COMMENT,
    SUBPATH_API_BASE_CONCAT_PATTERNS,
    SUBPATH_CLIENT_METHOD_PATTERNS,
    HtmlUrlExtractor,
    SubpathAuditFinding,
)
from .rewrite import (
    _rewrite_dynamic_html_attr_fragments_in_js_text,
    _rewrite_client_request_text,
    _rewrite_root_relative_html_attrs_outside_scripts,
    inject_tool_base_runtime,
    rewrite_html_relative_asset_urls,
)


PYTHON_EMBEDDED_HTML_SOURCE = "python_embedded_html"


@dataclass(frozen=True)
class PythonEmbeddedHtmlTarget:
    file_path: Path
    line: int
    value: str
    symbol: Optional[str]
    source_kind: str


def _looks_like_embedded_html(value: str) -> bool:
    lowered = value.lower()
    if "<script" not in lowered and "</html" not in lowered and "</body" not in lowered:
        return False
    return "<!doctype html" in lowered or "<html" in lowered


def _decorator_route_paths(node: ast.AST) -> List[str]:
    paths: List[str] = []
    decorators = getattr(node, "decorator_list", [])
    for decorator in decorators:
        call = decorator if isinstance(decorator, ast.Call) else None
        if call is None:
            continue
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr.lower() not in {"get", "post", "put", "delete", "patch", "route"}:
            continue
        if not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            paths.append(first.value)
    return paths


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _response_call_arg_names(call: ast.Call) -> Set[str]:
    name = _call_name(call.func)
    if name not in {"HTMLResponse", "Response", "render_template_string"}:
        return set()
    names: Set[str] = set()
    for arg in call.args:
        if isinstance(arg, ast.Name):
            names.add(arg.id)
    for keyword in call.keywords:
        if keyword.arg in {"content", "html"} and isinstance(keyword.value, ast.Name):
            names.add(keyword.value.id)
    return names


def _route_returns_name(func: ast.AST, candidate_name: str) -> bool:
    route_paths = _decorator_route_paths(func)
    if not route_paths:
        return False
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id == candidate_name:
            return True
        if isinstance(value, ast.Call) and candidate_name in _response_call_arg_names(value):
            return True
    return False


def _direct_response_string_nodes(func: ast.AST) -> List[ast.Constant]:
    if not _decorator_route_paths(func):
        return []
    nodes: List[ast.Constant] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
            continue
        name = _call_name(node.value.func)
        if name not in {"HTMLResponse", "Response", "render_template_string"}:
            continue
        for arg in node.value.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _looks_like_embedded_html(arg.value):
                nodes.append(arg)
        for keyword in node.value.keywords:
            if keyword.arg in {"content", "html"} and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str) and _looks_like_embedded_html(keyword.value.value):
                nodes.append(keyword.value)
    return nodes


def _assignment_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _assignment_value_node(node: ast.AST) -> Optional[ast.Constant]:
    value = getattr(node, "value", None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value
    return None


def find_python_embedded_html_targets_in_file(
    file_path: Path,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[PythonEmbeddedHtmlTarget]:
    text = read_text_if_exists(file_path)
    if not text or SUBPATH_ALLOWED_ROOT_COMMENT in text:
        return []
    if "<html" not in text.lower() and "<!doctype html" not in text.lower():
        return []
    if not any(token in text for token in ("HTMLResponse", "Response", "render_template_string")):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    html_assignments: Dict[str, ast.Constant] = {}
    for node in ast.walk(tree):
        name = _assignment_name(node)
        value_node = _assignment_value_node(node)
        if name and value_node is not None and _looks_like_embedded_html(value_node.value):
            html_assignments[name] = value_node

    returned_names: Set[str] = set()
    direct_nodes: List[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for name in html_assignments:
            if _route_returns_name(node, name):
                returned_names.add(name)
        direct_nodes.extend(_direct_response_string_nodes(node))

    targets: List[PythonEmbeddedHtmlTarget] = []
    for name in sorted(returned_names):
        value_node = html_assignments[name]
        targets.append(
            PythonEmbeddedHtmlTarget(
                file_path=file_path,
                line=getattr(value_node, "lineno", 1),
                value=value_node.value,
                symbol=name,
                source_kind=PYTHON_EMBEDDED_HTML_SOURCE,
            )
        )
    for value_node in direct_nodes:
        targets.append(
            PythonEmbeddedHtmlTarget(
                file_path=file_path,
                line=getattr(value_node, "lineno", 1),
                value=value_node.value,
                symbol=None,
                source_kind=PYTHON_EMBEDDED_HTML_SOURCE,
            )
        )
    return targets


def collect_python_embedded_html_candidate_files(
    repo_dir: Path,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
) -> List[Path]:
    patterns = [
        "*.py",
        "server.py",
        "app.py",
        "main.py",
        "api.py",
        "asgi.py",
        "wsgi.py",
        "app/**/*.py",
        "backend/**/*.py",
        "server/**/*.py",
    ]
    candidates: List[Path] = []
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        if any(part in relative.split("/") for part in ("tests", "vendor", ".venv", "node_modules", "__pycache__", "docs", "codex-home")):
            continue
        candidates.append(file_path)
    return sorted(set(candidates))


def find_python_embedded_html_targets(
    repo_dir: Path,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[PythonEmbeddedHtmlTarget]:
    targets: List[PythonEmbeddedHtmlTarget] = []
    for file_path in collect_python_embedded_html_candidate_files(repo_dir, collect_matching_files=collect_matching_files):
        targets.extend(
            find_python_embedded_html_targets_in_file(
                file_path,
                read_text_if_exists=read_text_if_exists,
            )
        )
    return targets


def find_python_embedded_html_files(
    repo_dir: Path,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[Path]:
    return sorted({target.file_path for target in find_python_embedded_html_targets(repo_dir, collect_matching_files=collect_matching_files, read_text_if_exists=read_text_if_exists)})


def _scan_html_value(
    html: str,
    *,
    file_name: str,
    base_path: str,
    base_line: int,
) -> List[SubpathAuditFinding]:
    findings: List[SubpathAuditFinding] = []
    parser = HtmlUrlExtractor()
    parser.feed(html)
    for ref in parser.references:
        if is_allowed_root_relative_url(ref.url, base_path):
            continue
        findings.append(
            SubpathAuditFinding(
                file=file_name,
                line=base_line + max(ref.line - 1, 0),
                severity="error",
                code="python_embedded_root_relative_html_url",
                message=f'Python embedded HTML emits root-relative `{ref.attr}="{ref.url}"` outside deployment subpath `{base_path}`',
                detail_kind={
                    "href": "html_link",
                    "src": "html_script" if ref.url.endswith((".js", ".mjs", ".ts", ".tsx", ".jsx")) or "/src/" in ref.url else "html_asset",
                    "action": "html_form_action",
                }.get(ref.attr, "html_attribute"),
            )
        )

    for pattern, detail_kind in zip(
        SUBPATH_CLIENT_METHOD_PATTERNS,
        (
            "request_api",
            "request_api",
            "eventsource",
            "request_api",
            "request_api",
            "navigation",
            "navigation",
            "navigation",
        ),
    ):
        for match in pattern.finditer(html):
            url = f"/{match.group('path')}"
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = base_line + html.count("\n", 0, match.start())
            findings.append(
                SubpathAuditFinding(
                    file=file_name,
                    line=line,
                    severity="error",
                    code="python_embedded_root_relative_client_url",
                    message=f'Python embedded HTML emits root-relative browser URL `{url}` outside deployment subpath `{base_path}`',
                    detail_kind=detail_kind,
                )
            )

    for pattern, detail_kind in zip(
        SUBPATH_API_BASE_CONCAT_PATTERNS,
        ("request_api", "request_api", "eventsource", "request_api", "request_api"),
    ):
        for match in pattern.finditer(html):
            url = f"/{match.group('path')}"
            if is_allowed_root_relative_url(url, base_path):
                continue
            line = base_line + html.count("\n", 0, match.start())
            findings.append(
                SubpathAuditFinding(
                    file=file_name,
                    line=line,
                    severity="error",
                    code="python_embedded_root_relative_client_url",
                    message=f'Python embedded HTML emits root-relative browser URL `{url}` outside deployment subpath `{base_path}`',
                    detail_kind=detail_kind,
                )
            )
    return findings


def scan_python_embedded_html_findings(
    repo_dir: Path,
    project_slug: str,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> List[SubpathAuditFinding]:
    base_path = build_deployment_base_path(project_slug)
    findings: List[SubpathAuditFinding] = []
    for target in find_python_embedded_html_targets(
        repo_dir,
        collect_matching_files=collect_matching_files,
        read_text_if_exists=read_text_if_exists,
    ):
        findings.extend(
            _scan_html_value(
                target.value,
                file_name=target.file_path.relative_to(repo_dir).as_posix(),
                base_path=base_path,
                base_line=target.line,
            )
        )
    return findings


def _literal_replacement(value: str) -> str:
    if '"""' not in value and not value.endswith("\\"):
        return f'r"""{value}"""'
    return repr(value)


def _replace_ast_node_text(source: str, node: ast.AST, replacement: str) -> str:
    lines = source.splitlines(keepends=True)
    start_line = getattr(node, "lineno", None)
    end_line = getattr(node, "end_lineno", None)
    start_col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if not all(isinstance(value, int) for value in (start_line, end_line, start_col, end_col)):
        return source
    start_line -= 1
    end_line -= 1
    lines[start_line] = lines[start_line][:start_col] + replacement + lines[end_line][end_col:]
    del lines[start_line + 1 : end_line + 1]
    return "".join(lines)


def _rewrite_html_value(html: str, base_path: str, repo_dir: Path, file_path: Path) -> str:
    rewritten = _rewrite_client_request_text(html)
    rewritten = _rewrite_dynamic_html_attr_fragments_in_js_text(rewritten)
    rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots=[repo_dir.resolve()])
    def replace_attr(_attr: str, url: str) -> Optional[str]:
        if is_allowed_root_relative_url(url, base_path):
            return None
        return f"{base_path}{url}"

    rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
        rewritten,
        attrs=("src", "href", "action"),
        replace_url=replace_attr,
    )
    if rewritten != html:
        rewritten = inject_tool_base_runtime(rewritten, base_path)
    return rewritten


def rewrite_python_embedded_html_subpath_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    read_text_if_exists: Callable[[Path], Optional[str]],
) -> bool:
    original = read_text(file_path)
    if SUBPATH_ALLOWED_ROOT_COMMENT in original:
        return False
    try:
        tree = ast.parse(original)
    except SyntaxError:
        return False
    targets = find_python_embedded_html_targets_in_file(
        file_path,
        read_text_if_exists=read_text_if_exists,
    )
    if not targets:
        return False
    target_keys = {(target.line, target.symbol, target.value) for target in targets}
    nodes: List[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for line, _symbol, value in target_keys:
            if getattr(node, "lineno", None) == line and node.value == value:
                nodes.append(node)
                break
    rewritten_source = original
    changed = False
    for node in sorted(nodes, key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0)), reverse=True):
        old_value = node.value
        new_value = _rewrite_html_value(old_value, base_path, repo_dir, file_path)
        if new_value == old_value:
            continue
        rewritten_source = _replace_ast_node_text(rewritten_source, node, _literal_replacement(new_value))
        changed = True
    if changed and rewritten_source != original:
        write_text(file_path, rewritten_source)
        return True
    return False
