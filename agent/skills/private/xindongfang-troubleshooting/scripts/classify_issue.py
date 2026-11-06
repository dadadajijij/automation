#!/usr/bin/env python3
"""Classify New Oriental complaints locally; never query or change configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from bisect import bisect_left
from pathlib import Path
from typing import Any, Iterator

RULES_PATH = Path(__file__).resolve().parents[1] / "references" / "classification-rules.json"
MAX_INPUT_BYTES = 256 * 1024
ROLE_PATTERN = r"主讲老师|授课老师|老师|教师|学员|学生"
ROLE_IDS = {"主讲老师": "teacher", "授课老师": "teacher", "老师": "teacher", "教师": "teacher", "学员": "student", "学生": "student"}
PLATFORMS = {
    "HarmonyOS": r"harmonyos|鸿蒙",
    "Android": r"android|安卓",
    "iOS": r"(?<![a-z])ios(?![a-z])|ipad|iphone|苹果手机",
    "Windows": r"windows|(?<![a-z])win(?:32|64)?(?![a-z])",
    "macOS": r"macos|macbook|(?<![a-z])mac(?![a-z])",
    "PC": r"电脑|pc端|教师pc|老师pc",
}
IDENTITY_FIELDS = {"channel_name": "channel_names", "teacher_uid": "teacher_uids",
                   "student_uid": "student_uids", "user_uid": "user_uids"}
TOPIC_SWITCH = r"另一个问题|另外一个问题|下面(?:查|看)|接下来|再查|再看|换个|换一个|另一个(?:老师|教师|学生|学员|用户|教室|课程)"


def split_spans(text: str, separator: str) -> Iterator[tuple[int, str]]:
    start = 0
    for match in re.finditer(separator, text):
        if start < match.start():
            yield start, text[start:match.start()]
        start = match.end()
    if start < len(text):
        yield start, text[start:]


def mask_links(text: str, normalized: str, offsets: list[int]) -> tuple[str, int, list[str]]:
    """Mask URL targets without losing labels, prose boundaries or source offsets."""
    cleaned = list(normalized)
    count, consumed = 0, 0
    reasons = []
    for match in re.finditer(r"https?://", normalized):
        raw_start = offsets[match.start()]
        if raw_start < consumed:
            continue
        end, depth = offsets[match.end() - 1] + 1, 0
        while end < len(text):
            char = text[end]
            if char.isspace() or char in '<>\"\'，。；！？、【】（）':
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            if char in "[]":
                break
            end += 1
        # Bare Unicode paths may be valid URLs or prose glued to a URL.
        if re.search(r"[\u3400-\u9fff]", text[raw_start:end]) and not (
            text[max(0, raw_start - 2):raw_start] == "](" or text[max(0, raw_start - 1):raw_start] == "<"
        ):
            reasons.append("url_boundary_uncertain")
        left, right = bisect_left(offsets, raw_start), bisect_left(offsets, end)
        cleaned[left:right] = " " * (right - left)
        count += 1
        consumed = end
    return "".join(cleaned), count, unique(reasons)


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    characters, offsets = [], []
    for index, character in enumerate(text):
        value = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", unicodedata.normalize("NFKC", character)).lower()
        characters.extend(value)
        offsets.extend([index] * len(value))
    return "".join(characters), offsets


def unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def validate_rules(rules: Any) -> None:
    if not isinstance(rules, dict) or not isinstance(rules.get("version"), str) or not isinstance(rules.get("fallback_skill"), str):
        raise ValueError("invalid rules document")
    if not isinstance(rules.get("categories"), list) or not isinstance(rules.get("fallback_intents"), list):
        raise ValueError("rules require categories and fallback_intents lists")
    seen = set()
    for item in rules["categories"] + rules["fallback_intents"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in seen:
            raise ValueError("rule identifiers must be unique strings")
        seen.add(item["id"])
        if not isinstance(item.get("patterns"), list) or not item["patterns"]:
            raise ValueError("rules require nonempty pattern lists")
        for pattern in item["patterns"]:
            if not isinstance(pattern, str):
                raise ValueError("patterns must be strings")
            re.compile(pattern)
    for category in rules["categories"]:
        if not all(isinstance(category.get(field), str) for field in ("label", "section")):
            raise ValueError("categories require labels and reference sections")


def find_platforms(text: str) -> list[str]:
    return [name for name, pattern in PLATFORMS.items() if re.search(pattern, text)]


def extract_identity(text: str, context: dict[str, Any]) -> dict[str, Any]:
    """Only labeled numeric RTC identity candidates are promoted to fields."""
    channel_ids = re.findall(r"(?:课程|课次|讲次|教室|次)\s*(?:\(\s*)?id\s*[:：]?\s*(\d+)", text)
    role_uids: dict[str, list[str]] = {"teacher": [], "student": [], "user": []}
    for role, aliases in (("teacher", r"主讲老师|授课老师|老师|教师"), ("student", r"学员|学生"), ("user", r"用户")):
        role_uids[role].extend(re.findall(rf"(?:{aliases})\s*id\s*[:：]?\s*\(?\s*(\d+)", text))
        # Display names and business account numbers are not RTC UIDs.
        role_uids[role].extend(re.findall(rf"(?:{aliases})\s*[:：]\s*[^\n，,。]{{0,80}}?\(\s*(\d+)\s*\)", text))
    identity: dict[str, Any] = {
        "channel_names": unique(channel_ids),
        "teacher_uids": unique(role_uids["teacher"]),
        "student_uids": unique(role_uids["student"]),
        "user_uids": unique(role_uids["user"]),
        "platforms": find_platforms(text),
        "unlabeled_channel_candidates": re.findall(r"(?m)^\s*(\d{6,12})\s*$", text),
        "has_unmapped_student_account": bool(re.search(r"学员号\s*[:：]", text)),
    }
    validated = {}
    for key in IDENTITY_FIELDS:
        value = context.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"context.{key} must be a nonempty string")
            validated[key] = value.strip()
    platform = context.get("platform")
    if platform is not None and (not isinstance(platform, str) or platform not in PLATFORMS):
        raise ValueError("context.platform must be HarmonyOS, Android, iOS, Windows, macOS or PC")
    current_uids = set(identity["teacher_uids"] + identity["student_uids"] + identity["user_uids"])
    known_uids = {value for key, value in validated.items() if key != "channel_name"}
    channel_changed = bool(identity["channel_names"] and identity["channel_names"] != [validated.get("channel_name")])
    target_changed = bool(current_uids and not current_uids <= known_uids)
    target_changed |= bool(identity["user_uids"] and validated.get("user_uid") and identity["user_uids"] != [validated["user_uid"]])
    target_changed |= any(bool(identity[target] and validated.get(key) and identity[target] != [validated[key]])
                          for key, target in IDENTITY_FIELDS.items() if key not in ("channel_name", "user_uid"))
    topic_changed = bool(re.search(TOPIC_SWITCH, text))
    mentioned_roles = {ROLE_IDS[m[0]] for m in re.finditer(ROLE_PATTERN, text)}
    warnings, inherited = [], []
    if context and channel_changed:
        warnings.append("context_channel_changed_or_unverified")
    if context and (target_changed or topic_changed):
        warnings.append("context_target_changed_or_unverified")
    for key, target in IDENTITY_FIELDS.items():
        value = validated.get(key)
        if not value or identity[target]:
            continue
        if key == "channel_name":
            if re.search(r"另一个(?:教室|课程)|换个(?:教室|课程)", text):
                continue
        elif channel_changed or target_changed or topic_changed:
            continue
        elif key == "user_uid" and any(validated.get(role + "_uid") != value for role in mentioned_roles):
            continue
        elif identity["user_uids"] and key != "user_uid" and value not in identity["user_uids"]:
            continue
        identity[target] = [value]
        inherited.append(key)
    platform_uid = validated.get("user_uid") or (next(iter(known_uids)) if len(known_uids) == 1 else None)
    roles_match_platform = all(validated.get(role + "_uid") == platform_uid for role in mentioned_roles)
    if not identity["platforms"] and platform and platform_uid and roles_match_platform and not (
        channel_changed or target_changed or topic_changed or (current_uids and current_uids != {platform_uid})
    ):
        identity["platforms"] = [platform]
        inherited.append("platform")
    if platform and not identity["platforms"]:
        warnings.append("context_platform_unbound")
    identity["context_inherited_fields"] = inherited
    identity["context_warnings"] = warnings
    return identity


def negated_or_resolved(clause: str, match: re.Match[str], sentence: str) -> str | None:
    before = clause[max(0, match.start() - 12):match.start()]
    after = clause[match.end():match.end() + 12]
    if re.search(r"(?:不|没有|并非|不是)(?:太|很|特别)?(?:模糊|旋转|倒置|掉线|断线|杂音|噪声|回声)", match.group()):
        return "negated_symptom"
    if re.search(r"(?:不是|并非|没有|不存在|未出现|不再有|否认|无)\s*(?:任何|明显|持续|所谓的|麦克风|音频|视频|摄像头)?\s*$", before):
        return "negated_symptom"
    if re.match(r"\s*(?:(?:已经|已|现在|目前)\s*)?(?:消失了|恢复了|好了|解决了|没有了)", after):
        return "resolved_symptom"
    if re.search(r"(?:之前|原来|此前|上次).*(?:现在|目前).{0,4}(?:正常|好了|恢复了|没有了)", sentence):
        current = re.split(r"现在|目前", sentence, maxsplit=1)[-1]
        if match.group() not in current:
            return "historical_resolved_symptom"
    if re.search(r"引用(?:旧|历史)|历史问题(?:是|为)|上次的问题(?:是|为)", before):
        return "quoted_history_needs_review"
    return None


def symptom_binding(clause: str, phrase_start: int, identity: dict[str, Any], category_id: str) -> dict[str, Any]:
    direction = None
    relation = re.search(rf"({ROLE_PATTERN}).{{0,5}}(?:听不到|听不见|看不到|看不见).{{0,5}}({ROLE_PATTERN})", clause)
    if relation:
        receiver, source = ROLE_IDS[relation[1]], ROLE_IDS[relation[2]]
        direction = {"source_role": source, "receiver_role": receiver}
    else:
        relation = re.search(rf"({ROLE_PATTERN}).{{0,4}}(?:讲话|说话).{{0,8}}({ROLE_PATTERN}).{{0,5}}(?:听不到|听不见)", clause)
        if relation:
            direction = {"source_role": ROLE_IDS[relation[1]], "receiver_role": ROLE_IDS[relation[2]]}
    roles = list(re.finditer(ROLE_PATTERN, clause[:phrase_start]))
    role = ROLE_IDS[roles[-1][0]] if roles else None
    if direction:
        role = direction["source_role"] if category_id == "camera-capture-failure" else direction["receiver_role"]
    target = (role + "_uids") if role else "user_uids"
    candidates = extract_identity(clause, {}).get(target) or identity.get(target, [])
    if not candidates and not role:
        candidates = unique(identity["teacher_uids"] + identity["student_uids"] + identity["user_uids"])
    return {"subject_role": role, "subject_uid": candidates[0] if len(candidates) == 1 else None, "direction": direction}


def same_occurrence(first: dict[str, Any], second: dict[str, Any]) -> bool:
    a, b = first["source_span"], second["source_span"]
    return (first["scope_id"] == second["scope_id"] and a["start"] < b["end"] and b["start"] < a["end"]
            and all(first[key] == second[key] for key in ("subject_role", "subject_uid", "direction", "media_source")))


def media_source(clause: str) -> str | None:
    if re.search(r"麦克风|说话|讲话|开麦|闭麦|人声", clause):
        return "microphone"
    if re.search(r"loopback|共享(?:电脑|系统)?声音|系统声音|共享音频", clause):
        return "loopback"
    if re.search(r"播放|mp3|课件|伴奏|进度条|播放进度", clause):
        return "playback"
    return None


def inherit_binding(binding: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous and binding["subject_role"] is None and binding["direction"] is None and (
        binding["subject_uid"] is None or binding["subject_uid"] == previous["subject_uid"]
    ):
        return previous
    return binding


def classify(text: str, context: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("question text must be nonempty")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("question exceeds 256 KiB")
    context = {} if context is None else context
    if not isinstance(context, dict):
        raise ValueError("context must be a JSON object")
    rules = rules if rules is not None else json.loads(RULES_PATH.read_text(encoding="utf-8"))
    validate_rules(rules)
    normalized, offsets = normalize_with_offsets(text)

    def source_evidence(start: int, end: int) -> dict[str, Any]:
        left, right = offsets[start], offsets[end - 1] + 1
        return {"text": text[left:right], "source_span": {"start": left, "end": right}}

    cleaned, link_count, review_reasons = mask_links(text, normalized, offsets)
    identity = extract_identity(cleaned, context)
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    fallback_evidence: list[dict[str, Any]] = []
    scene = None
    scope_id, previous_end = 0, 0
    suppresses = {cat["id"]: cat.get("suppresses", []) for cat in rules["categories"]}
    separator = rf"[,，]|但是|但|不过|然而|而是|并且|以及|(?:和|同时|而且|而|还有)(?=\s*(?:{ROLE_PATTERN}|用户))"
    for sentence_start, sentence in split_spans(cleaned, r"[。！？!?\n；;]+"):
        for clause_start, clause in split_spans(sentence, separator):
            if not clause.strip():
                continue
            start = sentence_start + clause_start
            scope_id += 1
            binding = symptom_binding(clause, len(clause), identity, "")
            object_key = (binding["subject_role"], binding["subject_uid"])
            explicit_source = media_source(clause)
            new_topic = bool(re.search(TOPIC_SWITCH, clause))
            new_live = bool(re.search(r"直播|实时课堂|实时通话", clause))
            object_changed = scene and any(value is not None and value != old for value, old in zip(object_key, scene["object"]))
            if new_topic or new_live or object_changed or re.search(r"\n\s*\n", cleaned[previous_end:start]):
                scene = None
            replay = re.search(r"回放(?:\s*$|里|中)|(?:回放|录制结果|录制产物|录制文件).{0,12}(?:无声|没声|没有声音|画面|异常|卡|黑|绿)", clause)
            if replay and cleaned[start + replay.start():start + replay.end()] != normalized[start + replay.start():start + replay.end()]:
                replay = None
            elliptical = bool(re.match(r"\s*(?:还是|仍然|但是|现在|目前|也|却|就|一直|依旧|它|这个|这段|该音频|老师|教师|学生|学员)*\s*(?:没有声音|没声音|没声|无声|听不到|听不见|时长|总时长|长度|进度条|播放进度|卡住|播不了)", clause))
            inherit = bool(scene and scene["remaining"] and not new_topic and (elliptical or explicit_source == "playback"))
            replay_scope = bool(replay or (inherit and scene["source"] == "replay"))
            source = "replay" if replay_scope else explicit_source or (scene["source"] if inherit else "unknown")
            scope_text = clause + (" " + scene["text"] if inherit and not explicit_source else "")
            binding = inherit_binding(binding, scene["binding"] if inherit else None)
            object_key = (binding["subject_role"], binding["subject_uid"])
            scene_binding = binding if inherit else None
            if replay:
                raw = source_evidence(start + replay.start(), start + replay.end())
                fallback_evidence.append({"id": "replay-investigation", "matched_text": raw["text"],
                                          "source_span": raw["source_span"], "scope_id": scope_id,
                                          "media_source": source, **binding})
            if replay or explicit_source:
                scene = {"source": source, "object": object_key, "binding": binding, "text": clause, "remaining": 1}
            elif inherit:
                scene = {**scene, "remaining": scene["remaining"] - 1}
            else:
                scene = None
            previous_end = start + len(clause)
            local_matches: list[dict[str, Any]] = []
            for category in rules["categories"]:
                if category.get("requires_any") and not any(term in scope_text for term in category["requires_any"]):
                    continue
                if category.get("requires_pattern") and not re.search(category["requires_pattern"], scope_text):
                    continue
                for pattern_index, pattern in enumerate(category["patterns"]):
                    for match in re.finditer(pattern, clause):
                        if cleaned[start + match.start():start + match.end()] != normalized[start + match.start():start + match.end()]:
                            continue
                        raw = source_evidence(start + match.start(), start + match.end())
                        reason = negated_or_resolved(clause, match, sentence)
                        if reason:
                            excluded.append({"id": category["id"], "matched_text": raw["text"], "source_span": raw["source_span"], "reason": reason})
                            if reason == "quoted_history_needs_review":
                                review_reasons.append(reason)
                            continue
                        if replay_scope:
                            excluded.append({"id": category["id"], "matched_text": raw["text"], "source_span": raw["source_span"], "reason": "replay_scope"})
                            continue
                        if category["id"] == "courseware-audio-playback" and re.search(r"视频.{0,6}(?:进度条|播放进度)", scope_text) and not re.search(r"音频|mp3|课件音频", scope_text):
                            review_reasons.append("video_playback_is_not_courseware_audio")
                            continue
                        binding = symptom_binding(clause, match.start(), identity, category["id"])
                        binding = inherit_binding(binding, scene_binding)
                        platforms = find_platforms(clause) or identity["platforms"]
                        item = {
                            "id": category["id"], "label": category["label"],
                            "matched_text": raw["text"], "source_span": raw["source_span"],
                            "scope_id": scope_id, "media_source": source,
                            "match_type": "compound" if category.get("requires_any") else "explicit_phrase",
                            "rule_id": f"{category['id']}:{pattern_index + 1}",
                            "reference_section": category["section"],
                            "platforms": platforms, **binding,
                            "route": "specialized_rule", "pending_checks": [],
                        }
                        if category["id"] == "camera-capture-failure":
                            local_capture = bool(re.search(r"采集失败|摄像头打不开|摄像头无法打开|摄像头不能打开|摄像头启动失败|摄像头无法开启|本地(?:预览|画面).{0,3}(?:黑屏|没画面)", clause))
                            if not local_capture:
                                item["route"] = "general_support"
                                item["pending_checks"].append("capture_or_remote_visibility")
                            if platforms != ["Windows"]:
                                item["pending_checks"].append("windows_platform_required_for_customer_workaround")
                        if category["id"] == "video-blur" and platforms != ["Android"]:
                            item["pending_checks"].append("platform_and_camera_backend_compatibility")
                        if category["id"] == "audio-echo":
                            item["pending_checks"].append("echo_source_uid_not_inferred_from_complainant")
                        if category["id"] == "audio-low-volume":
                            item["pending_checks"].append("capture_or_playback_or_loopback_source")
                        local_matches.append(item)
            # Suppress only overlapping mentions of the same object's symptom.
            retained = []
            for item in local_matches:
                if any(item["id"] in suppresses[other["id"]] and same_occurrence(item, other) for other in local_matches):
                    excluded.append({"id": item["id"], "matched_text": item["matched_text"],
                                     "source_span": item["source_span"], "reason": "more_specific_symptom_same_occurrence"})
                else:
                    retained.append(item)
            candidates.extend(retained)
            for intent in rules["fallback_intents"]:
                for pattern in intent["patterns"]:
                    for match in re.finditer(pattern, clause):
                        if cleaned[start + match.start():start + match.end()] != normalized[start + match.start():start + match.end()]:
                            continue
                        if not negated_or_resolved(clause, match, sentence):
                            raw = source_evidence(start + match.start(), start + match.end())
                            binding = symptom_binding(clause, match.start(), identity, "")
                            binding = inherit_binding(binding, scene_binding)
                            item = {"id": intent["id"], "matched_text": raw["text"], "source_span": raw["source_span"],
                                    "scope_id": scope_id, "media_source": source,
                                    **binding}
                            if intent["id"] == "rtc-quality" and any(same_occurrence(item, other) for other in retained):
                                continue
                            fallback_evidence.append(item)
    # Keep separate subjects and directions even when they share a category.
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in candidates:
        direction_key = json.dumps(item["direction"], sort_keys=True)
        key = (item["id"], item["subject_role"], item["subject_uid"], direction_key, tuple(item["platforms"]), item["route"], item["media_source"])
        evidence = {"text": item["matched_text"], "rule_id": item["rule_id"],
                    "source_span": item["source_span"], "scope_id": item["scope_id"]}
        if key not in merged:
            merged[key] = {**item, "evidence": [evidence]}
        elif evidence not in merged[key]["evidence"]:
            merged[key]["evidence"].append(evidence)
    categories = list(merged.values())
    fallbacks = unique([item["id"] for item in fallback_evidence])
    semantic_ambiguity = bool(review_reasons) or any(c["route"] == "general_support" for c in categories)
    status = "ambiguous" if semantic_ambiguity else "matched" if categories else "unclassified"
    return {
        "rule_version": rules["version"], "status": status,
        "categories": categories, "identity": identity,
        "needs_clarification": bool(review_reasons), "review_reasons": unique(review_reasons),
        "excluded_matches": excluded,
        "fallback": {"skill": rules["fallback_skill"], "required": not categories or bool(fallbacks) or semantic_ambiguity,
                     "intents": fallbacks, "evidence": fallback_evidence},
        "attachment_link_count": link_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="UTF-8 plain-text question file; default: stdin")
    source.add_argument("--text", help="Literal question text (prefer --input for customer text)")
    parser.add_argument("--context", type=Path, help="Optional JSON with already confirmed identity/platform")
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--output", type=Path, help="Also save classification JSON")
    args = parser.parse_args()
    try:
        if args.input:
            with args.input.open("rb") as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
        elif args.text is not None:
            raw = args.text.encode("utf-8")
        else:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("question exceeds 256 KiB")
        context = json.loads(args.context.read_text(encoding="utf-8")) if args.context else {}
        result = classify(raw.decode("utf-8-sig"), context, json.loads(args.rules.read_text(encoding="utf-8")))
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0
    except (OSError, UnicodeError, ValueError, re.error) as exc:
        # Do not include raw customer text or signed URLs in diagnostics.
        print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": "Invalid input, context, rules or output path"}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
