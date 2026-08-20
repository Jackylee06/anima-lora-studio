from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


SECTION_ORDER = (
    "quality_meta_year_safety",
    "subject_count",
    "characters",
    "series",
    "artists",
    "general",
)

ANIMA_RULES_VERSION = "anima-base-v1.0-2026-08-20"


def normalize_tag(tag: str, artist: bool = False) -> str:
    value = re.sub(r"\s+", " ", str(tag).strip().lower())
    if not re.fullmatch(r"score_[1-9]", value):
        value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip(" ,")
    if artist:
        value = f"@{value.lstrip('@').strip()}"
    return value


def _sentences(value: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", value.strip()) if part.strip()])


@dataclass(frozen=True)
class AssembledCaption:
    sections: dict[str, Any]
    text: str
    warnings: list[str]
    status: str


def assemble_caption(raw: dict[str, Any], profile: str, trigger: str, mode: str | None = None) -> AssembledCaption:
    output: dict[str, Any] = {}
    warnings = [str(item) for item in raw.get("warnings", []) if str(item).strip()]
    seen: set[str] = set()
    trigger_value = normalize_tag(trigger, artist=profile == "style")
    requested_mode = mode or ("hybrid" if profile == "style" else "tags")

    for section in SECTION_ORDER:
        values = raw.get(section, [])
        if not isinstance(values, list):
            warnings.append(f"{section} 不是数组，已忽略")
            values = []
        normalized: list[str] = []
        for item in values:
            tag = normalize_tag(str(item), artist=section == "artists")
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag)
        output[section] = normalized

    trigger_section = "artists" if profile == "style" else "characters"
    if trigger_value:
        for section in SECTION_ORDER:
            if section != trigger_section and trigger_value in output[section]:
                output[section].remove(trigger_value)
        if trigger_value not in output[trigger_section]:
            output[trigger_section].insert(0, trigger_value)

    tags = [tag for section in SECTION_ORDER for tag in output[section]]
    natural = str(raw.get("natural_language") or "").strip()
    if requested_mode == "tags":
        natural = ""
    elif natural and profile == "style" and _sentences(natural) < 2:
        warnings.append("画风 hybrid caption 的自然语言少于两句")
    output["natural_language"] = natural or None
    output["warnings"] = warnings
    text = ", ".join(tags)
    if natural:
        if text:
            text = f"{text}. {natural}"
        else:
            text = natural
    status = "needs_review" if warnings or not text else "draft"
    return AssembledCaption(output, text, warnings, status)


def fallback_from_wd(wd_result: dict[str, Any], profile: str, trigger: str) -> AssembledCaption:
    ratings = wd_result.get("rating", [])
    general = [item.get("tag", "") if isinstance(item, dict) else str(item) for item in wd_result.get("general", [])]
    characters = [item.get("tag", "") if isinstance(item, dict) else str(item) for item in wd_result.get("character", [])]
    subject_count: list[str] = []
    remaining: list[str] = []
    for tag in general:
        normalized = normalize_tag(tag)
        if re.fullmatch(r"\d+(?:girls?|boys?|others?)", normalized.replace(" ", "")):
            subject_count.append(normalized)
        else:
            remaining.append(normalized)
    safety: list[str] = []
    if ratings:
        top = ratings[0]
        label = top.get("tag", "") if isinstance(top, dict) else str(top)
        mapping = {"general": "safe", "sensitive": "sensitive", "questionable": "nsfw", "explicit": "explicit"}
        if normalize_tag(label) in mapping:
            safety.append(mapping[normalize_tag(label)])
    assembled = assemble_caption({
        "quality_meta_year_safety": safety,
        "subject_count": subject_count,
        "characters": characters,
        "series": [],
        "artists": [],
        "general": remaining,
        "natural_language": None,
        "warnings": ["LLM refine 失败，当前为 WD14 回退草稿"],
    }, profile, trigger, "tags")
    return assembled


def parse_llm_json(value: str) -> dict[str, Any]:
    candidate = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    result = json.loads(candidate)
    if not isinstance(result, dict):
        raise ValueError("LLM 输出必须是 JSON object")
    return result


def build_refine_messages(
    *, profile: str, trigger: str, metadata: dict[str, Any], wd: dict[str, Any], joycaption: str,
    identity_omit: Iterable[str] = (), source_artist_names: Iterable[str] = (), mode: str | None = None,
    custom_instructions: str = "",
) -> list[dict[str, str]]:
    requested_mode = mode or ("hybrid" if profile == "style" else "tags")
    if profile == "character":
        profile_rule = (
            "This is a character LoRA. Always place the exact trigger in characters. Describe changing clothing, pose, expression, composition and background. "
            "Do not add identity traits listed in identity_omit."
        )
    elif profile == "style":
        profile_rule = (
            "This is a style LoRA. Always place the exact @trigger in artists. Remove source artist identities and direct style labels. "
            "Describe subjects, objects, scene, medium-visible content and composition so the trigger learns the omitted style."
        )
    else:
        profile_rule = "This is a custom LoRA profile. Keep the exact trigger in characters and follow the project-specific instructions."
    if custom_instructions.strip():
        profile_rule += " Project-specific instructions: " + custom_instructions.strip()
    system = f"""You refine training captions for CircleStone Labs Anima Base v1.0.
Return JSON only with these keys: quality_meta_year_safety, subject_count, characters, series, artists, general, natural_language, warnings.
The first six values must be arrays of strings. Tag order is enforced later as: quality/meta/year/safety, subject count, character, series, @artist, general.
Use lowercase Danbooru/Gelbooru-style tags and spaces instead of underscores; score_1 through score_9 are the only underscore exception. Artist tags begin with @.
Do not blindly add masterpiece, best quality, score_7, or safe. Only include quality, year and safety when evidence supports them.
Anima was trained with tag dropout, so keep useful, non-redundant tags rather than exhaustively listing everything.
For natural language use normal English capitalization. Pure natural language needs at least two sentences. Requested output mode: {requested_mode}.
{profile_rule}
Never claim details that are absent or uncertain. Put uncertainty in warnings."""
    payload = {
        "profile": profile,
        "trigger": trigger,
        "metadata": metadata,
        "wd14": wd,
        "joycaption": joycaption,
        "identity_omit": list(identity_omit),
        "source_artist_names": list(source_artist_names),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
