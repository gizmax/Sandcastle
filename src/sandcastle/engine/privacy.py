"""PII detection and redaction engine (Privacy Router)."""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PIIMatch:
    """A single PII match found in text."""

    entity_type: str  # "email", "phone", "ssn", etc.
    start: int
    end: int
    original: str


@dataclass
class PrivacyConfig:
    """Privacy Router configuration (workflow-level or server-level)."""

    enabled: bool = False
    entities: list[str] = field(
        default_factory=lambda: ["email", "phone", "ssn", "credit_card"]
    )
    mode: str = "redact"  # "redact" | "audit_only"
    apply_to: list[str] = field(default_factory=lambda: ["outputs", "webhooks"])
    replacement_template: str = "[{entity_type}]"


# ---------------------------------------------------------------------------
# Built-in PII patterns
# ---------------------------------------------------------------------------

# Extend from policy.py BUILTIN_PATTERNS with additional PII types.
# All patterns use word-boundary anchors or negative lookahead/lookbehind
# where appropriate to reduce false positives.
PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    ),
    "phone": re.compile(
        # Matches E.164, US, EU-style numbers; requires at least 7 digits total.
        # Negative lookahead/lookbehind to avoid matching plain integers.
        r"(?<!\d)"
        r"(?:\+?1[\s.\-]?)?"
        r"(?:\(?\d{2,4}\)?[\s.\-]?)"
        r"(?:\d{3,4}[\s.\-]?)"
        r"\d{3,4}"
        r"(?!\d)"
    ),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(
        # 13-19 digits, optionally separated by spaces or dashes.
        # Negative lookahead to avoid matching ISBN-style or other numeric strings.
        r"(?<!\d)(?:\d[ \-]*?){13,19}(?!\d)"
    ),
    "ip_address": re.compile(
        r"\b"
        r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
        r"\b"
    ),
    "iban": re.compile(
        # IBAN: 2 letter country code + 2 check digits + 4-30 alphanumeric chars.
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"
    ),
    "date_of_birth": re.compile(
        # DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY formats; only 19xx/20xx years.
        r"\b"
        r"(?:0[1-9]|[12]\d|3[01])"
        r"[/.\-]"
        r"(?:0[1-9]|1[012])"
        r"[/.\-]"
        r"(?:19|20)\d{2}"
        r"\b"
    ),
}


# ---------------------------------------------------------------------------
# PrivacyRouter
# ---------------------------------------------------------------------------


class PrivacyRouter:
    """Detect and redact PII from step outputs and webhook payloads."""

    def __init__(self, config: PrivacyConfig) -> None:
        self.config = config
        # Only compile patterns for requested entity types that exist.
        self._patterns: dict[str, re.Pattern] = {
            k: v for k, v in PII_PATTERNS.items() if k in config.entities
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrub(self, text: str) -> tuple[str, list[PIIMatch]]:
        """Redact PII from a plain string.

        Args:
            text: Input string to inspect.

        Returns:
            Tuple of (scrubbed_text, matches). In audit_only mode the text
            is returned unchanged but matches are still returned.
        """
        if not text or not self._patterns:
            return text, []

        all_matches: list[PIIMatch] = []

        # Collect all matches across all entity types.
        for entity_type, pattern in self._patterns.items():
            for m in pattern.finditer(text):
                all_matches.append(
                    PIIMatch(
                        entity_type=entity_type,
                        start=m.start(),
                        end=m.end(),
                        original=m.group(0),
                    )
                )

        if not all_matches:
            return text, []

        # In audit_only mode: report matches but don't mutate the text.
        if self.config.mode == "audit_only":
            return text, all_matches

        # Sort by start position so we can replace from right to left without
        # invalidating earlier offsets.
        all_matches.sort(key=lambda m: (m.start, m.end))

        # Merge overlapping spans (keep the first entity_type encountered).
        merged: list[PIIMatch] = []
        for match in all_matches:
            if merged and match.start < merged[-1].end:
                # Overlapping - extend the span of the previous match.
                prev = merged[-1]
                if match.end > prev.end:
                    merged[-1] = PIIMatch(
                        entity_type=prev.entity_type,
                        start=prev.start,
                        end=match.end,
                        original=text[prev.start : match.end],
                    )
            else:
                merged.append(match)

        # Replace from right to left to preserve offsets.
        result = text
        for match in reversed(merged):
            replacement = self._format_replacement(match.entity_type)
            result = result[: match.start] + replacement + result[match.end :]

        return result, merged

    def scrub_dict(self, data: Any) -> tuple[Any, list[PIIMatch]]:
        """Recursively scrub PII from all string values in a dict/list/str.

        Args:
            data: Any JSON-serializable value (str, dict, list, int, float, bool, None).

        Returns:
            Tuple of (scrubbed_data, all_matches_found).
        """
        all_matches: list[PIIMatch] = []
        scrubbed = self._scrub_value(data, all_matches)
        return scrubbed, all_matches

    @classmethod
    def from_workflow(
        cls,
        workflow_privacy: dict | None,
        server_config: dict | None,
    ) -> "PrivacyRouter | None":
        """Build a PrivacyRouter by merging workflow-level and server-level config.

        Workflow-level config takes precedence over server-level defaults.
        Returns None if privacy is disabled at both levels.

        Args:
            workflow_privacy: Parsed YAML ``privacy:`` block from the workflow
                (may be None if not defined).
            server_config: Server-level privacy config dict from Settings
                (keys: enabled, entities, apply_to). May be None.
        """
        # Resolve enabled flag: workflow overrides server.
        wf = workflow_privacy or {}
        srv = server_config or {}

        server_enabled = bool(srv.get("enabled", False))
        wf_enabled = wf.get("enabled", None)  # None = not explicitly set

        # If neither level enables privacy, return None.
        if wf_enabled is False or (wf_enabled is None and not server_enabled):
            return None

        # Build merged config: workflow values override server defaults.
        entities_raw = wf.get("entities") or srv.get("entities", ["email", "phone", "ssn", "credit_card"])
        if isinstance(entities_raw, str):
            entities = [e.strip() for e in entities_raw.split(",") if e.strip()]
        else:
            entities = list(entities_raw)

        apply_to_raw = wf.get("apply_to") or srv.get("apply_to", ["outputs", "webhooks"])
        if isinstance(apply_to_raw, str):
            apply_to = [a.strip() for a in apply_to_raw.split(",") if a.strip()]
        else:
            apply_to = list(apply_to_raw)

        config = PrivacyConfig(
            enabled=True,
            entities=entities,
            mode=wf.get("mode") or srv.get("mode", "redact"),
            apply_to=apply_to,
            replacement_template=wf.get("replacement_template")
            or srv.get("replacement_template", "[{entity_type}]"),
        )

        # Validate entity types - warn and skip unknown ones.
        known = set(PII_PATTERNS.keys())
        valid_entities = []
        for e in config.entities:
            if e in known:
                valid_entities.append(e)
            else:
                logger.warning(
                    "PrivacyRouter: unknown entity type '%s' - skipping. "
                    "Known types: %s",
                    e,
                    ", ".join(sorted(known)),
                )
        config.entities = valid_entities

        if not config.entities:
            logger.warning(
                "PrivacyRouter: no valid entity types configured - privacy router disabled"
            )
            return None

        return cls(config)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format_replacement(self, entity_type: str) -> str:
        """Format the replacement string for a given entity type."""
        return self.config.replacement_template.format(entity_type=entity_type.upper())

    def _scrub_value(self, value: Any, matches: list[PIIMatch]) -> Any:
        """Recursively scrub a value, collecting matches in-place."""
        if isinstance(value, str):
            scrubbed, found = self.scrub(value)
            matches.extend(found)
            return scrubbed
        if isinstance(value, dict):
            return {k: self._scrub_value(v, matches) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub_value(item, matches) for item in value]
        # int, float, bool, None - pass through unchanged.
        return copy.copy(value)
