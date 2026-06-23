import re


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def build_deployment_base_path(project_slug: str) -> str:
    return f"/tools2/{slugify(project_slug)}"


def split_url_suffix(url: str) -> tuple[str, str]:
    match = re.match(r"^([^?#]*)(.*)$", url)
    if not match:
        return url, ""
    return match.group(1), match.group(2)


def looks_like_templated_value(value: str) -> bool:
    return any(token in value for token in ("{{", "}}", "<%", "%>", "${"))


def is_allowed_root_relative_url(url: str, base_path: str) -> bool:
    stripped = url.strip()
    if not stripped or not stripped.startswith("/"):
        return True
    if stripped.startswith("//"):
        return True
    if stripped.startswith("#"):
        return True
    lowered = stripped.lower()
    if lowered.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return True
    if stripped == base_path or stripped.startswith(f"{base_path}/"):
        return True
    return False
