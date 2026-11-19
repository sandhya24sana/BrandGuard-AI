import re
from typing import List, Dict, Any, Tuple
from services.correction_engine import (
    apply_corrections,
    legacy_claim_replacement,
    legacy_terminology_replacement,
    safe_replacement_text,
)


def validate_claim_pattern(pattern: str) -> None:
    if len(pattern) > 128:
        raise ValueError("Claim patterns must be 128 characters or fewer")
    if (
        re.search(r"\([^)]*[+*{][^)]*\)[+*{]", pattern)
        or re.search(r"\([^)]*\|[^)]*\)[+*{]", pattern)
        or len(re.findall(r"\.\*[+*]|\.\+[+*]", pattern)) > 1
    ):
        raise ValueError("Claim pattern contains an unsafe nested or ambiguous quantifier")
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"Invalid claim pattern: {error}") from error


class RuleBasedAnalyzer:
    """
    Deterministic rule-based brand governance engine.
    Detects terminology issues, fact inaccuracies, tone mismatches,
    unsupported claims, and readability/accessibility problems.
    Operates offline without requiring external AI keys.
    """

    def __init__(self, brand: Dict[str, Any], facts: List[Dict[str, Any]], 
                 terminology: List[Dict[str, Any]], visual_rules: Dict[str, Any] = None,
                 claim_rules: List[Dict[str, Any]] = None):
        self.brand = brand
        self.facts = facts or []
        self.terminology = terminology or []
        self.visual_rules = visual_rules or {}
        self.claim_rules = claim_rules or []

    def analyze(self, text: str, content_type: str = "Ad Copy", channel: str = "Digital") -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []

        # 1. Terminology Checks
        term_issues, term_penalty = self._check_terminology(text)
        issues.extend(term_issues)

        # 2. Fact Accuracy Checks
        fact_issues, fact_penalty = self._check_facts(text)
        issues.extend(fact_issues)

        # 3. Tone & Style Checks
        tone_issues, tone_penalty = self._check_tone(text, content_type)
        issues.extend(tone_issues)

        # 4. Claim & Compliance Checks
        claim_issues, claim_penalty = self._check_claims(text)
        issues.extend(claim_issues)

        # 5. Accessibility & Readability Checks
        access_issues, access_penalty, readability_stats = self._check_accessibility(text)
        issues.extend(access_issues)

        # 6. Calculate Sub-scores (0-100)
        score_terminology = max(0, 100 - term_penalty)
        score_facts = max(0, 100 - fact_penalty)
        score_tone = max(0, 100 - tone_penalty)
        score_claims = max(0, 100 - claim_penalty)
        score_accessibility = max(0, 100 - access_penalty)
        category_scores = {
            "Terminology": "terminology",
            "Fact Accuracy": "facts",
            "Voice & Tone": "tone",
            "Compliance & Claims": "claims",
            "Accessibility & Clarity": "accessibility"
        }
        sub_scores = {
            "terminology": score_terminology,
            "facts": score_facts,
            "tone": score_tone,
            "claims": score_claims,
            "accessibility": score_accessibility
        }
        for issue in issues:
            category_score = category_scores.get(issue.get("category"))
            if not category_score:
                continue
            ceiling = 49 if issue.get("severity", "").upper() == "CRITICAL" else 74
            if issue.get("severity", "").upper() in ("CRITICAL", "WARNING"):
                sub_scores[category_score] = min(sub_scores[category_score], ceiling)

        # Overall Weighted Compliance Score (0-100)
        overall_score = round(
            (sub_scores["facts"] * 0.30) +
            (sub_scores["claims"] * 0.25) +
            (sub_scores["terminology"] * 0.25) +
            (sub_scores["tone"] * 0.10) +
            (sub_scores["accessibility"] * 0.10)
        )
        overall_score = max(5, min(100, overall_score))

        # A critical issue cannot be hidden behind a high weighted average.
        severities = {issue.get("severity", "").upper() for issue in issues}
        if "CRITICAL" in severities:
            overall_score = min(overall_score, 49)
        elif "WARNING" in severities:
            overall_score = min(overall_score, 89)

        # Risk Level
        if overall_score >= 90:
            risk_level = "LOW"
            risk_badge = "Low Detected Risk - Human Review Required"
        elif overall_score >= 75:
            risk_level = "MODERATE"
            risk_badge = "Moderate Risk - Minor Revisions Needed"
        elif overall_score >= 50:
            risk_level = "HIGH"
            risk_badge = "High Risk - Substantial Issues Detected"
        else:
            risk_level = "CRITICAL"
            risk_badge = "Critical Risk - Immediate Remediation Required"

        # Generate rule-based corrected text
        corrected_text = self._generate_corrected_text(text, issues)

        summary = self._generate_summary(overall_score, risk_level, issues)

        return {
            "score": overall_score,
            "risk_level": risk_level,
            "risk_badge": risk_badge,
            "sub_scores": sub_scores,
            "readability": readability_stats,
            "issues": issues,
            "summary": summary,
            "corrected_text": corrected_text,
            "engine_used": "RULE_BASED_FALLBACK"
        }

    def _check_terminology(self, text: str) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        for rule_index, item in enumerate(self.terminology):
            if not item.get("active", 1):
                continue
            rule_id = item.get("id")
            if rule_id is None:
                rule_id = rule_index
            term = item.get("term", "").strip()
            if not term:
                continue

            pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
            matches = list(pattern.finditer(text))
            replacement_text = legacy_terminology_replacement(item)

            for match in matches:
                matched_snippet = match.group(0)
                start, end = match.span()
                severity = (item.get("severity") or "warning").lower()
                replacement = safe_replacement_text(replacement_text, matched_snippet)

                if severity == "critical":
                    penalty += 20
                elif severity == "warning":
                    penalty += 12
                else:
                    penalty += 6

                issues.append({
                    "id": f"term_{rule_id}_{start}_{end}",
                    "category": "Terminology",
                    "severity": severity.upper(),
                    "rule": f"Prohibited Term: '{term}'",
                    "matched_text": matched_snippet,
                    "location": {"start": start, "end": end},
                    "explanation": item.get("reason") or f"'{term}' violates the {self.brand.get('name', 'company')} vocabulary standard.",
                    "suggestion": replacement or "",
                    "replacement_text": replacement,
                    "auto_fixable": replacement is not None,
                    "manual_review_required": replacement is None,
                    "alternatives": [replacement] if replacement else []
                })

        return issues, min(100, penalty)

    def _check_facts(self, text: str) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0
        sentences = [
            (match.group(0), match.start())
            for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text)
        ]
        number_pattern = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\+?\b")

        for fact in self.facts:
            statement = (fact.get("statement") or "").strip()
            key_metric = (fact.get("key_metric") or "").strip()
            keywords = [
                keyword.strip().lower()
                for keyword in (fact.get("keywords") or "").split(",")
                if keyword.strip()
            ]
            approved_values = {}
            for approved_match in number_pattern.finditer(f"{statement} {key_metric}"):
                value = approved_match.group(0)
                approved_values.setdefault(value.replace(",", "").rstrip("%+"), value)
            approved_numbers = set(approved_values)
            if not statement or not keywords or not approved_numbers:
                continue

            for sentence, offset in sentences:
                lowered_sentence = sentence.lower()
                if not any(keyword in lowered_sentence for keyword in keywords):
                    continue
                stated_matches = list(number_pattern.finditer(sentence))
                normalized_stated = {
                    match.group(0).replace(",", "").rstrip("%+")
                    for match in stated_matches
                }
                if not normalized_stated or normalized_stated.issubset(approved_numbers):
                    continue

                conflicting_matches = [
                    match for match in stated_matches
                    if match.group(0).replace(",", "").rstrip("%+") not in approved_numbers
                ]
                if not conflicting_matches:
                    continue
                match = conflicting_matches[0]
                start, end = offset + match.start(), offset + match.end()
                replacement = next(iter(approved_values.values())) if len(approved_values) == 1 else ""
                replacement = safe_replacement_text(replacement, match.group(0))
                penalty += 25
                issues.append({
                    "id": f"fact_{fact.get('id', len(issues))}_{start}",
                    "category": "Fact Accuracy",
                    "severity": "WARNING",
                    "rule": f"Approved Fact: {fact.get('category') or 'Brand fact'}",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": "This numeric claim conflicts with the approved fact configured for this brand.",
                    "suggestion": replacement or "",
                    "replacement_text": replacement,
                    "approved_fact": statement,
                    "auto_fixable": replacement is not None,
                    "manual_review_required": replacement is None
                })
                break

        return issues, min(100, penalty)

    def _check_tone(self, text: str, content_type: str) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        tone_profile = (self.brand.get("primary_tone") or "").lower()
        voice_traits = self.brand.get("voice_traits") or []
        if isinstance(voice_traits, str):
            try:
                import json
                voice_traits = json.loads(voice_traits)
            except json.JSONDecodeError:
                voice_traits = []
        if isinstance(voice_traits, list):
            tone_profile += " " + " ".join(
                trait.lower() for trait in voice_traits if isinstance(trait, str)
            )

        slang_patterns = []
        if any(term in tone_profile for term in ("formal", "professional", "corporate", "technical")):
            slang_patterns.extend([
                (r"\b(bruh|tbh|cray|cray-cray|heck of a|kick-ass|freaking|omg|y'all|lit|super hyped)\b",
                 "Informal slang does not match the brand's configured voice.", "WARNING"),
                (r"\b(insane|crazy|ridiculous)\s+(speed|performance|value|deal|pricing)\b",
                 "Hyperbolic phrasing does not match the brand's configured voice.", "WARNING"),
                (r"\b(game[- ]changer|next-gen synergy|silver bullet|paradigm shift)\b",
                 "This buzzword may not match the brand's configured voice.", "INFO")
            ])
        slang_patterns.append(
            (r"\b(act now|hurry before it's gone|limited time offer|buy now or regret it)\b",
             "Aggressive pressure language may require brand or legal review.", "WARNING")
        )

        for pat, reason, sev in slang_patterns:
            for match in re.finditer(pat, text, re.IGNORECASE):
                start, end = match.span()
                penalty += 15 if sev == "CRITICAL" else 8
                issues.append({
                    "id": f"tone_{start}_{end}",
                    "category": "Voice & Tone",
                    "severity": sev,
                    "rule": "Brand Tone & Formality Standards",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": reason,
                    "suggestion": "Rephrase with professional, objective language.",
                    "replacement_text": None,
                    "auto_fixable": False,
                    "manual_review_required": True
                })

        # Excessive exclamation marks (e.g. "!!!")
        exclamation_matches = list(re.finditer(r"!{2,}", text))
        if exclamation_matches:
            penalty += 10
            for match in exclamation_matches:
                start, end = match.span()
                issues.append({
                    "id": f"tone_excl_{start}",
                    "category": "Voice & Tone",
                    "severity": "WARNING",
                    "rule": "Punctuation & Professional Restraint",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": "Multiple exclamation marks appear spammy and violate enterprise copy standards.",
                    "suggestion": ".",
                    "replacement_text": ".",
                    "auto_fixable": True
                })

        # Shouting ALL CAPS words (3 or more consecutive capitalized words of length > 2)
        all_caps_match = re.search(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,}){2,}\b", text)
        if all_caps_match:
            penalty += 15
            start, end = all_caps_match.span()
            issues.append({
                "id": f"tone_caps_{start}",
                "category": "Voice & Tone",
                "severity": "WARNING",
                "rule": "Capitalization & Typography",
                "matched_text": all_caps_match.group(0),
                "location": {"start": start, "end": end},
                "explanation": "All-caps text reads as shouting and damages brand credibility.",
                "suggestion": all_caps_match.group(0).capitalize(),
                "replacement_text": all_caps_match.group(0).capitalize(),
                "auto_fixable": True
            })

        return issues, min(100, penalty)

    def _check_claims(self, text: str) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        # Custom configured claim rules
        for rule_index, rule in enumerate(self.claim_rules):
            if not rule.get("active", 1):
                continue
            rule_id = rule.get("id")
            if rule_id is None:
                rule_id = rule_index
            pattern_str = rule.get("pattern_or_trigger", "")
            if not pattern_str:
                continue

            try:
                for match in re.finditer(pattern_str, text, re.IGNORECASE):
                    start, end = match.span()
                    sev = (rule.get("severity") or "critical").upper()
                    replacement = safe_replacement_text(
                        legacy_claim_replacement(rule), match.group(0)
                    )
                    penalty += 30 if sev == "CRITICAL" else 15
                    issues.append({
                        "id": f"claim_{rule_id}_{start}_{end}",
                        "category": "Compliance & Claims",
                        "severity": sev,
                        "rule": rule.get("rule_name", "Regulatory Compliance"),
                        "matched_text": match.group(0),
                        "location": {"start": start, "end": end},
                        "explanation": rule.get("explanation") or "Unsupported absolute claim.",
                        "suggestion": replacement or "",
                        "replacement_text": replacement,
                        "required_disclaimer": rule.get("required_disclaimer") or "",
                        "auto_fixable": replacement is not None,
                        "manual_review_required": replacement is None
                    })
            except re.error as error:
                raise ValueError(f"Invalid saved claim pattern in rule '{rule.get('rule_name', '')}': {error}") from error

        # Common absolute warranties
        unsupported_patterns = [
            (r"\b100%\s+(?:guaranteed|risk-free|money-back|secure)\b", "Absolute warranties create strict legal liability.", "CRITICAL", "backed by enterprise SLA and satisfaction guarantees"),
            (r"\b(the\s+only\s+cloud\s+provider|unmatched\s+by\s+anyone|the\s+world's\s+best)\b", "Unsubstantiated superlative without documented third-party ranking.", "WARNING", "a premier enterprise cloud platform"),
            (r"\b(unlimited\s+free\s+tier|free\s+forever\s+with\s+no\s+limits)\b", "Misleading claim; all developer tiers carry documented fair-use caps.", "CRITICAL", "generous developer tier with transparent quotas")
        ]

        for pattern_index, (pat, expl, sev, sugg) in enumerate(unsupported_patterns):
            for match in re.finditer(pat, text, re.IGNORECASE):
                # Avoid duplicate if already caught by custom rule
                start, end = match.span()
                if any(
                    iss["location"] == {"start": start, "end": end}
                    for iss in issues
                    if iss["category"] == "Compliance & Claims"
                ):
                    continue
                penalty += 25 if sev == "CRITICAL" else 12
                replacement = safe_replacement_text(sugg, match.group(0))
                issues.append({
                    "id": f"claim_gen_{pattern_index}_{start}_{end}",
                    "category": "Compliance & Claims",
                    "severity": sev,
                    "rule": "Substantiation & Advertising Truth Standard",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": expl,
                    "suggestion": replacement or "",
                    "replacement_text": replacement,
                    "auto_fixable": replacement is not None,
                    "manual_review_required": replacement is None
                })

        return issues, min(100, penalty)

    def _check_accessibility(self, text: str) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
        issues = []
        penalty = 0

        sentence_spans = []
        for match in re.finditer(r"[^.!?]+", text):
            raw_sentence = match.group(0)
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            start = match.start() + len(raw_sentence) - len(raw_sentence.lstrip())
            sentence_spans.append((sentence, start, start + len(sentence)))
        sentences = [sentence for sentence, _, _ in sentence_spans]
        words = re.findall(r"\b[A-Za-z0-9'-]+\b", text)

        word_count = len(words)
        sentence_count = max(1, len(sentences))
        
        # Count syllables (approximate)
        def count_syllables(w):
            w = w.lower()
            if len(w) <= 3:
                return 1
            w = re.sub(r"(?:[^laeiouy]|ed|es|e)$", "", w)
            w = re.sub(r"^y", "", w)
            syl = len(re.findall(r"[aeiouy]{1,2}", w))
            return max(1, syl)

        total_syllables = sum(count_syllables(w) for w in words) if words else 0
        
        # Flesch Reading Ease
        # 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)
        words_per_sentence = word_count / sentence_count if sentence_count > 0 else 0
        syllables_per_word = total_syllables / word_count if word_count > 0 else 0
        
        reading_ease = 206.835 - (1.015 * words_per_sentence) - (84.6 * syllables_per_word)
        reading_ease = round(max(0, min(100, reading_ease)), 1)

        # Flesch-Kincaid Grade Level
        # 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59
        grade_level = (0.39 * words_per_sentence) + (11.8 * syllables_per_word) - 15.59
        grade_level = round(max(1, grade_level), 1)

        # 1. Overly long sentences (> 32 words)
        for s, start, end in sentence_spans:
            s_words = re.findall(r"\b[A-Za-z0-9'-]+\b", s)
            if len(s_words) > 32:
                penalty += 10
                issues.append({
                    "id": f"access_long_sent_{start}_{end}",
                    "category": "Accessibility & Clarity",
                    "severity": "WARNING",
                    "rule": "Sentence Length & Cognitive Load",
                    "matched_text": text[start:end],
                    "location": {"start": start, "end": end},
                    "explanation": f"Sentence contains {len(s_words)} words. Split sentences into clearer 15-20 word statements for cognitive accessibility.",
                    "suggestion": "Break sentence into two shorter, active statements.",
                    "auto_fixable": False
                })

        # 2. Non-descriptive links ("click here", "click this")
        click_matches = list(re.finditer(r"\b(click\s+here|click\s+this\s+link|tap\s+here)\b", text, re.IGNORECASE))
        for match in click_matches:
            penalty += 8
            start, end = match.span()
            issues.append({
                "id": f"access_link_{start}",
                "category": "Accessibility & Clarity",
                "severity": "INFO",
                "rule": "WCAG Accessible Hyperlink Context",
                "matched_text": match.group(0),
                "location": {"start": start, "end": end},
                "explanation": "'Click here' lacks semantic context for screen-reader users. Use descriptive action verbs like 'Explore the platform' or 'Download benchmark report'.",
                "suggestion": "Use a descriptive link label based on the link destination.",
                "replacement_text": None,
                "auto_fixable": False,
                "manual_review_required": True
            })

        readability_stats = {
            "word_count": word_count,
            "sentence_count": sentence_count,
            "reading_ease": reading_ease,
            "grade_level": grade_level,
            "reading_time_sec": round(word_count / 3.3) # avg 200 wpm
        }

        return issues, min(100, penalty), readability_stats

    def _generate_corrected_text(self, text: str, issues: List[Dict[str, Any]]) -> str:
        """Build a preview using only validated, exact-span replacements."""
        correction_plan = apply_corrections(text, issues)
        for issue in issues:
            reason = correction_plan["manual_review"].get(issue.get("id"))
            if reason:
                issue["auto_fixable"] = False
                issue["manual_review_required"] = True
                issue["manual_review_reason"] = reason
        return correction_plan["text"]

    def _generate_summary(self, score: int, risk_level: str, issues: List[Dict[str, Any]]) -> str:
        crit_count = sum(1 for i in issues if i["severity"] == "CRITICAL")
        warn_count = sum(1 for i in issues if i["severity"] == "WARNING")
        info_count = sum(1 for i in issues if i["severity"] == "INFO")

        if crit_count:
            assessment = "Critical policy violations were detected."
        elif warn_count:
            assessment = "Policy warnings were detected."
        elif info_count:
            assessment = "Only informational observations were detected."
        else:
            assessment = "No configured policy violations were detected."
        return (
            f"{assessment} Score: {score}/100 ({risk_level} risk); "
            f"{crit_count} critical, {warn_count} warning, and {info_count} informational issue(s). "
            "This automated result is not an approval; human review is required before publication."
        )
