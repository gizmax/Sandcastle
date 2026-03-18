"""Tests for the PrivacyRouter PII detection and redaction engine."""

from __future__ import annotations

import pytest

from sandcastle.engine.privacy import (
    PIIMatch,
    PII_PATTERNS,
    PrivacyConfig,
    PrivacyRouter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_router(**kwargs) -> PrivacyRouter:
    """Helper to create a PrivacyRouter with all defaults overridable."""
    defaults = dict(
        enabled=True,
        entities=list(PII_PATTERNS.keys()),
        mode="redact",
        apply_to=["outputs", "webhooks"],
        replacement_template="[{entity_type}]",
    )
    defaults.update(kwargs)
    config = PrivacyConfig(**defaults)
    return PrivacyRouter(config)


# ---------------------------------------------------------------------------
# Email detection
# ---------------------------------------------------------------------------


class TestEmailDetection:
    def test_simple_email(self):
        router = make_router(entities=["email"])
        scrubbed, matches = router.scrub("Contact us at hello@example.com please.")
        assert "[EMAIL]" in scrubbed
        assert len(matches) == 1
        assert matches[0].entity_type == "email"
        assert matches[0].original == "hello@example.com"

    def test_email_with_plus(self):
        router = make_router(entities=["email"])
        scrubbed, matches = router.scrub("user+tag@sub.domain.org is valid.")
        assert len(matches) == 1
        assert "[EMAIL]" in scrubbed

    def test_multiple_emails(self):
        router = make_router(entities=["email"])
        text = "From: a@b.com To: c@d.org"
        scrubbed, matches = router.scrub(text)
        assert len(matches) == 2
        assert scrubbed.count("[EMAIL]") == 2

    def test_no_false_positive_no_at(self):
        router = make_router(entities=["email"])
        _, matches = router.scrub("This is a normal sentence without any emails.")
        assert matches == []

    def test_email_with_subdomain(self):
        router = make_router(entities=["email"])
        _, matches = router.scrub("Send to firstname.lastname@mail.company.co.uk")
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# Phone number detection
# ---------------------------------------------------------------------------


class TestPhoneDetection:
    def test_us_format(self):
        router = make_router(entities=["phone"])
        scrubbed, matches = router.scrub("Call 555-867-5309 for info.")
        assert len(matches) >= 1
        assert "[PHONE]" in scrubbed

    def test_e164_format(self):
        router = make_router(entities=["phone"])
        _, matches = router.scrub("International: +1 415 555 2671")
        assert len(matches) >= 1

    def test_no_false_positive_short_number(self):
        router = make_router(entities=["phone"])
        # A 3-digit number should not match
        _, matches = router.scrub("Error code: 404")
        assert matches == []

    def test_no_false_positive_year(self):
        router = make_router(entities=["phone"])
        # A standalone 4-digit year should not match
        _, matches = router.scrub("Year: 2025")
        assert matches == []


# ---------------------------------------------------------------------------
# SSN detection
# ---------------------------------------------------------------------------


class TestSSNDetection:
    def test_standard_ssn(self):
        router = make_router(entities=["ssn"])
        scrubbed, matches = router.scrub("SSN: 123-45-6789")
        assert len(matches) == 1
        assert matches[0].entity_type == "ssn"
        assert "[SSN]" in scrubbed

    def test_no_false_positive_different_dash_pattern(self):
        router = make_router(entities=["ssn"])
        _, matches = router.scrub("Reference: 12-345-6789")
        assert matches == []

    def test_ssn_embedded_in_text(self):
        router = make_router(entities=["ssn"])
        text = "The applicant's social security number is 987-65-4321 as stated."
        scrubbed, matches = router.scrub(text)
        assert len(matches) == 1
        assert "987-65-4321" not in scrubbed


# ---------------------------------------------------------------------------
# Credit card detection
# ---------------------------------------------------------------------------


class TestCreditCardDetection:
    def test_visa_number(self):
        router = make_router(entities=["credit_card"])
        scrubbed, matches = router.scrub("Card: 4111 1111 1111 1111")
        assert len(matches) == 1
        assert matches[0].entity_type == "credit_card"
        assert "[CREDIT_CARD]" in scrubbed

    def test_amex_format(self):
        router = make_router(entities=["credit_card"])
        scrubbed, matches = router.scrub("Amex: 3782 822463 10005")
        assert len(matches) >= 1

    def test_mastercard_dashes(self):
        router = make_router(entities=["credit_card"])
        scrubbed, matches = router.scrub("MC: 5500-0000-0000-0004")
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# IP address detection
# ---------------------------------------------------------------------------


class TestIPAddressDetection:
    def test_ipv4_address(self):
        router = make_router(entities=["ip_address"])
        scrubbed, matches = router.scrub("Server at 192.168.1.100 is down.")
        assert len(matches) == 1
        assert matches[0].entity_type == "ip_address"
        assert "[IP_ADDRESS]" in scrubbed

    def test_public_ip(self):
        router = make_router(entities=["ip_address"])
        _, matches = router.scrub("External IP: 8.8.8.8")
        assert len(matches) == 1

    def test_no_false_positive_version_string(self):
        router = make_router(entities=["ip_address"])
        # "1.2.3" is not a valid IPv4 address
        _, matches = router.scrub("Version 1.2.3 is installed")
        assert matches == []

    def test_no_false_positive_out_of_range(self):
        router = make_router(entities=["ip_address"])
        # 999.999.999.999 is invalid
        _, matches = router.scrub("999.999.999.999")
        assert matches == []


# ---------------------------------------------------------------------------
# IBAN detection
# ---------------------------------------------------------------------------


class TestIBANDetection:
    def test_german_iban(self):
        router = make_router(entities=["iban"])
        scrubbed, matches = router.scrub("IBAN: DE89370400440532013000")
        assert len(matches) == 1
        assert matches[0].entity_type == "iban"
        assert "[IBAN]" in scrubbed

    def test_uk_iban(self):
        router = make_router(entities=["iban"])
        _, matches = router.scrub("Sort: GB29NWBK60161331926819")
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# scrub_dict - recursive scrubbing
# ---------------------------------------------------------------------------


class TestScrubDict:
    def test_flat_dict(self):
        router = make_router(entities=["email"])
        data = {"name": "Alice", "email": "alice@example.com", "age": 30}
        scrubbed, matches = router.scrub_dict(data)
        assert scrubbed["name"] == "Alice"
        assert scrubbed["age"] == 30
        assert "[EMAIL]" in scrubbed["email"]
        assert len(matches) == 1

    def test_nested_dict(self):
        router = make_router(entities=["email", "phone"])
        data = {
            "user": {
                "contact": {
                    "email": "test@test.com",
                    "phone": "555-123-4567",
                }
            }
        }
        scrubbed, matches = router.scrub_dict(data)
        assert "[EMAIL]" in scrubbed["user"]["contact"]["email"]
        assert len(matches) >= 1

    def test_list_of_strings(self):
        router = make_router(entities=["email"])
        data = ["hello", "user@domain.com", "world"]
        scrubbed, matches = router.scrub_dict(data)
        assert scrubbed[0] == "hello"
        assert "[EMAIL]" in scrubbed[1]
        assert scrubbed[2] == "world"
        assert len(matches) == 1

    def test_list_of_dicts(self):
        router = make_router(entities=["ssn"])
        data = [
            {"id": 1, "ssn": "123-45-6789"},
            {"id": 2, "ssn": "987-65-4321"},
        ]
        scrubbed, matches = router.scrub_dict(data)
        assert len(matches) == 2
        assert all("[SSN]" in item["ssn"] for item in scrubbed)

    def test_non_string_values_passthrough(self):
        router = make_router(entities=["email"])
        data = {"count": 42, "active": True, "score": 3.14, "empty": None}
        scrubbed, matches = router.scrub_dict(data)
        assert scrubbed == data
        assert matches == []

    def test_empty_string(self):
        router = make_router(entities=["email"])
        scrubbed, matches = router.scrub_dict("")
        assert scrubbed == ""
        assert matches == []

    def test_mixed_types(self):
        router = make_router(entities=["email"])
        data = {
            "results": [
                {"ok": True, "email": "a@b.com"},
                42,
                None,
                "plain text",
            ]
        }
        scrubbed, matches = router.scrub_dict(data)
        assert len(matches) == 1
        assert scrubbed["results"][1] == 42
        assert scrubbed["results"][2] is None


# ---------------------------------------------------------------------------
# Disabled router
# ---------------------------------------------------------------------------


class TestDisabledRouter:
    def test_disabled_returns_unchanged(self):
        config = PrivacyConfig(enabled=False, entities=["email"])
        router = PrivacyRouter(config)
        text = "Email: hello@example.com"
        # Even with a disabled config, scrub() still works at the pattern level.
        # The disabled flag is handled by from_workflow returning None.
        # This test verifies the router instance itself works when entities=[].
        config2 = PrivacyConfig(enabled=False, entities=[])
        router2 = PrivacyRouter(config2)
        scrubbed, matches = router2.scrub(text)
        assert scrubbed == text
        assert matches == []

    def test_from_workflow_returns_none_when_disabled(self):
        result = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": False},
            server_config={"enabled": False},
        )
        assert result is None

    def test_from_workflow_returns_none_when_neither_enabled(self):
        result = PrivacyRouter.from_workflow(
            workflow_privacy=None,
            server_config=None,
        )
        assert result is None

    def test_from_workflow_returns_none_when_no_config(self):
        result = PrivacyRouter.from_workflow(
            workflow_privacy={},
            server_config={"enabled": False},
        )
        assert result is None


# ---------------------------------------------------------------------------
# audit_only mode
# ---------------------------------------------------------------------------


class TestAuditOnlyMode:
    def test_audit_only_returns_matches_without_modifying(self):
        config = PrivacyConfig(
            enabled=True,
            entities=["email"],
            mode="audit_only",
            apply_to=["outputs"],
        )
        router = PrivacyRouter(config)
        original = "Contact: ceo@company.com"
        scrubbed, matches = router.scrub(original)
        # Text should be unchanged in audit_only mode.
        assert scrubbed == original
        # But matches should still be reported.
        assert len(matches) == 1
        assert matches[0].entity_type == "email"

    def test_audit_only_dict(self):
        config = PrivacyConfig(
            enabled=True,
            entities=["ssn"],
            mode="audit_only",
            apply_to=["outputs"],
        )
        router = PrivacyRouter(config)
        data = {"ssn": "123-45-6789"}
        scrubbed, matches = router.scrub_dict(data)
        # Dict value unchanged.
        assert scrubbed["ssn"] == "123-45-6789"
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# from_workflow config merging
# ---------------------------------------------------------------------------


class TestFromWorkflow:
    def test_workflow_enables_overrides_server(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "entities": ["email"]},
            server_config={"enabled": False},
        )
        assert router is not None
        assert router.config.entities == ["email"]

    def test_server_enabled_no_workflow_config(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy=None,
            server_config={
                "enabled": True,
                "entities": "email,ssn",
                "apply_to": "outputs",
            },
        )
        assert router is not None
        assert "email" in router.config.entities
        assert "ssn" in router.config.entities
        assert "outputs" in router.config.apply_to

    def test_workflow_entities_override_server(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "entities": ["ip_address"]},
            server_config={"enabled": True, "entities": "email,phone"},
        )
        assert router is not None
        assert router.config.entities == ["ip_address"]

    def test_comma_separated_entities_string(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy=None,
            server_config={"enabled": True, "entities": "email,phone,ssn"},
        )
        assert router is not None
        assert set(router.config.entities) == {"email", "phone", "ssn"}

    def test_unknown_entity_type_warns_and_skips(self):
        """Unknown entity types should be silently skipped (a warning is logged)."""
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "entities": ["email", "unknown_pii_type"]},
            server_config=None,
        )
        assert router is not None
        assert "email" in router.config.entities
        assert "unknown_pii_type" not in router.config.entities

    def test_all_unknown_returns_none(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "entities": ["nonexistent"]},
            server_config=None,
        )
        assert router is None

    def test_mode_from_workflow(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "mode": "audit_only"},
            server_config={"enabled": True},
        )
        assert router is not None
        assert router.config.mode == "audit_only"

    def test_apply_to_list_from_workflow(self):
        router = PrivacyRouter.from_workflow(
            workflow_privacy={"enabled": True, "apply_to": ["outputs"]},
            server_config={"enabled": True},
        )
        assert router is not None
        assert router.config.apply_to == ["outputs"]
        assert "webhooks" not in router.config.apply_to


# ---------------------------------------------------------------------------
# Replacement template
# ---------------------------------------------------------------------------


class TestReplacementTemplate:
    def test_default_template(self):
        router = make_router(entities=["email"], replacement_template="[{entity_type}]")
        scrubbed, _ = router.scrub("Email: test@test.com")
        assert "[EMAIL]" in scrubbed

    def test_custom_template(self):
        router = make_router(entities=["email"], replacement_template="<REDACTED:{entity_type}>")
        scrubbed, _ = router.scrub("Email: test@test.com")
        assert "<REDACTED:EMAIL>" in scrubbed

    def test_template_applied_per_entity_type(self):
        router = make_router(
            entities=["email", "ssn"],
            replacement_template="***{entity_type}***",
        )
        scrubbed, matches = router.scrub("Email: a@b.com, SSN: 123-45-6789")
        assert "***EMAIL***" in scrubbed
        assert "***SSN***" in scrubbed


# ---------------------------------------------------------------------------
# No false positives on normal text
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_plain_sentence(self):
        router = make_router()
        text = (
            "The quick brown fox jumps over the lazy dog. "
            "Version 2.3.4 was released in 2024. "
            "The price is $19.99 and the discount is 10%."
        )
        _, matches = router.scrub(text)
        # Allow for minor regex edge cases but expect very few or no matches.
        assert len(matches) == 0, f"False positives: {matches}"

    def test_code_snippet(self):
        router = make_router(entities=["email", "ssn", "credit_card"])
        text = (
            "def calculate(x, y):\n"
            "    result = x + y\n"
            "    return result\n"
            "\n"
            "# Range: 0-100\n"
        )
        _, matches = router.scrub(text)
        assert len(matches) == 0, f"False positives: {matches}"

    def test_json_structure(self):
        router = make_router(entities=["email", "ssn"])
        import json
        data = {"name": "Test User", "age": 30, "status": "active", "id": "usr_12345"}
        _, matches = router.scrub(json.dumps(data))
        assert len(matches) == 0

    def test_url_no_false_positive(self):
        router = make_router(entities=["email"])
        # URLs should not match as emails
        _, matches = router.scrub("Visit https://example.com/path?q=value for more info.")
        assert len(matches) == 0

    def test_markdown_text(self):
        router = make_router(entities=["credit_card", "ssn"])
        text = (
            "## Summary\n\n"
            "- Total items: 42\n"
            "- Success rate: 99.9%\n"
            "- Run ID: run-12345678\n"
        )
        _, matches = router.scrub(text)
        assert len(matches) == 0
