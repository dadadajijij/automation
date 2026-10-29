"""Conservative, text-only edits of a direct Vite configuration object.

This deliberately does not evaluate JavaScript. Function exports, spreads,
templates and regular expressions are left to the existing runtime audit.
"""

import re
from typing import Optional


def _tokens(text: str) -> Optional[list[tuple[str, int, int]]]:
    tokens: list[tuple[str, int, int]] = []
    index = 0
    while index < len(text):
        start = index
        char = text[index]
        if char.isspace():
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
        elif char in "'\"":
            quote = char
            index += 1
            while index < len(text) and text[index] != quote:
                if text[index] in "\r\n":
                    return None
                index += 2 if text[index] == "\\" else 1
            if index >= len(text):
                return None
            index += 1
            tokens.append((text[start:index], start, index))
        elif char in "`/":
            # Neither templates nor regex/division expressions are needed for
            # the supported shape. Do not guess at their delimiter semantics.
            return None
        elif char.isalpha() or char in "_$":
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] in "_$"):
                index += 1
            tokens.append((text[start:index], start, index))
        else:
            index += 1
            tokens.append((char, start, index))
    return tokens


def direct_config_base(text: str, *, include_missing: bool = False) -> Optional[tuple[int, int, str]]:
    """Return the unique top-level base expression, or None for unknown shapes."""
    tokens = _tokens(text)
    if tokens is None:
        return None
    values = [token[0] for token in tokens]
    exports = [i for i in range(len(values) - 2) if values[i:i + 2] == ["export", "default"]]
    if len(exports) != 1:
        return None
    index = exports[0] + 2
    wrapped = values[index:index + 2] == ["defineConfig", "("]
    if wrapped:
        index += 2
    if index >= len(values) or values[index] != "{":
        return None

    stack = ["}"]
    properties: list[list[tuple[str, int, int]]] = []
    current: list[tuple[str, int, int]] = []
    pairs = {"{": "}", "[": "]", "(": ")"}
    for token_index in range(index + 1, len(tokens)):
        token = tokens[token_index]
        value = token[0]
        if value in pairs:
            stack.append(pairs[value])
        elif value in ("}", "]", ")"):
            if not stack or value != stack.pop():
                return None
            if not stack:
                if current:
                    properties.append(current)
                tail = values[token_index + 1:]
                if wrapped:
                    if not tail or tail[0] != ")":
                        return None
                    tail = tail[1:]
                if tail not in ([], [";"]):
                    return None
                break
        if value == "," and len(stack) == 1:
            properties.append(current)
            current = []
        else:
            current.append(token)
    else:
        return None

    bases = []
    for prop in properties:
        if not prop:
            continue
        # A spread or computed key may override base, including after our edit.
        if prop[0][0] in (".", "["):
            return None
        if prop[0][0] in ("base", "'base'", '"base"'):
            if len(prop) < 3 or prop[1][0] != ":":
                return None
            bases.append(prop[2:])
    if not bases and include_missing:
        return tokens[index][2], tokens[index][2], ""
    if len(bases) != 1:
        return None
    expression = bases[0]
    start, end = expression[0][1], expression[-1][2]
    return start, end, text[start:end]


def is_relative_base(expression: str) -> bool:
    """Only accept literals and a plain process.env variable with a safe fallback."""
    literal = r'''(?:"(?:\./?|)"|'(?:\./?|)')'''
    return bool(re.fullmatch(
        rf"\s*(?:{literal}|process\s*\.\s*env\s*\.\s*[A-Za-z_$][\w$]*\s*(?:\|\||\?\?)\s*{literal})\s*",
        expression,
    ))


def rewrite_vite_base(text: str, base_path: str, *, repair_relative: bool = False) -> tuple[str, str]:
    """Add a missing base or repair a positively identified relative base."""
    base = direct_config_base(text, include_missing=True)
    if base is None:
        return text, "unsupported_config"
    start, end, expression = base
    if start == end:
        suffix = "" if text[start:].startswith("\n") else "\n"
        return text[:start] + f'\n  base: "{base_path}",' + suffix + text[start:], "added_missing_base"
    if expression in (f'"{base_path}"', f"'{base_path}'"):
        return text, "already_prefixed"
    if not is_relative_base(expression):
        return text, "preserved_custom_base"
    if not repair_relative:
        return text, "preserved_relative_base"
    return text[:start] + f'"{base_path}"' + text[end:], "repaired_relative_base"
