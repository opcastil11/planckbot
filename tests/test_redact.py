"""Tests for ingest.redact — secret detection + redaction at storage time."""

from __future__ import annotations

import json

from planckbot.ingest.redact import redact, redact_obj, SECRET_PATTERNS


def test_empty_input_returns_empty_no_hits():
    out, hits = redact("")
    assert out == ""
    assert hits == []

    out, hits = redact_obj({})
    assert out == {}
    assert hits == []


def test_anthropic_key_redacted():
    text = "Authorization: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    out, hits = redact(text)
    assert "anthropic-key" in hits
    assert "sk-ant-api03" not in out
    assert "[REDACTED:anthropic-key]" in out


def test_openai_proj_key_redacted():
    text = 'OPENAI_API_KEY=sk-proj-i2I2y2ewRBWUmIX-eo5s2ZzPM9vICLLiNLpICrxzmt_3vs'
    out, hits = redact(text)
    assert "openai-proj-key" in hits
    assert "sk-proj-i2I2" not in out


def test_github_oauth_redacted():
    text = "git push https://gho_ABCDEFGHIJ1234567890abcdefGHIJ@github.com/foo/bar"
    out, hits = redact(text)
    assert "github-oauth" in hits
    assert "gho_ABC" not in out


def test_aws_access_key_redacted():
    text = "credentials: AKIAIOSFODNN7EXAMPLE region: us-east-1"
    out, hits = redact(text)
    assert "aws-access-key" in hits
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_private_key_block_redacted():
    text = """logs:
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA1234567890abcdef
fakebody==
-----END RSA PRIVATE KEY-----
end of log"""
    out, hits = redact(text)
    assert "private-key" in hits
    assert "MIIEowIBAA" not in out
    assert "end of log" in out  # surrounding content preserved


def test_jwt_redacted():
    text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwiaWF0IjoxNTE2MjM5MDIyfQ.abc123"
    out, hits = redact(text)
    assert "jwt" in hits
    assert "eyJhbGciOi" not in out


def test_password_field_preserves_key():
    text = '{"username": "alice", "password": "hunter2hunter2"}'
    out, hits = redact(text)
    assert "password-field" in hits
    assert '"password"' in out  # key preserved
    assert "hunter2" not in out
    # still valid JSON
    parsed = json.loads(out)
    assert parsed["username"] == "alice"
    assert parsed["password"].startswith("[REDACTED")


def test_api_key_field_preserves_key():
    text = '{"api_key": "OPAQUE-not-a-real-token-000000000000"}'
    out, hits = redact(text)
    assert "api-key-field" in hits
    parsed = json.loads(out)
    assert "REDACTED" in parsed["api_key"]


def test_multiple_secrets_in_one_text():
    text = (
        "ANTHROPIC_KEY=sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYzABCD1234\n"
        "GITHUB_TOKEN=ghp_AaaaaBbbbbCccccDddddEeeeeFffffGggg\n"
        '{"password": "verysecret"}\n'
    )
    out, hits = redact(text)
    assert "anthropic-key" in hits
    assert "github-pat" in hits
    assert "password-field" in hits
    for needle in ["sk-ant-api03", "ghp_Aaa", "verysecret"]:
        assert needle not in out


def test_no_false_positive_on_clean_text():
    text = "Just a regular Bash command: ls -la /home/user/project"
    out, hits = redact(text)
    assert hits == []
    assert out == text


def test_short_sk_prefix_not_matched():
    # Don't redact things like `sk-` in arbitrary identifiers.
    text = "function sk-something() {} or just sk-12345"
    out, hits = redact(text)
    assert hits == []
    assert out == text


def test_redact_obj_walks_nested_dict():
    obj = {
        "command": "curl -H 'Authorization: sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF'",
        "metadata": {
            "user": "alice",
            "deep": {"token": "ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHH"},
        },
        "args": ["--verbose", "AKIAIOSFODNN7EXAMPLE"],
    }
    new, hits = redact_obj(obj)
    assert "anthropic-key" in hits
    assert "github-pat" in hits
    assert "aws-access-key" in hits
    assert new["metadata"]["user"] == "alice"  # untouched
    assert "REDACTED" in new["command"]
    assert "REDACTED" in new["metadata"]["deep"]["token"]
    assert "REDACTED" in new["args"][1]
    assert new["args"][0] == "--verbose"  # untouched


def test_redact_obj_preserves_non_string_types():
    obj = {"count": 42, "flag": True, "ratio": 3.14, "null": None}
    new, hits = redact_obj(obj)
    assert hits == []
    assert new == obj


def test_pattern_names_unique():
    names = [n for n, _ in SECRET_PATTERNS]
    assert len(names) == len(set(names)), "pattern names must be unique"
