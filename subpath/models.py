import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple


SUBPATH_ALLOWED_ROOT_COMMENT = "ka-subpath-allow-root"
SUBPATH_BROWSER_ATTRS = ("href", "src", "action")
SUBPATH_CLIENT_METHOD_PATTERNS = (
    re.compile(r'\bfetch\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bnew\s+Request\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bnew\s+EventSource\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\baxios\.(?:get|post|put|delete|patch)\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\baxios\(\s*\{\s*[^}]*\burl\s*:\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1', re.DOTALL),
    re.compile(r'\bwindow\.location\.href\s*=\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
    re.compile(r'\bwindow\.location\.assign\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1\s*\)'),
    re.compile(r'\bwindow\.open\(\s*(["\'`])/(?P<path>[^"\'`#?][^"\'`]*)\1'),
)
SUBPATH_TEMPLATE_ALLOWLIST_FIELDS = ("pathTemplate",)


@dataclass
class SubpathAuditFinding:
    file: str
    line: int
    severity: str
    code: str
    message: str
    detail_kind: Optional[str] = None


@dataclass
class HtmlUrlReference:
    tag: str
    attr: str
    url: str
    line: int


class HtmlUrlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: List[HtmlUrlReference] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        line, _offset = self.getpos()
        for attr, value in attrs:
            if attr in SUBPATH_BROWSER_ATTRS and isinstance(value, str):
                self.references.append(HtmlUrlReference(tag=tag, attr=attr, url=value, line=line))


@dataclass(frozen=True)
class RuntimeRootEvidence:
    path: Path
    source: str
    confidence: str
    detail: str


@dataclass(frozen=True)
class FrameworkCandidate:
    framework: str
    package_dir: Path
    confidence: str
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class FrontendProjectStrategy:
    project_id: str
    framework: str
    proxy_mode: str
    adapter: str
    project_root: Path
    source_roots: Tuple[Path, ...]
    runtime_roots: Tuple[Path, ...]
    config_files: Tuple[Path, ...]
    capabilities: Tuple[str, ...]
    evidence: Tuple[str, ...]
    source_adapter: str = "static_rewrite"
    runtime_entry_hint: Optional[str] = None
    runtime_entry_candidates: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SubpathPlan:
    projects: Tuple[FrontendProjectStrategy, ...]
    default_project: Optional[str]
    notes: Tuple[str, ...] = ()
