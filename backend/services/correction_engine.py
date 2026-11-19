import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set


_INSTRUCTION_PREFIX = re.compile(
    r"^(?:avoid\b|do not\b|don't\b|use\s*:|"
    r"add\s+(?:a\s+)?disclaimer\b|required disclaimer\b|"
    r"compliance explanation\b|policy rationale\b)",
    re.IGNORECASE,
)
_LEGACY_CLAIM_ALTERNATIVE = re.compile(
    r"""(?:^|[\s.;])use\s*:\s*(?:"([^"\r\n]+)"|'([^'\r\n]+)'|“([^”\r\n]+)”)""",
    re.IGNORECASE,
)


def validate_replacement_text(
    replacement_text: Any,
    matched_text: Optional[str] = None,
) -> str:
    if not isinstance(replacement_text, str):
        raise ValueError("Replacement text must be a string")
    replacement = replacement_text.strip()
    if not replacement or len(replacement) > 1000:
        raise ValueError("Replacement text must contain 1 to 1000 characters")
    if any(
        unicodedata.category(character) == "Cc" and character not in "\r\n\t"
        for character in replacement
    ):
        raise ValueError("Replacement text contains unsupported control characters")
    if _INSTRUCTION_PREFIX.match(replacement):
        raise ValueError("Replacement text must be literal content, not an instruction")
    if matched_text and not isinstance(matched_text, str):
        raise ValueError("Matched text must be a string")
    if matched_text and replacement.casefold() == matched_text.casefold():
        raise ValueError("Replacement text must differ from the matched text")
    return replacement


def safe_replacement_text(replacement_text: Any, matched_text: Optional[str] = None) -> Optional[str]:
    try:
        return validate_replacement_text(replacement_text, matched_text)
    except ValueError:
        return None


def legacy_terminology_replacement(rule: Dict[str, Any]) -> Optional[str]:
    explicit = rule.get("replacement_text")
    if explicit is not None:
        return explicit
    legacy = rule.get("replacement")
    if not isinstance(legacy, str) or "/" in legacy:
        return None
    return legacy


def legacy_claim_replacement(rule: Dict[str, Any]) -> Optional[str]:
    explicit = rule.get("replacement_text")
    if explicit is not None:
        return explicit
    disclaimer = rule.get("required_disclaimer")
    if not isinstance(disclaimer, str):
        return None
    match = _LEGACY_CLAIM_ALTERNATIVE.search(disclaimer)
    if not match:
        return None
    return next((value for value in match.groups() if value is not None), None)


def _case_adjusted_replacement(matched_text: str, replacement_text: str) -> str:
    if matched_text.isupper():
        return replacement_text.upper()
    if matched_text.istitle() and replacement_text.islower():
        return replacement_text.title()
    return replacement_text


def apply_corrections(
    text: str,
    issues: Iterable[Dict[str, Any]],
    issue_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("Source text must be a string")

    selected_ids: Optional[Set[str]] = set(issue_ids) if issue_ids is not None else None
    unknown_ids = set(selected_ids or ())
    manual_review = {}
    by_span = {}

    for issue in issues:
        issue_id = issue.get("id")
        if issue_id is not None:
            unknown_ids.discard(issue_id)
        selected = selected_ids is None or issue_id in selected_ids

        matched_text = issue.get("matched_text")
        location = issue.get("location")
        if not isinstance(location, dict):
            location = {}
        start, end = location.get("start"), location.get("end")
        replacement_text = issue.get("replacement_text")
        try:
            replacement = validate_replacement_text(replacement_text, matched_text)
        except ValueError:
            replacement = None

        if (
            issue.get("auto_fixable") is not True
            or replacement is None
            or not isinstance(matched_text, str)
            or not matched_text
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(text)
            or text[start:end] != matched_text
        ):
            if selected and issue_id is not None:
                manual_review[issue_id] = "No validated replacement matches the current source span."
            continue

        replacement = _case_adjusted_replacement(matched_text, replacement)
        by_span.setdefault((start, end), []).append({
            "id": issue_id,
            "replacement": replacement,
            "selected": selected,
        })

    for issue_id in unknown_ids:
        manual_review[issue_id] = "Issue is no longer present in the current analysis."

    candidates = []
    for (start, end), span_issues in by_span.items():
        replacements = {item["replacement"] for item in span_issues}
        ids = [item["id"] for item in span_issues if item["id"] is not None]
        selected_ids_for_span = [
            item["id"] for item in span_issues
            if item["selected"] and item["id"] is not None
        ]
        if len(replacements) != 1:
            for issue_id in selected_ids_for_span:
                manual_review[issue_id] = "Conflicting rules propose different replacements for the same span."
            continue
        candidates.append({
            "start": start,
            "end": end,
            "replacement": next(iter(replacements)),
            "ids": ids,
            "selected_ids": selected_ids_for_span,
        })

    overlapping = set()
    for left_index, left in enumerate(candidates):
        for right_index in range(left_index + 1, len(candidates)):
            right = candidates[right_index]
            if left["start"] < right["end"] and right["start"] < left["end"]:
                overlapping.update((left_index, right_index))

    for index in overlapping:
        for issue_id in candidates[index]["selected_ids"]:
            manual_review[issue_id] = "Overlapping correction spans require manual review."

    applicable = [
        candidate for index, candidate in enumerate(candidates)
        if index not in overlapping and candidate["selected_ids"]
    ]
    corrected_text = text
    applied_ids = []
    for candidate in sorted(applicable, key=lambda item: item["start"], reverse=True):
        corrected_text = (
            corrected_text[:candidate["start"]]
            + candidate["replacement"]
            + corrected_text[candidate["end"]:]
        )
        applied_ids.extend(candidate["selected_ids"])

    replacement_locations = []
    offset = 0
    for candidate in sorted(applicable, key=lambda item: item["start"]):
        new_start = candidate["start"] + offset
        replacement_locations.append({
            "start": new_start,
            "end": new_start + len(candidate["replacement"]),
            "replacement_text": candidate["replacement"],
            "issue_ids": candidate["selected_ids"],
        })
        offset += len(candidate["replacement"]) - (candidate["end"] - candidate["start"])

    return {
        "text": corrected_text,
        "applied_issue_ids": applied_ids,
        "manual_review": manual_review,
        "unknown_issue_ids": sorted(unknown_ids),
        "replacement_locations": replacement_locations,
    }
