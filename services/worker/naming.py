from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any


DEFAULT_TEMPLATE = "pixiv/{AI}/{age}/{user}-{user_id}/{id}-{title}"

TOKENS = {
    "id", "pid", "p", "user", "user_id", "title", "page_title", "page_type", "page_id",
    "tags", "tags_translate", "tags_transl_only", "page_tag", "type", "type_illust", "type_manga",
    "type_ugoira", "type_novel", "AI", "age", "age_r", "like", "bmk", "bmk_1000", "bmk_id",
    "view", "rank", "date", "upload_date", "task_date", "px", "char_count", "series_title",
    "series_order", "series_id", "sl", "multi_image_folder", "r18_g_folder", "match_tag_folder1",
    "match_tag_folder2",
}

CONDITIONAL_TOKENS = {
    "AI", "age_r", "page_tag", "type_illust", "type_manga", "type_ugoira", "type_novel",
    "multi_image_folder", "r18_g_folder", "match_tag_folder1", "match_tag_folder2",
}

FREE_TEXT_TOKENS = {
    "user", "title", "page_title", "tags", "tags_translate", "tags_transl_only", "page_tag",
    "series_title", "multi_image_folder", "r18_g_folder", "match_tag_folder1", "match_tag_folder2",
}

TOKEN_PATTERNS = {
    "id": r"\d+(?:_p\d+)?",
    "pid": r"\d+",
    "p": r"\d+",
    "user_id": r"\d+",
    "page_id": r"\d+",
    "type": r"Illustration|Manga|Ugoira|Novel",
    "type_illust": r"Illustration",
    "type_manga": r"Manga",
    "type_ugoira": r"Ugoira",
    "type_novel": r"Novel",
    "AI": r"AI",
    "age": r"All Ages|R-18G|R-18",
    "age_r": r"R-18G|R-18",
    "like": r"\d+",
    "bmk": r"\d+",
    "bmk_1000": r"\d+\+",
    "bmk_id": r"\d+",
    "view": r"\d+",
    "rank": r"#\d+",
    "date": r"\d{4}-\d{2}-\d{2}",
    "upload_date": r"\d{4}-\d{2}-\d{2}",
    "task_date": r"\d{4}-\d{2}-\d{2}",
    "px": r"\d+x\d+",
    "char_count": r"\d+",
    "series_order": r"#?\d+",
    "series_id": r"\d+",
    "sl": r"0|2|4|6",
    "page_type": r"[^/]+?",
}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledTemplate:
    template: str
    regex: re.Pattern[str]
    optional_segments: tuple[int, ...]

    def parse(self, relative_path: str) -> dict[str, Any] | None:
        normalized = relative_path.replace("\\", "/").strip("/")
        pure = PurePath(normalized)
        if pure.suffix:
            normalized = str(pure.with_suffix("")).replace("\\", "/")
        match = self.regex.fullmatch(normalized)
        if not match:
            return None
        values: dict[str, Any] = {key: value for key, value in match.groupdict().items() if value not in (None, "")}
        if "id" in values:
            identity = str(values["id"])
            id_match = re.fullmatch(r"(?P<pid>\d+)(?:_p(?P<p>\d+))?", identity)
            if id_match:
                values.setdefault("pid", id_match.group("pid"))
                if id_match.group("p") is not None:
                    values.setdefault("p", id_match.group("p"))
        for key in ("p", "user_id", "page_id", "like", "bmk", "bmk_id", "view", "char_count", "series_id", "sl"):
            if key in values and str(values[key]).isdigit():
                values[key] = int(values[key])
        for key in ("tags", "tags_translate", "tags_transl_only"):
            if key in values:
                raw = str(values[key])
                values[key] = [part.strip() for part in re.split(r"[,，]", raw) if part.strip()]
        if "px" in values:
            width, height = str(values["px"]).split("x", 1)
            values["px_width"] = int(width)
            values["px_height"] = int(height)
        return values


def _segment_parts(segment: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z0-9_]+)\}", segment):
        if match.start() > cursor:
            parts.append(("literal", segment[cursor:match.start()]))
        token = match.group(1)
        if token not in TOKENS:
            raise TemplateError(f"未知命名标记：{{{token}}}")
        parts.append(("token", token))
        cursor = match.end()
    if cursor < len(segment):
        parts.append(("literal", segment[cursor:]))
    if not parts:
        raise TemplateError("路径模板包含空目录段")
    return parts


def _validate_ambiguity(parts: list[tuple[str, str]]) -> None:
    previous_free = False
    for kind, value in parts:
        if kind == "literal":
            if value:
                previous_free = False
            continue
        current_free = value in FREE_TEXT_TOKENS or value not in TOKEN_PATTERNS
        if current_free and previous_free:
            raise TemplateError(f"相邻自由文本标记无法无歧义解析：{{{value}}}；请在标记之间加入固定分隔符")
        previous_free = current_free


def compile_template(template: str) -> CompiledTemplate:
    normalized = template.replace("\\", "/").strip("/")
    if not normalized:
        raise TemplateError("路径模板不能为空")
    segments = normalized.split("/")
    regex_parts: list[str] = []
    used_tokens: set[str] = set()
    optional_segments: list[int] = []
    for index, segment in enumerate(segments):
        parts = _segment_parts(segment)
        _validate_ambiguity(parts)
        only_conditional = len(parts) == 1 and parts[0][0] == "token" and parts[0][1] in CONDITIONAL_TOKENS
        inner: list[str] = []
        for kind, value in parts:
            if kind == "literal":
                inner.append(re.escape(value))
                continue
            if value in used_tokens:
                inner.append(f"(?P={value})")
                continue
            used_tokens.add(value)
            pattern = TOKEN_PATTERNS.get(value, r"[^/]+?")
            inner.append(f"(?P<{value}>{pattern})")
        prefix = "" if index == 0 else "/"
        if only_conditional:
            optional_segments.append(index)
            regex_parts.append(f"(?:{prefix}{''.join(inner)})?")
        else:
            regex_parts.append(prefix + "".join(inner))
    return CompiledTemplate(normalized, re.compile("".join(regex_parts), re.IGNORECASE), tuple(optional_segments))


def parse_with_source_root(compiled: CompiledTemplate, source_root_name: str, relative_path: str) -> dict[str, Any] | None:
    result = compiled.parse(relative_path)
    if result is not None:
        return result
    prefixed = f"{source_root_name}/{relative_path}".replace("\\", "/")
    return compiled.parse(prefixed)

