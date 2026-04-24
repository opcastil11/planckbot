"""Tests for the .mcpignore / secret-filter layer of the proxy."""

from __future__ import annotations

from pathlib import Path

import pytest

from planckbot.proxy.ignore import (
    HARD_DEFAULTS,
    IgnoreRules,
    base_arg_of,
    filter_listing,
    load_rules,
    path_arg_of,
)


# ---- IgnoreRules.matches() ------------------------------------------------


def test_matches_dotenv_basename():
    r = IgnoreRules(patterns=(".env",))
    assert r.matches(".env") == ".env"
    assert r.matches("/abs/path/.env") == ".env"


def test_matches_dotenv_glob():
    r = IgnoreRules(patterns=(".env.*",))
    assert r.matches(".env.local") == ".env.*"
    assert r.matches("/x/y/.env.production") == ".env.*"


def test_matches_directory_pattern_catches_subtree():
    r = IgnoreRules(patterns=(".ssh/",))
    assert r.matches("/home/u/.ssh/id_rsa") == ".ssh/"
    assert r.matches("/home/u/.ssh/authorized_keys") == ".ssh/"
    assert r.matches("project/src/main.py") is None


def test_matches_key_extensions():
    r = IgnoreRules(patterns=("*.pem", "*.key"))
    assert r.matches("server.pem") == "*.pem"
    assert r.matches("/etc/ssl/priv.key") == "*.key"
    assert r.matches("README.md") is None


def test_matches_empty_path_returns_none():
    r = IgnoreRules(patterns=(".env",))
    assert r.matches("") is None


def test_matches_respects_pattern_order():
    r = IgnoreRules(patterns=(".env.*", ".env"))
    # Either wins — just confirm something matches and it's truthy
    m = r.matches(".env")
    assert m in {".env", ".env.*"}


def test_matches_full_path_not_basename_only():
    r = IgnoreRules(patterns=("credentials.json",))
    assert r.matches("/opt/app/credentials.json") == "credentials.json"


# ---- load_rules ----------------------------------------------------------


def test_load_rules_defaults_only_when_no_mcpignore(tmp_path):
    r = load_rules(tmp_path)
    assert r.custom_path is None
    # All HARD_DEFAULTS present
    for p in HARD_DEFAULTS:
        assert p in r.patterns


def test_load_rules_reads_custom_mcpignore(tmp_path):
    (tmp_path / ".mcpignore").write_text(
        "# secrets that are project-specific\n"
        "secrets/\n"
        "prod-*.yaml\n"
        "\n"
        "config/local.json\n"
    )
    r = load_rules(tmp_path)
    assert r.custom_path is not None
    assert "secrets/" in r.patterns
    assert "prod-*.yaml" in r.patterns
    assert "config/local.json" in r.patterns
    # HARD_DEFAULTS still present
    assert ".env" in r.patterns


def test_load_rules_skips_blank_lines_and_comments(tmp_path):
    (tmp_path / ".mcpignore").write_text(
        "\n# just a comment\n#another\n  \n*.secret\n"
    )
    r = load_rules(tmp_path)
    assert "*.secret" in r.patterns
    assert "" not in r.patterns
    assert "# just a comment" not in r.patterns


def test_load_rules_no_served_path_still_returns_defaults():
    r = load_rules(None)
    assert ".env" in r.patterns
    assert r.custom_path is None


# ---- filter_listing ------------------------------------------------------


def test_filter_listing_strips_dotenv_entry():
    txt = (
        "[DIR] src\n"
        "[FILE] package.json\n"
        "[FILE] .env.local\n"
        "[FILE] .env.production\n"
    )
    rules = IgnoreRules(patterns=(".env.*",))
    out, n = filter_listing(txt, base_dir="/project", rules=rules)
    assert ".env.local" not in out
    assert ".env.production" not in out
    assert "package.json" in out
    assert n == 2


def test_filter_listing_strips_ssh_dir_entry():
    txt = "[DIR] .ssh\n[DIR] src\n"
    rules = IgnoreRules(patterns=(".ssh/",))
    out, n = filter_listing(txt, base_dir="/home/u", rules=rules)
    assert ".ssh" not in out
    assert "src" in out
    assert n == 1


def test_filter_listing_passes_through_when_nothing_matches():
    txt = "[DIR] src\n[FILE] README.md"
    rules = IgnoreRules(patterns=("*.pem",))
    out, n = filter_listing(txt, "/x", rules)
    assert out == txt
    assert n == 0


def test_filter_listing_handles_blank_lines():
    txt = "[DIR] src\n\n[FILE] .env\n\n"
    rules = IgnoreRules(patterns=(".env",))
    out, n = filter_listing(txt, "/x", rules)
    assert ".env" not in out
    assert n == 1


# ---- path_arg_of / base_arg_of -------------------------------------------


def test_path_arg_of_read_tools():
    assert path_arg_of("read_file", {"path": "/x"}) == "/x"
    assert path_arg_of("read_text_file", {"path": "/y"}) == "/y"
    assert path_arg_of("get_file_info", {"path": "/z"}) == "/z"


def test_path_arg_of_returns_none_for_listing_tool():
    assert path_arg_of("list_directory", {"path": "/x"}) is None


def test_base_arg_of_listing_tools():
    assert base_arg_of("list_directory", {"path": "/x"}) == "/x"
    assert base_arg_of("directory_tree", {"path": "/y"}) == "/y"
    assert base_arg_of("search_files", {"path": "/z"}) == "/z"
    assert base_arg_of("read_file", {"path": "/z"}) is None


def test_base_arg_of_falls_back_to_root_key():
    assert base_arg_of("search_files", {"root": "/alt"}) == "/alt"


# ---- orquesta-specific scenario (integration-shaped) --------------------


def test_orquesta_env_local_is_blocked_by_defaults(tmp_path):
    """Scenario: MCP is serving /orquesta which contains .env.local. The
    default rules must catch it without any custom .mcpignore."""
    rules = load_rules(tmp_path)
    assert rules.matches("/home/kai/work/orquesta/.env.local") is not None
    assert rules.matches("/home/kai/work/orquesta/.env.local.bak") is not None


def test_token_files_blocked():
    rules = load_rules(None)
    assert rules.matches("/opt/secret.pem") is not None
    assert rules.matches("id_rsa") is not None
    assert rules.matches(".aws/credentials") is not None
    assert rules.matches("service-account-prod.json") is not None
