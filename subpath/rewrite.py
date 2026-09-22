import re
from pathlib import Path
from typing import Callable, List, Optional

from .common import looks_like_templated_value, split_url_suffix
from .models import FrontendProjectStrategy, SubpathPlan


HTML_RELATIVE_ASSET_EXTENSIONS = {
    ".css",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".map",
    ".mp3",
    ".wav",
    ".mp4",
    ".webm",
}


HTML_SCRIPT_STYLE_BLOCK_PATTERN = re.compile(r"(<(?:script|style)\b[^>]*>)(.*?)(</(?:script|style)>)", re.IGNORECASE | re.DOTALL)


def _sorted_unique_paths(items: List[Path]) -> List[Path]:
    return sorted(set(items))


def _rewrite_html_outside_script_style_blocks(text: str, rewrite_segment: Callable[[str], str]) -> str:
    rewritten_parts: List[str] = []
    previous_end = 0
    for match in HTML_SCRIPT_STYLE_BLOCK_PATTERN.finditer(text):
        rewritten_parts.append(rewrite_segment(text[previous_end : match.start()]))
        rewritten_parts.append(rewrite_segment(match.group(1)))
        rewritten_parts.append(match.group(2))
        rewritten_parts.append(match.group(3))
        previous_end = match.end()
    rewritten_parts.append(rewrite_segment(text[previous_end:]))
    return "".join(rewritten_parts)


def _rewrite_root_relative_html_attrs_outside_scripts(
    text: str,
    *,
    attrs: tuple[str, ...],
    replace_url: Callable[[str, str], Optional[str]],
) -> str:
    attr_names = "|".join(re.escape(attr) for attr in attrs)
    attr_pattern = re.compile(rf'(?P<attr>{attr_names})=(?P<quote>["\'])(?P<url>/(?!/)[^"\']*)(?P=quote)')

    def rewrite_segment(segment: str) -> str:
        def replace_attr(match: re.Match[str]) -> str:
            attr = match.group("attr")
            url = match.group("url")
            replacement = replace_url(attr, url)
            if replacement is None:
                return match.group(0)
            quote = match.group("quote")
            return f"{attr}={quote}{replacement}{quote}"

        return attr_pattern.sub(replace_attr, segment)

    return _rewrite_html_outside_script_style_blocks(text, rewrite_segment)


def _paths_under_project(paths: List[Path], project: FrontendProjectStrategy) -> List[Path]:
    scoped: List[Path] = []
    for path in paths:
        try:
            path.resolve().relative_to(project.project_root.resolve())
        except ValueError:
            continue
        scoped.append(path)
    return scoped


def inject_tool_base_runtime(text: str, base_path: str) -> str:
    marker = f'window.__TOOL_BASE_PATH__ = "{base_path}";'
    if marker in text:
        return text
    script = (
        "<script>\n"
        f'window.__TOOL_BASE_PATH__ = "{base_path}";\n'
        'window.__TOOL_ORIGIN_URL__ = window.__TOOL_BASE_PATH__ ? (window.location.origin + window.__TOOL_BASE_PATH__) : window.location.origin;\n'
        "window.withToolBase = function(path) {\n"
        "  if (!path) return path;\n"
        "  var base = window.__TOOL_BASE_PATH__ || \"\";\n"
        "  if (!base) return path;\n"
        "  if (/^(https?:)?\\/\\//.test(path)) return path;\n"
        "  if (path.startsWith(base + \"/\") || path === base) return path;\n"
        "  if (path.startsWith(\"/\")) return base + path;\n"
        "  return base + \"/\" + path.replace(/^\\/+/, \"\");\n"
        "};\n"
        "</script>\n"
    )
    if "</head>" in text:
        return text.replace("<script>", f"{script}<script>", 1) if "<script>" in text else text.replace("</head>", f"{script}</head>", 1)
    return script + text


def frontend_runtime_root(repo_dir: Path, file_path: Path, runtime_roots: Optional[List[Path]] = None) -> Path:
    if runtime_roots:
        file_resolved = file_path.resolve()
        for root in runtime_roots:
            try:
                file_resolved.relative_to(root)
            except ValueError:
                continue
            return root

    relative = file_path.relative_to(repo_dir)
    parts = relative.parts
    if not parts:
        return repo_dir
    for anchor in ("src", "static", "public", "client", "web"):
        if anchor in parts:
            return repo_dir / anchor
    return repo_dir


def should_rewrite_relative_html_asset(raw_url: str) -> bool:
    asset_path, _suffix = split_url_suffix(raw_url.strip())
    if not asset_path:
        return False
    if asset_path.startswith(("/", "#")):
        return False
    if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?//", asset_path):
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", asset_path):
        return False
    if looks_like_templated_value(asset_path):
        return False
    suffix = Path(asset_path).suffix.lower()
    return suffix in HTML_RELATIVE_ASSET_EXTENSIONS


def should_prefix_root_relative_html_url(
    attr: str,
    raw_url: str,
    file_path: Path,
    repo_dir: Path,
    *,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    asset_path, _suffix = split_url_suffix(raw_url.strip())
    if not asset_path.startswith("/"):
        return False
    if asset_path.startswith("//") or looks_like_templated_value(asset_path):
        return False
    lowered = asset_path.lower()
    if lowered.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return False
    if attr == "src" and asset_path.startswith("/src/") and file_path.name == "index.html":
        for project_root in vite_project_roots(repo_dir):
            try:
                file_path.resolve().relative_to(project_root)
            except ValueError:
                continue
            return False
    return True


def resolve_repo_relative_asset_url(
    repo_dir: Path,
    file_path: Path,
    raw_url: str,
    runtime_roots: Optional[List[Path]] = None,
) -> Optional[str]:
    if not should_rewrite_relative_html_asset(raw_url):
        return None
    asset_path, suffix = split_url_suffix(raw_url.strip())
    content_root = frontend_runtime_root(repo_dir, file_path, runtime_roots)
    candidate = (file_path.parent / asset_path).resolve()
    try:
        candidate.relative_to(content_root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    relative_to_root = candidate.relative_to(content_root.resolve()).as_posix()
    return f"/{relative_to_root}{suffix}"


def rewrite_html_relative_asset_urls(
    text: str,
    repo_dir: Path,
    file_path: Path,
    runtime_roots: Optional[List[Path]] = None,
) -> str:
    attr_pattern = re.compile(r'(?P<attr>\b(?:src|href))=(?P<quote>["\'])(?P<url>[^"\']+)(?P=quote)')

    def rewrite_segment(segment: str) -> str:
        def replace_attr(match: re.Match[str]) -> str:
            original_url = match.group("url")
            rewritten_url = resolve_repo_relative_asset_url(repo_dir, file_path, original_url, runtime_roots)
            if rewritten_url is None:
                return match.group(0)
            quote = match.group("quote")
            return f'{match.group("attr")}={quote}{rewritten_url}{quote}'

        return attr_pattern.sub(replace_attr, segment)

    return _rewrite_html_outside_script_style_blocks(text, rewrite_segment)


def rewrite_origin_based_subpath_logic(text: str) -> str:
    rewritten = text
    rewritten = re.sub(
        r'((?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*ORIGIN[A-Za-z0-9_$]*\s*=\s*)(?!window\.__TOOL_ORIGIN_URL__ \|\| )([^;]+);',
        r'\1(window.__TOOL_ORIGIN_URL__ || (\2));',
        rewritten,
    )
    rewritten = rewritten.replace("return origin;", "return window.__TOOL_ORIGIN_URL__ || origin;")
    rewritten = rewritten.replace("${origin}", "${window.__TOOL_ORIGIN_URL__ || origin}")
    rewritten = rewritten.replace(
        "${window.location.origin}",
        "${window.__TOOL_ORIGIN_URL__ || window.location.origin}",
    )
    rewritten = rewritten.replace(
        'window.location.href = SETUP_PAGE_URL;',
        'window.location.href = (Reflect.get(window, "withToolBase")?.(SETUP_PAGE_URL) ?? SETUP_PAGE_URL);',
    )
    rewritten = rewritten.replace(
        "window.location.href = $(e.target).attr('data-href');",
        "window.location.href = (Reflect.get(window, \"withToolBase\")?.($(e.target).attr('data-href')) ?? $(e.target).attr('data-href'));",
    )
    rewritten = rewritten.replace(
        "window.location.href = href;",
        "window.location.href = (Reflect.get(window, \"withToolBase\")?.(href) ?? href);",
    )
    rewritten = rewritten.replace(
        'window.location.href = target;',
        'window.location.href = (Reflect.get(window, "withToolBase")?.(target) ?? target);',
    )
    rewritten = rewritten.replace(
        'window.location.href = url;',
        'window.location.href = (Reflect.get(window, "withToolBase")?.(url) ?? url);',
    )
    return rewritten


def wrap_runtime_tool_base_expr(path_expr: str) -> str:
    return f'(Reflect.get(window, "withToolBase")?.({path_expr}) ?? {path_expr})'


_JSX_ROOT_RELATIVE_PATH_ATTRIBUTE_PATTERN = re.compile(
    r'(?P<prefix><[A-Za-z][A-Za-z0-9:.-]*\b[^>]*?\bpath\s*=\s*)'
    r'(?P<quote>["\'])(?P<url>/(?!/)[^"\']*)(?P=quote)'
)


def rewrite_jsx_root_relative_path_attributes(text: str) -> str:
    """Rewrite JSX path attributes without producing invalid JSX syntax.

    The generic JavaScript assignment rewrite also matches ``path="/"`` in
    JSX. JSX requires an expression attribute to be wrapped in braces, so
    handle this shape before the generic rules run.
    """

    def replace_attribute(match: re.Match[str]) -> str:
        quote = match.group("quote")
        url = f"{quote}{match.group('url')}{quote}"
        return f"{match.group('prefix')}{{{wrap_runtime_tool_base_expr(url)}}}"

    return _JSX_ROOT_RELATIVE_PATH_ATTRIBUTE_PATTERN.sub(replace_attribute, text)


_INLINE_HTML_API_FETCH_PATH_PATTERN = re.compile(
    r"(?P<header>"
    r"(?:async\s+)?function\s+api\s*\(\s*"
    r"(?P<path>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*,\s*"
    r"(?P<options>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*\)\s*\{\s*"
    r"const\s+res\s*=\s*await\s+fetch\s*\(\s*)"
    r"(?P=path)"
    r"(?P<rest>\s*,\s*(?P=options)\s*\)\s*;)"
)


def rewrite_inline_html_api_fetch_path(text: str) -> str:
    """Fix the narrow single-file HTML pattern where ``api(path)`` is a local
    wrapper around ``fetch(path, ...)``.

    The existing generic logic rewrites direct ``fetch('/api/...')`` calls, but
    cannot see root-relative URLs passed through a local request-wrapper
    function.  This intentionally only rewrites the wrapper body's fetch call;
    it does not scan every string in inline scripts and does not touch HTML
    attributes or external files.
    """

    def replace_fetch_path(match: re.Match[str]) -> str:
        return (
            match.group("header")
            + wrap_runtime_tool_base_expr(match.group("path"))
            + match.group("rest")
        )

    return _INLINE_HTML_API_FETCH_PATH_PATTERN.sub(replace_fetch_path, text)


def _safe_api_base_assignment_pattern(var_name: str) -> re.Pattern[str]:
    return re.compile(
        rf'((?:const|let|var)\s+{re.escape(var_name)}\s*=\s*)(?P<value>""|\'\'|window\.location\.origin|location\.origin)(\s*;)',
    )


def _uses_api_base_concat(text: str, var_name: str) -> bool:
    return bool(
        re.search(
            rf'\b(?:fetch|axios(?:\.(?:get|post|put|delete|patch))?|Request|EventSource)\b[\s\S]*?\b{re.escape(var_name)}\s*\+\s*(["\'`])/api/',
            text,
        )
        or re.search(rf'\b{re.escape(var_name)}\s*\+\s*(["\'`])/api/', text)
    )


def _rewrite_api_base_assignment_text(text: str) -> str:
    rewritten = text
    for var_name in ("API_BASE", "BASE_URL", "API_ROOT", "API_PREFIX"):
        if not _uses_api_base_concat(rewritten, var_name):
            continue
        pattern = _safe_api_base_assignment_pattern(var_name)

        def replace_assignment(match: re.Match[str]) -> str:
            value = match.group("value")
            if value in {'""', "''"}:
                replacement_value = 'window.__TOOL_BASE_PATH__ || ""'
            else:
                replacement_value = "window.__TOOL_ORIGIN_URL__ || window.location.origin"
            return f"{match.group(1)}{replacement_value}{match.group(3)}"

        rewritten = pattern.sub(replace_assignment, rewritten)
    return rewritten


def _rewrite_origin_api_concat_text(text: str) -> str:
    rewritten = text
    rewritten = re.sub(
        r'window\.location\.origin\s*\+\s*(["\'`])/api/(?P<path>[^"\'`]*)\1',
        lambda match: f'(window.__TOOL_ORIGIN_URL__ || window.location.origin) + "/api/{match.group("path")}"',
        rewritten,
    )
    rewritten = re.sub(
        r'(?<!\.)location\.origin\s*\+\s*(["\'`])/api/(?P<path>[^"\'`]*)\1',
        lambda match: f'(window.__TOOL_ORIGIN_URL__ || window.location.origin) + "/api/{match.group("path")}"',
        rewritten,
    )
    return rewritten


def rewrite_vite_fetch_template_calls(text: str) -> str:
    rewritten = text
    rewritten = re.sub(
        r'fetch\(\s*`/(?P<path>[^`$][^`]*)`\s*\)',
        lambda match: f"fetch({wrap_runtime_tool_base_expr('`/' + match.group('path') + '`')})",
        rewritten,
    )
    rewritten = re.sub(
        r'fetch\(\s*`/(?P<prefix>[^`]*\$\{[^`]+\}[^`]*)`\s*\)',
        lambda match: f"fetch({wrap_runtime_tool_base_expr('`/' + match.group('prefix') + '`')})",
        rewritten,
    )
    rewritten = re.sub(
        r'fetch\(\s*`/(?P<prefix>[^`]*\$\{[^`]+\}[^`]*)`\s*,',
        lambda match: f"fetch({wrap_runtime_tool_base_expr('`/' + match.group('prefix') + '`')},",
        rewritten,
    )
    rewritten = re.sub(
        r'new\s+EventSource\(\s*`/(?P<prefix>[^`]*\$\{[^`]+\}[^`]*)`\s*\)',
        lambda match: f"new EventSource({wrap_runtime_tool_base_expr('`/' + match.group('prefix') + '`')})",
        rewritten,
    )
    return rewritten


def _normalize_with_tool_base_nesting(text: str) -> str:
    rewritten = text
    rewritten = re.sub(r'window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)', r'window.withToolBase(\1)', rewritten)
    rewritten = re.sub(r'(?<!\.)withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)', r'withToolBase(\1)', rewritten)
    rewritten = re.sub(r'fetch\(\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)\s*\)', r'fetch(window.withToolBase(\1))', rewritten)
    rewritten = re.sub(r'url:\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)', r'url: window.withToolBase(\1)', rewritten)
    rewritten = rewritten.replace(
        "window.__TOOL_ORIGIN_URL__ || (window.__TOOL_ORIGIN_URL__ || window.location.origin)",
        "window.__TOOL_ORIGIN_URL__ || window.location.origin",
    )
    return rewritten


def _rewrite_request_api_text(text: str) -> str:
    rewritten = _normalize_with_tool_base_nesting(text)
    rewritten = _rewrite_api_base_assignment_text(rewritten)
    rewritten = _rewrite_origin_api_concat_text(rewritten)
    rewritten = re.sub(r'axios\.(get|post|put|delete|patch)\(\s*window\.withToolBase\(\s*window\.withToolBase\((.*?)\)\s*\)', r'axios.\1(window.withToolBase(\2)', rewritten)
    rewritten = re.sub(r'fetch\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)', lambda match: f"fetch({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    rewritten = re.sub(r'fetch\(\s*(["\'`]/[^"\'`]*["\'`])\s*,', lambda match: f"fetch({wrap_runtime_tool_base_expr(match.group(1))},", rewritten)
    rewritten = re.sub(r'fetch\(\s*(["\'`]/[^"\'`]*["\'`])\s*\+', lambda match: f"fetch({wrap_runtime_tool_base_expr(match.group(1))} +", rewritten)
    rewritten = re.sub(r'axios\.(get|post|put|delete|patch)\(\s*(["\'`]/[^"\'`]*["\'`])', lambda match: f"axios.{match.group(1)}({wrap_runtime_tool_base_expr(match.group(2))}", rewritten)
    rewritten = re.sub(r'new\s+Request\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)', lambda match: f"new Request({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    rewritten = re.sub(r'new\s+Request\(\s*(["\'`]/[^"\'`]*["\'`])\s*,', lambda match: f"new Request({wrap_runtime_tool_base_expr(match.group(1))},", rewritten)
    rewritten = re.sub(r'uploadWithProgress\(\s*(["\'`]/[^"\'`]*["\'`])', lambda match: f"uploadWithProgress({wrap_runtime_tool_base_expr(match.group(1))}", rewritten)
    rewritten = re.sub(r'(\b(?:endpoint|url|path|apiPath|requestPath)\s*=\s*)(["\'`]/[^"\'`]*["\'`])', lambda match: f"{match.group(1)}{wrap_runtime_tool_base_expr(match.group(2))}", rewritten)
    rewritten = re.sub(r'url:\s*(["\'`]/[^"\'`]*["\'`])', lambda match: f"url: {wrap_runtime_tool_base_expr(match.group(1))}", rewritten)
    return rewritten


def _rewrite_navigation_text(text: str) -> str:
    rewritten = _normalize_with_tool_base_nesting(text)
    rewritten = re.sub(r'window\.location\.href\s*=\s*(["\'`]/[^"\'`]*["\'`])', lambda match: f"window.location.href = {wrap_runtime_tool_base_expr(match.group(1))}", rewritten)
    rewritten = re.sub(r'window\.location\.assign\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)', lambda match: f"window.location.assign({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    rewritten = re.sub(r'window\.open\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)', lambda match: f"window.open({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    rewritten = rewrite_origin_based_subpath_logic(rewritten)
    return rewritten


def _rewrite_eventsource_text(text: str) -> str:
    rewritten = _normalize_with_tool_base_nesting(text)
    rewritten = re.sub(r'new\s+EventSource\(\s*(["\'`]/[^"\'`]*["\'`])\s*\)', lambda match: f"new EventSource({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    rewritten = re.sub(r'new\s+EventSource\(\s*(`\/[^`]*\$\{[^`]+\}[^`]*`)\s*\)', lambda match: f"new EventSource({wrap_runtime_tool_base_expr(match.group(1))})", rewritten)
    return rewritten


def _rewrite_return_value_text(text: str) -> str:
    rewritten = _normalize_with_tool_base_nesting(text)
    rewritten = re.sub(r'(?<!window\.withToolBase\()(["\'`])/static-preview\1', wrap_runtime_tool_base_expr('"/static-preview"'), rewritten)
    rewritten = re.sub(r'\breturn\s+(["\'`]/[^"\'`]*["\'`])', lambda match: f"return {wrap_runtime_tool_base_expr(match.group(1))}", rewritten)
    rewritten = re.sub(r'\breturn\s+(`\/[^`]*\$\{[^`]+\}[^`]*`)', lambda match: f"return {wrap_runtime_tool_base_expr(match.group(1))}", rewritten)
    return rewritten


def _rewrite_dynamic_html_attr_fragments_in_js_text(text: str) -> str:
    pattern = re.compile(
        r'(?P<literal_quote>["\'])(?P<prefix><[^"\'`<>]*?\b(?:href|src|action)=(?P<attr_quote>["\']))(?P<url>/(?!/)[^"\'`+]*?/)(?P=literal_quote)\s*\+'
    )

    def replace_fragment(match: re.Match[str]) -> str:
        literal_quote = match.group("literal_quote")
        url = match.group("url")
        if not url.startswith("/api/"):
            return match.group(0)
        expression = wrap_runtime_tool_base_expr(f"{literal_quote}{url}{literal_quote}")
        return f'{literal_quote}{match.group("prefix")}{literal_quote} + {expression} +'

    return pattern.sub(replace_fragment, text)


def _rewrite_client_request_text(text: str) -> str:
    rewritten = rewrite_jsx_root_relative_path_attributes(text)
    rewritten = _rewrite_request_api_text(rewritten)
    rewritten = _rewrite_navigation_text(rewritten)
    rewritten = _rewrite_eventsource_text(rewritten)
    rewritten = _rewrite_return_value_text(rewritten)
    rewritten = rewrite_vite_fetch_template_calls(rewritten)
    rewritten = rewrite_origin_based_subpath_logic(rewritten)
    return rewritten


def rewrite_frontend_client_request_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    if file_path.suffix not in {".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        return False
    original = read_text(file_path)
    rewritten = _rewrite_client_request_text(original)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_request_api_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    if file_path.suffix not in {".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        return False
    original = read_text(file_path)
    rewritten = _rewrite_request_api_text(original)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_navigation_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    if file_path.suffix not in {".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        return False
    original = read_text(file_path)
    rewritten = _rewrite_navigation_text(original)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_eventsource_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    if file_path.suffix not in {".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        return False
    original = read_text(file_path)
    rewritten = _rewrite_eventsource_text(original)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_return_value_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    if file_path.suffix not in {".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        return False
    original = read_text(file_path)
    rewritten = _rewrite_return_value_text(original)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_html_attribute_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    detect_frontend_runtime_root_groups: Optional[Callable[[Path], dict]] = None,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    if file_path.suffix != ".html":
        return False
    original = read_text(file_path)
    rewritten = original
    runtime_root_groups = detect_frontend_runtime_root_groups(repo_dir) if detect_frontend_runtime_root_groups is not None else {}
    runtime_roots = list(runtime_root_groups.get("service_roots", ())) if isinstance(runtime_root_groups, dict) else []
    if not runtime_roots:
        runtime_roots = detect_frontend_runtime_roots(repo_dir)
    rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
    def replace_root_relative_attr(attr: str, url: str) -> Optional[str]:
        if not should_prefix_root_relative_html_url(attr, url, file_path, repo_dir, vite_project_roots=vite_project_roots):
            return None
        return f"{base_path}{url}"

    rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
        rewritten,
        attrs=("src", "href", "action"),
        replace_url=replace_root_relative_attr,
    )
    rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_html_link_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    detect_frontend_runtime_root_groups: Optional[Callable[[Path], dict]] = None,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    if file_path.suffix != ".html":
        return False
    original = read_text(file_path)
    rewritten = original
    runtime_root_groups = detect_frontend_runtime_root_groups(repo_dir) if detect_frontend_runtime_root_groups is not None else {}
    runtime_roots = list(runtime_root_groups.get("service_roots", ())) if isinstance(runtime_root_groups, dict) else []
    if not runtime_roots:
        runtime_roots = detect_frontend_runtime_roots(repo_dir)
    rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
    def replace_link_attr(_attr: str, url: str) -> Optional[str]:
        if not should_prefix_root_relative_html_url("href", url, file_path, repo_dir, vite_project_roots=vite_project_roots):
            return None
        return f"{base_path}{url}"

    rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
        rewritten,
        attrs=("href",),
        replace_url=replace_link_attr,
    )
    rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_html_script_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    detect_frontend_runtime_root_groups: Optional[Callable[[Path], dict]] = None,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    if file_path.suffix != ".html":
        return False
    original = read_text(file_path)
    rewritten = original
    runtime_root_groups = detect_frontend_runtime_root_groups(repo_dir) if detect_frontend_runtime_root_groups is not None else {}
    runtime_roots = list(runtime_root_groups.get("service_roots", ())) if isinstance(runtime_root_groups, dict) else []
    if not runtime_roots:
        runtime_roots = detect_frontend_runtime_roots(repo_dir)
    rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
    def replace_script_attr(_attr: str, url: str) -> Optional[str]:
        if not should_prefix_root_relative_html_url("src", url, file_path, repo_dir, vite_project_roots=vite_project_roots):
            return None
        return f"{base_path}{url}"

    rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
        rewritten,
        attrs=("src",),
        replace_url=replace_script_attr,
    )
    rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_html_form_action_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    detect_frontend_runtime_root_groups: Optional[Callable[[Path], dict]] = None,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    if file_path.suffix != ".html":
        return False
    original = read_text(file_path)
    rewritten = original
    runtime_root_groups = detect_frontend_runtime_root_groups(repo_dir) if detect_frontend_runtime_root_groups is not None else {}
    runtime_roots = list(runtime_root_groups.get("service_roots", ())) if isinstance(runtime_root_groups, dict) else []
    if not runtime_roots:
        runtime_roots = detect_frontend_runtime_roots(repo_dir)
    rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
    def replace_form_action_attr(_attr: str, url: str) -> Optional[str]:
        if not should_prefix_root_relative_html_url("action", url, file_path, repo_dir, vite_project_roots=vite_project_roots):
            return None
        return f"{base_path}{url}"

    rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
        rewritten,
        attrs=("action",),
        replace_url=replace_form_action_attr,
    )
    rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def rewrite_frontend_subpath_urls(
    file_path: Path,
    base_path: str,
    repo_dir: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    detect_frontend_runtime_roots: Callable[[Path], List[Path]],
    detect_frontend_runtime_root_groups: Optional[Callable[[Path], dict]] = None,
    vite_project_roots: Callable[[Path], List[Path]],
) -> bool:
    original = read_text(file_path)
    rewritten = original
    runtime_root_groups = detect_frontend_runtime_root_groups(repo_dir) if detect_frontend_runtime_root_groups is not None else {}
    runtime_roots = list(runtime_root_groups.get("service_roots", ())) if isinstance(runtime_root_groups, dict) else []
    if not runtime_roots:
        runtime_roots = detect_frontend_runtime_roots(repo_dir)
    if file_path.suffix in {".html", ".js", ".mjs", ".ts", ".tsx", ".jsx"}:
        rewritten = _rewrite_client_request_text(rewritten)
        if file_path.suffix == ".html":
            rewritten = rewrite_inline_html_api_fetch_path(rewritten)
            rewritten = rewrite_html_relative_asset_urls(rewritten, repo_dir, file_path, runtime_roots)
            def replace_root_relative_attr(attr: str, url: str) -> Optional[str]:
                if not should_prefix_root_relative_html_url(attr, url, file_path, repo_dir, vite_project_roots=vite_project_roots):
                    return None
                return f"{base_path}{url}"

            rewritten = _rewrite_root_relative_html_attrs_outside_scripts(
                rewritten,
                attrs=("src", "href", "action"),
                replace_url=replace_root_relative_attr,
            )
            rewritten = inject_tool_base_runtime(rewritten, base_path)
    if rewritten != original:
        write_text(file_path, rewritten)
        return True
    return False


def find_project_rewrite_targets(
    repo_dir: Path,
    project: FrontendProjectStrategy,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    discover_runtime_root_frontend_files: Callable[[Path], List[Path]],
    is_server_side_code_file: Callable[[Path, Path], bool],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    patterns = [
        "*.html",
        "src/**/*.html",
        "src/**/*.js",
        "src/**/*.mjs",
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
        "audioqas/web/static/**/*.html",
        "audioqas/web/static/**/*.js",
    ]
    targets: List[Path] = []
    for file_path in collect_matching_files(repo_dir, patterns):
        relative = file_path.relative_to(repo_dir).as_posix()
        if relative.startswith(("server/", "backend/", "api/")):
            continue
        if is_server_side_code_file(file_path, repo_dir):
            continue
        targets.append(file_path)
    targets.extend(discover_runtime_root_frontend_files(repo_dir))
    for source_root in project.source_roots if "declaration" in project.evidence else ():
        try:
            relative_root = source_root.relative_to(repo_dir.resolve())
        except ValueError:
            continue
        for pattern in ("**/*.html", "**/*.js", "**/*.mjs", "**/*.ts", "**/*.tsx", "**/*.jsx"):
            for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{pattern}"]):
                if is_server_side_code_file(file_path, repo_dir):
                    continue
                targets.append(file_path)
    if project.framework == "vite":
        for project_root in vite_project_roots(repo_dir):
            try:
                relative_root = project_root.relative_to(repo_dir.resolve())
            except ValueError:
                continue
            for pattern in ("index.html", "src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.jsx", "src/**/*.mjs"):
                for file_path in collect_matching_files(repo_dir, [f"{relative_root.as_posix()}/{pattern}"]):
                    if is_server_side_code_file(file_path, repo_dir):
                        continue
                    targets.append(file_path)
    return _sorted_unique_paths(_paths_under_project(targets, project))


def find_plan_rewrite_targets(
    repo_dir: Path,
    plan: SubpathPlan,
    *,
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    discover_runtime_root_frontend_files: Callable[[Path], List[Path]],
    is_server_side_code_file: Callable[[Path, Path], bool],
    vite_project_roots: Callable[[Path], List[Path]],
) -> List[Path]:
    targets: List[Path] = []
    for project in plan.projects:
        if project.capabilities and "client_code" not in project.capabilities and "html_entry" not in project.capabilities:
            continue
        targets.extend(
            find_project_rewrite_targets(
                repo_dir,
                project,
                collect_matching_files=collect_matching_files,
                discover_runtime_root_frontend_files=discover_runtime_root_frontend_files,
                is_server_side_code_file=is_server_side_code_file,
                vite_project_roots=vite_project_roots,
            )
        )
    return _sorted_unique_paths(targets)
