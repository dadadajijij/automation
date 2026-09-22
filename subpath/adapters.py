import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from .common import build_deployment_base_path
from .models import FrontendProjectStrategy, SubpathPlan


NEXTJS_RUNTIME_SHIM_BASENAME = "ka_tool_base_runtime"
NEXTJS_WINDOW_TYPES_BASENAME = "ka_tool_window"


def _vite_allowed_host_literal(host: str) -> str:
    return json.dumps(host, ensure_ascii=True)


def _matching_brace_index(text: str, open_index: int) -> Optional[int]:
    depth = 0
    quote: Optional[str] = None
    escaped = False
    line_comment = False
    block_comment = False
    index = open_index
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _vite_config_object_span(text: str, property_name: str) -> Optional[tuple[int, int]]:
    match = re.search(rf"\b{re.escape(property_name)}\s*:\s*\{{", text)
    if not match:
        return None
    open_index = text.find("{", match.start(), match.end())
    close_index = _matching_brace_index(text, open_index)
    if close_index is None:
        return None
    return open_index, close_index


def _insert_vite_object_property(text: str, open_index: int, property_text: str) -> str:
    if text[open_index + 1 :].startswith("\n"):
        return text[: open_index + 1] + f"\n  {property_text}" + text[open_index + 1 :]
    return text[: open_index + 1] + f" {property_text} " + text[open_index + 1 :]


def ensure_vite_preview_allowed_host(text: str, host: Optional[str]) -> str:
    """Add one exact external host to Vite's preview.allowedHosts setting."""
    normalized_host = host.strip() if isinstance(host, str) else ""
    if not normalized_host:
        return text

    preview_span = _vite_config_object_span(text, "preview")
    if preview_span is None:
        for pattern in (r"defineConfig\s*\(\s*\{", r"\breturn\s*\{", r"export\s+default\s+\{"):
            match = re.search(pattern, text)
            if match:
                open_index = text.find("{", match.start(), match.end())
                return _insert_vite_object_property(
                    text,
                    open_index,
                    f"preview: {{ allowedHosts: [{_vite_allowed_host_literal(normalized_host)}] }},",
                )
        return text

    open_index, close_index = preview_span
    preview_text = text[open_index + 1 : close_index]
    allowed_match = re.search(
        r"\ballowedHosts\s*:\s*(?P<value>\[[^\]]*\]|true|['\"][^'\"]*['\"])",
        preview_text,
        flags=re.DOTALL,
    )
    if allowed_match is None:
        insertion = f"allowedHosts: [{_vite_allowed_host_literal(normalized_host)}],"
        return text[: open_index + 1] + f"\n  {insertion}" + text[open_index + 1 :]

    value = allowed_match.group("value")
    if value == "true":
        return text
    if value.startswith("["):
        if re.search(rf"['\"]{re.escape(normalized_host)}['\"]", value):
            return text
        inner = value[1:-1].rstrip()
        separator = ", " if inner else ""
        replacement = f"[{inner}{separator}{_vite_allowed_host_literal(normalized_host)}]"
    else:
        replacement = f"[{value}, {_vite_allowed_host_literal(normalized_host)}]"
    value_start = open_index + 1 + allowed_match.start("value")
    value_end = open_index + 1 + allowed_match.end("value")
    return text[:value_start] + replacement + text[value_end:]


def _shared_repo_root(plan: SubpathPlan) -> Optional[Path]:
    if not plan.projects:
        return None
    if len(plan.projects) == 1:
        project = plan.projects[0]
        if project.config_files:
            config_parent = project.config_files[0].resolve().parent
            if config_parent != project.project_root.resolve():
                return project.project_root.resolve().parent
        if project.source_roots:
            first_source_root = project.source_roots[0].resolve()
            if first_source_root != project.project_root.resolve():
                return project.project_root.resolve().parent
        if project.runtime_roots:
            first_runtime_root = project.runtime_roots[0].resolve()
            if first_runtime_root != project.project_root.resolve():
                return project.project_root.resolve().parent
        return project.project_root.resolve()
    common_parts = list(plan.projects[0].project_root.resolve().parts)
    for project in plan.projects[1:]:
        project_parts = list(project.project_root.resolve().parts)
        max_len = min(len(common_parts), len(project_parts))
        index = 0
        while index < max_len and common_parts[index] == project_parts[index]:
            index += 1
        common_parts = common_parts[:index]
        if not common_parts:
            return None
    return Path(*common_parts)


def ensure_vite_base_config(
    repo_dir: Path,
    project_slug: str,
    *,
    vite_config_file: Callable[[Path], Optional[Path]],
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    preview_allowed_host: Optional[str] = None,
) -> List[str]:
    config_path = vite_config_file(repo_dir)
    if config_path is None:
        return []
    base_path = f"{build_deployment_base_path(project_slug)}/"
    original = read_text(config_path)
    rewritten = original
    if re.search(r"defineConfig\(\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"defineConfig\(\s*\{\s*\}",
            f'defineConfig({{\n  base: "{base_path}",\n}}',
            rewritten,
            count=1,
        )
    if "base:" not in rewritten:
        rewritten = re.sub(r"(defineConfig\(\s*\{\n)", rf'\1  base: "{base_path}",' + "\n", rewritten, count=1)
        rewritten = re.sub(r"(export\s+default\s+\{\n)", rf'\1  base: "{base_path}",' + "\n", rewritten, count=1)
    rewritten = ensure_vite_preview_allowed_host(rewritten, preview_allowed_host)
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def ensure_vite_base_config_for_project(
    project: FrontendProjectStrategy,
    project_slug: str,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    preview_allowed_host: Optional[str] = None,
) -> List[str]:
    for config_path in project.config_files:
        if config_path.name.startswith("vite.config.") or ("declaration" in project.evidence and config_path.suffix in {".ts", ".js", ".mjs", ".cjs"}):
            base_path = f"{build_deployment_base_path(project_slug)}/"
            original = read_text(config_path)
            rewritten = original
            if re.search(r"defineConfig\(\s*\{\s*\}", rewritten):
                rewritten = re.sub(
                    r"defineConfig\(\s*\{\s*\}",
                    f'defineConfig({{\n  base: "{base_path}",\n}}',
                    rewritten,
                    count=1,
                )
            if "base:" not in rewritten:
                rewritten = re.sub(r"(defineConfig\(\s*\{\n)", rf'\1  base: "{base_path}",' + "\n", rewritten, count=1)
                rewritten = re.sub(r"(export\s+default\s+\{\n)", rf'\1  base: "{base_path}",' + "\n", rewritten, count=1)
            rewritten = ensure_vite_preview_allowed_host(rewritten, preview_allowed_host)
            if rewritten != original:
                write_text(config_path, rewritten)
                try:
                    relative_path = config_path.relative_to(project.project_root).as_posix()
                except ValueError:
                    relative_path = config_path.name
                return [relative_path]
            return []
    return []


def ensure_vue_cli_public_path(
    repo_dir: Path,
    project_slug: str,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    config_path = repo_dir / "vue.config.js"
    base_path = f"{build_deployment_base_path(project_slug)}/"
    if not config_path.exists():
        write_text(config_path, f"module.exports = {{\n  publicPath: '{base_path}',\n}}\n")
        return [config_path.relative_to(repo_dir).as_posix()]
    original = read_text(config_path)
    rewritten = original
    if re.search(r"module\.exports\s*=\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"module\.exports\s*=\s*\{\s*\}",
            f"module.exports = {{\n  publicPath: '{base_path}',\n}}",
            rewritten,
            count=1,
        )
    if "publicPath:" not in rewritten:
        rewritten = re.sub(r"(module\.exports\s*=\s*\{\n)", rf"\1  publicPath: '{base_path}'," + "\n", rewritten, count=1)
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def ensure_vue_cli_public_path_for_project(
    project: FrontendProjectStrategy,
    project_slug: str,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    for config_path in project.config_files:
        if config_path.name == "vue.config.js" or ("declaration" in project.evidence and config_path.suffix == ".js"):
            base_path = f"{build_deployment_base_path(project_slug)}/"
            if not config_path.exists():
                write_text(config_path, f"module.exports = {{\n  publicPath: '{base_path}',\n}}\n")
                return [config_path.relative_to(project.project_root).as_posix()]
            original = read_text(config_path)
            rewritten = original
            if re.search(r"module\.exports\s*=\s*\{\s*\}", rewritten):
                rewritten = re.sub(r"module\.exports\s*=\s*\{\s*\}", f"module.exports = {{\n  publicPath: '{base_path}',\n}}", rewritten, count=1)
            if "publicPath:" not in rewritten:
                rewritten = re.sub(r"(module\.exports\s*=\s*\{\n)", rf"\1  publicPath: '{base_path}'," + "\n", rewritten, count=1)
            if rewritten != original:
                write_text(config_path, rewritten)
                return [config_path.relative_to(project.project_root).as_posix()]
            return []
    return []


def ensure_cra_homepage(
    repo_dir: Path,
    project_slug: str,
    *,
    parse_package_json: Callable[[Path], dict],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    package_path = repo_dir / "package.json"
    if not package_path.exists():
        return []
    package = parse_package_json(repo_dir)
    desired_homepage = build_deployment_base_path(project_slug)
    if package.get("homepage") == desired_homepage:
        return []
    package["homepage"] = desired_homepage
    write_text(package_path, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    return [package_path.relative_to(repo_dir).as_posix()]


def ensure_cra_homepage_for_project(
    project: FrontendProjectStrategy,
    project_slug: str,
    *,
    parse_package_json: Callable[[Path], dict],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    package_path = next((path for path in project.config_files if path.name == "package.json"), project.project_root / "package.json")
    if not package_path.exists():
        return []
    package = parse_package_json(project.project_root)
    desired_homepage = build_deployment_base_path(project_slug)
    if package.get("homepage") == desired_homepage:
        return []
    package["homepage"] = desired_homepage
    write_text(package_path, json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    return [package_path.relative_to(project.project_root).as_posix()]


def nextjs_runtime_import_path(entry_path: Path, shim_path: Path) -> str:
    relative = shim_path.relative_to(entry_path.parent).as_posix() if shim_path.parent == entry_path.parent else os.path.relpath(shim_path, entry_path.parent).replace("\\", "/")
    if relative.endswith(".ts"):
        relative = relative[:-3]
    if not relative.startswith("."):
        relative = f"./{relative}"
    return relative


def ensure_nextjs_runtime_import(
    entry_path: Path,
    shim_path: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    original = read_text(entry_path)
    import_path = nextjs_runtime_import_path(entry_path, shim_path)
    import_line = f'import "{import_path}"'
    if import_line in original or f"import '{import_path}'" in original:
        return False
    rewritten = f'{import_line}\n{original}'
    write_text(entry_path, rewritten)
    return True


def remove_nextjs_runtime_import(
    entry_path: Path,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> bool:
    original = read_text(entry_path)
    rewritten = re.sub(
        rf'^\s*import\s+["\'].*{re.escape(NEXTJS_RUNTIME_SHIM_BASENAME)}["\']\s*;?\s*\n?',
        "",
        original,
        flags=re.MULTILINE,
    )
    if rewritten != original:
        write_text(entry_path, rewritten)
        return True
    return False


def ensure_nextjs_basepath_config(
    repo_dir: Path,
    project_slug: str,
    *,
    nextjs_config_file: Callable[[Path], Optional[Path]],
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    config_path = nextjs_config_file(repo_dir)
    if config_path is None:
        return []
    base_path = build_deployment_base_path(project_slug)
    original = read_text(config_path)
    rewritten = original
    if re.search(r"const\s+nextConfig(?:\s*:\s*NextConfig)?\s*=\s*\{\s*\}", rewritten):
        rewritten = re.sub(
            r"const\s+nextConfig(\s*:\s*NextConfig)?\s*=\s*\{\s*\}",
            f'const nextConfig\\1 = {{\n  basePath: "{base_path}",\n  assetPrefix: "{base_path}",\n}}',
            rewritten,
            count=1,
        )
    if "basePath:" not in rewritten:
        rewritten = re.sub(r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)", rf'\1  basePath: "{base_path}",' + "\n", rewritten, count=1)
        rewritten = re.sub(r"(const\s+nextConfig\s*=\s*\{\n)", rf'\1  basePath: "{base_path}",' + "\n", rewritten, count=1)
    if "assetPrefix:" not in rewritten:
        rewritten = re.sub(r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)", rf'\1  assetPrefix: "{base_path}",' + "\n", rewritten, count=1)
        rewritten = re.sub(r"(const\s+nextConfig\s*=\s*\{\n)", rf'\1  assetPrefix: "{base_path}",' + "\n", rewritten, count=1)
    if rewritten != original:
        write_text(config_path, rewritten)
        return [config_path.relative_to(repo_dir).as_posix()]
    return []


def ensure_nextjs_basepath_config_for_project(
    project: FrontendProjectStrategy,
    project_slug: str,
    *,
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    for config_path in project.config_files:
        if config_path.name.startswith("next.config.") or ("declaration" in project.evidence and config_path.suffix in {".ts", ".js", ".mjs", ".cjs"}):
            base_path = build_deployment_base_path(project_slug)
            original = read_text(config_path)
            rewritten = original
            if re.search(r"const\s+nextConfig(?:\s*:\s*NextConfig)?\s*=\s*\{\s*\}", rewritten):
                rewritten = re.sub(
                    r"const\s+nextConfig(\s*:\s*NextConfig)?\s*=\s*\{\s*\}",
                    f'const nextConfig\\1 = {{\n  basePath: "{base_path}",\n  assetPrefix: "{base_path}",\n}}',
                    rewritten,
                    count=1,
                )
            if "basePath:" not in rewritten:
                rewritten = re.sub(r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)", rf'\1  basePath: "{base_path}",' + "\n", rewritten, count=1)
                rewritten = re.sub(r"(const\s+nextConfig\s*=\s*\{\n)", rf'\1  basePath: "{base_path}",' + "\n", rewritten, count=1)
            if "assetPrefix:" not in rewritten:
                rewritten = re.sub(r"(const\s+nextConfig\s*:\s*NextConfig\s*=\s*\{\n)", rf'\1  assetPrefix: "{base_path}",' + "\n", rewritten, count=1)
                rewritten = re.sub(r"(const\s+nextConfig\s*=\s*\{\n)", rf'\1  assetPrefix: "{base_path}",' + "\n", rewritten, count=1)
            if rewritten != original:
                write_text(config_path, rewritten)
                return [config_path.relative_to(project.project_root).as_posix()]
            return []
    return []


def ensure_next_link_import(text: str) -> str:
    if re.search(r"""^\s*import\s+Link\s+from\s+['"]next/link['"]\s*$""", text, flags=re.MULTILINE):
        return text
    return insert_import_after_directives(text, 'import Link from "next/link"')


def ensure_with_tool_base_import(text: str, entry_path: Path, helper_path: Path) -> str:
    import_path = nextjs_runtime_import_path(entry_path, helper_path)
    import_line = f'import {{ withToolBase }} from "{import_path}"'
    if import_line in text or f"import {{ withToolBase }} from '{import_path}'" in text:
        return text
    return insert_import_after_directives(text, import_line)


def insert_import_after_directives(text: str, import_line: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    directive_pattern = re.compile(r'^\s*["\']use (client|server)["\']\s*;?\s*$')
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if directive_pattern.match(stripped):
            insert_at = index + 1
            continue
        break
    lines.insert(insert_at, f"{import_line}\n")
    return "".join(lines)


def rewrite_nextjs_jsx_anchor_hrefs(text: str) -> str:
    rewritten = text
    anchor_pattern = re.compile(r'<a(?P<attrs>\s+[^>]*?\bhref\s*=\s*(?P<quote>["\'])(?P<href>/[^"\']*)(?P=quote)[^>]*)>(?P<body>.*?)</a>', re.DOTALL)

    def replace_anchor(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        href = match.group("href")
        body = match.group("body")
        rewritten_attrs = re.sub(r'\bhref\s*=\s*(["\'])/[^"\']*(\1)', f'href="{href}"', attrs, count=1)
        return f"<Link{rewritten_attrs}>{body}</Link>"

    rewritten = anchor_pattern.sub(replace_anchor, rewritten)
    return rewritten


def rewrite_nextjs_fetch_calls(text: str) -> str:
    rewritten = text
    rewritten = re.sub(r'fetch\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)', r'fetch(withToolBase("\2"))', rewritten)
    rewritten = re.sub(r'fetch\(\s*(["\'`])(/[^"\'`]*)(\1)\s*,', r'fetch(withToolBase("\2"),', rewritten)
    rewritten = re.sub(r'new\s+Request\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)', r'new Request(withToolBase("\2"))', rewritten)
    rewritten = re.sub(r'new\s+Request\(\s*(["\'`])(/[^"\'`]*)(\1)\s*,', r'new Request(withToolBase("\2"),', rewritten)
    rewritten = re.sub(r'new\s+EventSource\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)', r'new EventSource(withToolBase("\2"))', rewritten)
    rewritten = re.sub(r'new\s+EventSource\(\s*(`\/[^`]*\$\{[^`]+\}[^`]*`)\s*\)', r'new EventSource(withToolBase(\1))', rewritten)
    rewritten = re.sub(r'axios\.(get|post|put|delete|patch)\(\s*(["\'`])(/[^"\'`]*)(\2)', r'axios.\1(withToolBase("\3")', rewritten)
    rewritten = re.sub(r'url:\s*(["\'`])(/[^"\'`]*)(\1)', r'url: withToolBase("\2")', rewritten)
    rewritten = re.sub(r'axios\(\s*\{\s*([^}]*)url:\s*(["\'`])(/[^"\'`]*)(\2)', r'axios({ \1url: withToolBase("\3")', rewritten)
    rewritten = re.sub(r'url:\s*(["\'`])(/[^"\'`]*)(\1)', r'url: withToolBase("\2")', rewritten)
    rewritten = re.sub(r'window\.location\.href\s*=\s*(["\'`])(/[^"\'`]*)(\1)', r'window.location.href = withToolBase("\2")', rewritten)
    rewritten = re.sub(r'window\.location\.assign\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)', r'window.location.assign(withToolBase("\2"))', rewritten)
    rewritten = re.sub(r'window\.open\(\s*(["\'`])(/[^"\'`]*)(\1)\s*\)', r'window.open(withToolBase("\2"))', rewritten)
    rewritten = re.sub(r'\breturn\s+(["\'`])(/[^"\'`]*)(\1)', r'return withToolBase("\2")', rewritten)
    rewritten = re.sub(r'\breturn\s+(`\/[^`]*\$\{[^`]+\}[^`]*`)', r'return withToolBase(\1)', rewritten)
    return rewritten


def rewrite_nextjs_form_actions(text: str) -> str:
    return re.sub(
        r'<form(?P<attrs>[^>]*?)\s+action=(?P<quote>["\'])(?P<path>/[^"\']*)(?P=quote)(?P<rest>[^>]*)>',
        lambda match: f'<form{match.group("attrs")} action={{withToolBase("{match.group("path")}")}}{match.group("rest")}>',
        text,
    )


def rewrite_nextjs_metadata_icon_urls(text: str, base_path: str) -> str:
    """Make Next Metadata icon URLs independent of build-time public env values."""
    def prefix_asset(asset_path: str) -> str:
        if asset_path == base_path or asset_path.startswith(f"{base_path}/"):
            return asset_path
        return f"{base_path}{asset_path}"

    direct_icon_pattern = re.compile(
        r'(?P<property>\b(?:icon|shortcut|apple)\s*:\s*)(?P<quote>["\'])(?P<asset>/(?!/)[^"\']*)(?P=quote)'
    )
    rewritten = direct_icon_pattern.sub(
        lambda match: f'{match.group("property")}"{prefix_asset(match.group("asset"))}"',
        text,
    )
    dynamic_icon_pattern = re.compile(
        r'(?P<property>\b(?:icon|shortcut|apple)\s*:\s*)`\$\{[^`{}]*\}/(?P<asset>[^`]+)`'
    )
    return dynamic_icon_pattern.sub(
        lambda match: f'{match.group("property")}"{base_path}/{match.group("asset")}"',
        rewritten,
    )


def ensure_nextjs_helper_module(
    entry_path: Path,
    repo_dir: Path,
    project_slug: str,
    *,
    read_text_if_exists: Callable[[Path], Optional[str]],
    write_text: Callable[[Path, str], None],
) -> List[str]:
    shim_path = repo_dir / f"{NEXTJS_RUNTIME_SHIM_BASENAME}.ts"
    base_path = build_deployment_base_path(project_slug)
    shim_text = (
        f'  const basePath = "{base_path}"\n'
        "\n"
        "export function withToolBase(path: string): string {\n"
        "    if (!path) return path\n"
        "    if (/^(https?:)?\\/\\//.test(path)) return path\n"
        "    if (path === basePath || path.startsWith(`${basePath}/`)) return path\n"
        "    if (path.startsWith('/')) return `${basePath}${path}`\n"
        "    return `${basePath}/${path.replace(/^\\/+/, '')}`\n"
        "}\n"
    )
    changed: List[str] = []
    original_shim = read_text_if_exists(shim_path)
    if original_shim != shim_text:
        write_text(shim_path, shim_text)
        changed.append(shim_path.relative_to(repo_dir).as_posix())
    return changed


def auto_fix_nextjs_subpath_issues(
    repo_dir: Path,
    project_slug: str,
    *,
    nextjs_entry_file: Callable[[Path], Optional[Path]],
    collect_matching_files: Callable[[Path, List[str]], List[Path]],
    read_text: Callable[[Path], str],
    read_text_if_exists: Callable[[Path], Optional[str]],
    write_text: Callable[[Path, str], None],
    sorted_unique: Callable[[List[str]], List[str]],
) -> List[str]:
    changed: List[str] = []
    entry_path = nextjs_entry_file(repo_dir)
    if entry_path is not None:
        helper_changes = ensure_nextjs_helper_module(
            entry_path,
            repo_dir,
            project_slug,
            read_text_if_exists=read_text_if_exists,
            write_text=write_text,
        )
        changed.extend(helper_changes)
    source_patterns = [
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.jsx",
        "src/**/*.js",
        "pages/**/*.ts",
        "pages/**/*.tsx",
        "pages/**/*.jsx",
        "pages/**/*.js",
        "app/**/*.ts",
        "app/**/*.tsx",
        "app/**/*.jsx",
        "app/**/*.js",
        "components/**/*.ts",
        "components/**/*.tsx",
        "components/**/*.jsx",
        "components/**/*.js",
    ]
    helper_path = repo_dir / f"{NEXTJS_RUNTIME_SHIM_BASENAME}.ts"
    for file_path in collect_matching_files(repo_dir, source_patterns):
        original = read_text(file_path)
        rewritten = rewrite_nextjs_metadata_icon_urls(
            original,
            build_deployment_base_path(project_slug),
        )
        if re.search(r'<a\b[^>]*\bhref\s*=\s*(["\'])/[^"\']*\1', rewritten):
            rewritten = ensure_next_link_import(rewritten)
            rewritten = rewrite_nextjs_jsx_anchor_hrefs(rewritten)
        form_rewritten = rewrite_nextjs_form_actions(rewritten)
        if form_rewritten != rewritten:
            rewritten = form_rewritten
        request_rewritten = rewrite_nextjs_fetch_calls(rewritten)
        if request_rewritten != rewritten:
            rewritten = ensure_with_tool_base_import(request_rewritten, file_path, helper_path)
        elif form_rewritten != original:
            rewritten = ensure_with_tool_base_import(rewritten, file_path, helper_path)
        else:
            rewritten = request_rewritten
        if rewritten != original:
            write_text(file_path, rewritten)
            changed.append(file_path.relative_to(repo_dir).as_posix())
    return sorted_unique(changed)


def apply_nextjs_subpath_adapter(
    repo_dir: Path,
    project_slug: str,
    *,
    ensure_nextjs_basepath_config_fn: Callable[[Path, str], List[str]],
    auto_fix_nextjs_subpath_issues_fn: Callable[[Path, str], List[str]],
) -> List[str]:
    changed: List[str] = []
    changed.extend(ensure_nextjs_basepath_config_fn(repo_dir, project_slug))
    legacy_types = repo_dir / f"{NEXTJS_WINDOW_TYPES_BASENAME}.d.ts"
    if legacy_types.exists():
        legacy_types.unlink()
        changed.append(legacy_types.relative_to(repo_dir).as_posix())
    changed.extend(auto_fix_nextjs_subpath_issues_fn(repo_dir, project_slug))
    return sorted({item for item in changed if item})


def apply_framework_config_adapter(
    project: FrontendProjectStrategy,
    project_slug: str,
    *,
    parse_package_json: Callable[[Path], dict],
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    preview_allowed_host: Optional[str] = None,
) -> List[str]:
    if project.adapter == "vite":
        return ensure_vite_base_config_for_project(
            project,
            project_slug,
            read_text=read_text,
            write_text=write_text,
            preview_allowed_host=preview_allowed_host,
        )
    if project.adapter == "nextjs":
        return ensure_nextjs_basepath_config_for_project(project, project_slug, read_text=read_text, write_text=write_text)
    if project.adapter == "vue_cli":
        return ensure_vue_cli_public_path_for_project(project, project_slug, read_text=read_text, write_text=write_text)
    if project.adapter == "cra":
        return ensure_cra_homepage_for_project(project, project_slug, parse_package_json=parse_package_json, write_text=write_text)
    return []


def apply_framework_config_adapters(
    plan: SubpathPlan,
    project_slug: str,
    *,
    parse_package_json: Callable[[Path], dict],
    read_text: Callable[[Path], str],
    write_text: Callable[[Path, str], None],
    preview_allowed_host: Optional[str] = None,
) -> List[str]:
    changed: List[str] = []
    repo_root = _shared_repo_root(plan)
    for project in plan.projects:
        for item in apply_framework_config_adapter(
            project,
            project_slug,
            parse_package_json=parse_package_json,
            read_text=read_text,
            write_text=write_text,
            preview_allowed_host=preview_allowed_host,
        ):
            if "/" in item:
                changed.append(item)
                continue
            if repo_root is not None:
                try:
                    config_path = next(path for path in project.config_files if path.name == item)
                    changed.append(config_path.relative_to(repo_root).as_posix())
                    continue
                except StopIteration:
                    pass
            changed.append(item)
    return sorted({item for item in changed if item})


def apply_vite_subpath_adapter(
    repo_dir: Path,
    project_slug: str,
    *,
    ensure_vite_base_config_fn: Callable[[Path, str], List[str]],
    find_frontend_rewrite_targets: Callable[[Path], List[Path]],
    rewrite_frontend_subpath_urls: Callable[[Path, str, Path], bool],
    sorted_unique: Callable[[List[str]], List[str]],
) -> List[str]:
    changed: List[str] = []
    changed.extend(ensure_vite_base_config_fn(repo_dir, project_slug))
    base_path = build_deployment_base_path(project_slug)
    for file_path in find_frontend_rewrite_targets(repo_dir):
        if rewrite_frontend_subpath_urls(file_path, base_path, repo_dir):
            changed.append(file_path.relative_to(repo_dir).as_posix())
    return sorted_unique(changed)
