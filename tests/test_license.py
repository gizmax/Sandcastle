"""Tests for the license key validation module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sandcastle.engine.license import (
    COMMUNITY_MODE,
    LicenseInfo,
    LicenseStatus,
    LicenseTier,
    get_license,
    reset_cache,
    validate_license_key,
)

# -- Test keys generated with the matching private key -----------------------

# Valid pro key (expires 2099-12-31)
_PRO_KEY = "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJUZXN0IENvcnAiLCJtYXhfc2VhdHMiOjEwLCJleHAiOiIyMDk5LTEyLTMxIiwiaWF0IjoiMjAyNi0wMi0yNiIsImlkIjoibGljX3Rlc3QwMDAxIn0.TuAmX7VJ33zEfIY-NLStTr2ADUIHjeWyewCVDqvpaeRluNTePGaYbMC8TE6S5FiyY_0-xNJu0AA7woYFHUEiBA"

# Expired key (expired 2020-01-01)
_EXPIRED_KEY = "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJFeHBpcmVkIENvcnAiLCJtYXhfc2VhdHMiOjUsImV4cCI6IjIwMjAtMDEtMDEiLCJpYXQiOiIyMDE5LTAxLTAxIiwiaWQiOiJsaWNfdGVzdDAwMDIifQ.aZK91wxVzqS8EMFW62TxRQPkG6r5yIMfplL9PrZiFYWYDU2vKxqc-VE2bRolF6GEyT00RoI2fA6mMcWuKpdlCg"

# Enterprise key (expires 2099-12-31)
_ENTERPRISE_KEY = "sc_lic_eyJ2IjoxLCJ0aWVyIjoiZW50ZXJwcmlzZSIsImxpY2Vuc2VlIjoiRW50ZXJwcmlzZSBJbmMiLCJtYXhfc2VhdHMiOjEwMCwiZXhwIjoiMjA5OS0xMi0zMSIsImlhdCI6IjIwMjYtMDItMjYiLCJpZCI6ImxpY190ZXN0MDAwMyJ9.cag_hJPe7RYmBlnb0zMTOgcY0FvE4CQchG3Ju13XvMDbZPgu7SllTY9OhZr67R9K0J1ZYrvVBP68hQrkBZtKBA"

# Bad version (v:2)
_BAD_VERSION_KEY = "sc_lic_eyJ2IjoyLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJYIiwibWF4X3NlYXRzIjoxLCJleHAiOiIyMDk5LTEyLTMxIiwiaWF0IjoiMjAyNi0wMi0yNiIsImlkIjoibGljX3Rlc3QwMDA0In0.QVksXtofZh039-QJPFuf3SaOSlVBx-BstD8R0QmnTslDP6y0yCz2e9xEkCx5WE_zvK6JzcRhWY1_CZlV5lhbAg"


# -- Format parsing tests ----------------------------------------------------


class TestFormatParsing:
    def test_empty_string(self):
        result = validate_license_key("")
        assert result is COMMUNITY_MODE
        assert result.status == LicenseStatus.missing
        assert result.tier == LicenseTier.community

    def test_none_like_empty(self):
        result = validate_license_key("   ")
        assert result is COMMUNITY_MODE

    def test_wrong_prefix(self):
        result = validate_license_key("wrong_prefix_abc.def")
        assert result.status == LicenseStatus.invalid
        assert "prefix" in result.detail.lower()

    def test_missing_signature(self):
        result = validate_license_key("sc_lic_payloadonly")
        assert result.status == LicenseStatus.invalid
        assert "signature" in result.detail.lower()

    def test_empty_payload_part(self):
        result = validate_license_key("sc_lic_.signature")
        assert result.status == LicenseStatus.invalid

    def test_empty_signature_part(self):
        result = validate_license_key("sc_lic_payload.")
        assert result.status == LicenseStatus.invalid


# -- Signature verification tests --------------------------------------------


class TestSignatureVerification:
    def test_valid_pro_key(self):
        result = validate_license_key(_PRO_KEY)
        assert result.status == LicenseStatus.valid
        assert result.tier == LicenseTier.pro
        assert result.licensee == "Test Corp"
        assert result.max_seats == 10
        assert result.expires == "2099-12-31"
        assert result.license_id == "lic_test0001"
        assert result.is_production is True

    def test_expired_key(self):
        result = validate_license_key(_EXPIRED_KEY)
        assert result.status == LicenseStatus.expired
        assert result.tier == LicenseTier.pro
        assert result.licensee == "Expired Corp"
        assert "expired" in result.detail.lower()
        assert result.is_production is False

    def test_wrong_signature(self):
        # Swap the signature from a different key
        payload = _PRO_KEY.rsplit(".", 1)[0]
        sig = _ENTERPRISE_KEY.rsplit(".", 1)[1]
        tampered = f"{payload}.{sig}"
        result = validate_license_key(tampered)
        assert result.status == LicenseStatus.invalid
        assert "signature" in result.detail.lower()

    def test_tampered_payload(self):
        # Modify a character in the payload
        parts = _PRO_KEY.split(".")
        payload = list(parts[0])
        # Flip a character in the middle of the base64 payload
        idx = len("sc_lic_") + 10
        payload[idx] = "A" if payload[idx] != "A" else "B"
        tampered = "".join(payload) + "." + parts[1]
        result = validate_license_key(tampered)
        assert result.status == LicenseStatus.invalid

    def test_enterprise_tier(self):
        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.status == LicenseStatus.valid
        assert result.tier == LicenseTier.enterprise
        assert result.licensee == "Enterprise Inc"
        assert result.max_seats == 100
        assert result.is_production is True

    def test_bad_version(self):
        result = validate_license_key(_BAD_VERSION_KEY)
        assert result.status == LicenseStatus.invalid
        assert "version" in result.detail.lower()


# -- Community mode tests ----------------------------------------------------


class TestCommunityMode:
    def test_community_mode_properties(self):
        assert COMMUNITY_MODE.status == LicenseStatus.missing
        assert COMMUNITY_MODE.tier == LicenseTier.community
        assert COMMUNITY_MODE.is_production is False
        assert COMMUNITY_MODE.licensee == ""
        assert COMMUNITY_MODE.max_seats == 0

    def test_community_mode_is_frozen(self):
        with pytest.raises(AttributeError):
            COMMUNITY_MODE.status = LicenseStatus.valid  # type: ignore[misc]


# -- Caching tests -----------------------------------------------------------


class TestCaching:
    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_get_license_caches(self):
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            result2 = get_license()
            assert result1 is result2
            assert result1.status == LicenseStatus.valid

    def test_reset_cache_clears(self):
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            assert result1.status == LicenseStatus.valid

            reset_cache()
            mock_settings.license_key = ""
            result2 = get_license()
            assert result2 is COMMUNITY_MODE


# -- Cryptography fallback test ----------------------------------------------


class TestCryptographyFallback:
    def test_missing_cryptography_returns_invalid(self):
        import importlib
        import sys

        # Temporarily hide the cryptography package
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = validate_license_key(_PRO_KEY)
            assert result.status == LicenseStatus.invalid
            assert "cryptography" in result.detail.lower()
