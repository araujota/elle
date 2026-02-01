"""Tests for capabilities/autogen/prompts.py coverage.

Covers build_generation_prompt, build_subcommand_prompt, and build_validation_prompt
with various combinations of man page content.
"""

from __future__ import annotations

from elle.capabilities.autogen.models import (
    ParsedExample,
    ParsedFlag,
    ParsedManPage,
    ParsedSynopsis,
)
from elle.capabilities.autogen.prompts import (
    CAPABILITY_GEN_SYSTEM_PROMPT,
    build_generation_prompt,
    build_subcommand_prompt,
    build_validation_prompt,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_man_page(**kwargs) -> ParsedManPage:
    """Build a ParsedManPage with sensible defaults, overriding with kwargs."""
    defaults = {
        "name": "testcmd",
        "section": 1,
        "synopsis": None,
        "description": "",
        "flags": (),
        "examples": (),
        "raw_text": "",
    }
    defaults.update(kwargs)
    return ParsedManPage(**defaults)


def _make_flag(**kwargs) -> ParsedFlag:
    defaults = {
        "name": "verbose",
        "short": "-v",
        "long": "--verbose",
        "description": "Enable verbose output",
    }
    defaults.update(kwargs)
    return ParsedFlag(**defaults)


# ===========================================================================
# System prompt constant
# ===========================================================================


class TestSystemPrompt:
    def test_system_prompt_exists(self):
        assert len(CAPABILITY_GEN_SYSTEM_PROMPT) > 100
        assert "RISK LEVEL GUIDELINES" in CAPABILITY_GEN_SYSTEM_PROMPT
        assert "DOMAIN GUIDELINES" in CAPABILITY_GEN_SYSTEM_PROMPT


# ===========================================================================
# build_generation_prompt
# ===========================================================================


class TestBuildGenerationPrompt:
    def test_minimal_man_page(self):
        """Man page with no synopsis, description, flags, or examples."""
        man = _make_man_page()
        prompt = build_generation_prompt(man)
        assert "testcmd" in prompt
        assert "Instructions" in prompt

    def test_with_synopsis(self):
        syn = ParsedSynopsis(raw="testcmd [OPTIONS] FILE", command="testcmd")
        man = _make_man_page(synopsis=syn)
        prompt = build_generation_prompt(man)
        assert "SYNOPSIS" in prompt
        assert "testcmd [OPTIONS] FILE" in prompt

    def test_with_description(self):
        man = _make_man_page(description="A test command that does stuff.")
        prompt = build_generation_prompt(man)
        assert "DESCRIPTION" in prompt
        assert "A test command" in prompt

    def test_with_flags(self):
        flags = (
            _make_flag(name="verbose", short="-v", long="--verbose", description="Enable verbose"),
            _make_flag(name="quiet", short=None, long="--quiet", description="Suppress output"),
            _make_flag(name="file", short="-f", long=None, description="Input file", metavar="FILE"),
            _make_flag(name="raw", short=None, long=None, description="Raw output"),
        )
        man = _make_man_page(flags=flags)
        prompt = build_generation_prompt(man)
        assert "OPTIONS" in prompt
        assert "--verbose" in prompt
        assert "--quiet" in prompt
        assert "FILE" in prompt
        # Flag with no short or long should use --{name}
        assert "--raw" in prompt

    def test_with_examples(self):
        examples = (
            ParsedExample(command="testcmd --verbose file.txt", description="Verbose mode"),
            ParsedExample(command="testcmd -f out.log", description=""),
        )
        man = _make_man_page(examples=examples)
        prompt = build_generation_prompt(man)
        assert "EXAMPLES" in prompt
        assert "testcmd --verbose file.txt" in prompt
        assert "Verbose mode" in prompt

    def test_full_man_page(self):
        """Man page with all sections populated."""
        syn = ParsedSynopsis(raw="testcmd [OPTIONS] FILE", command="testcmd")
        flags = (_make_flag(),)
        examples = (ParsedExample(command="testcmd -v foo", description="Test"),)
        man = _make_man_page(
            synopsis=syn,
            description="Full test command.",
            flags=flags,
            examples=examples,
        )
        prompt = build_generation_prompt(man)
        assert "SYNOPSIS" in prompt
        assert "DESCRIPTION" in prompt
        assert "OPTIONS" in prompt
        assert "EXAMPLES" in prompt
        assert "Instructions" in prompt

    def test_flags_limit_to_20(self):
        """Only first 20 flags are included."""
        flags = tuple(
            _make_flag(name=f"flag{i}", long=f"--flag{i}", description=f"Flag {i}")
            for i in range(30)
        )
        man = _make_man_page(flags=flags)
        prompt = build_generation_prompt(man)
        assert "--flag19" in prompt
        assert "--flag20" not in prompt

    def test_examples_limit_to_5(self):
        """Only first 5 examples are included."""
        examples = tuple(
            ParsedExample(command=f"cmd{i}", description=f"Ex {i}")
            for i in range(10)
        )
        man = _make_man_page(examples=examples)
        prompt = build_generation_prompt(man)
        assert "cmd4" in prompt
        assert "cmd5" not in prompt


# ===========================================================================
# build_subcommand_prompt
# ===========================================================================


class TestBuildSubcommandPrompt:
    def test_minimal(self):
        man = _make_man_page()
        prompt = build_subcommand_prompt(man, "restart")
        assert "testcmd restart" in prompt
        assert "SUBCOMMAND" in prompt
        assert "testcmd.restart" in prompt

    def test_with_synopsis_and_description(self):
        syn = ParsedSynopsis(raw="testcmd restart [OPTIONS]", command="testcmd")
        man = _make_man_page(synopsis=syn, description="Restart the service.")
        prompt = build_subcommand_prompt(man, "restart")
        assert "SYNOPSIS" in prompt
        assert "DESCRIPTION" in prompt

    def test_with_flags(self):
        flags = (
            _make_flag(name="force", short="-f", long="--force", description="Force restart"),
        )
        man = _make_man_page(flags=flags)
        prompt = build_subcommand_prompt(man, "restart")
        assert "OPTIONS" in prompt
        assert "--force" in prompt

    def test_flags_without_long(self):
        """Flag with only short form uses --{name} as fallback."""
        flags = (_make_flag(name="yes", short="-y", long=None, description="Auto yes"),)
        man = _make_man_page(flags=flags)
        prompt = build_subcommand_prompt(man, "install")
        assert "-y" in prompt

    def test_subcommand_with_hyphen(self):
        """Subcommand with hyphen is converted to underscore in capability name."""
        man = _make_man_page()
        prompt = build_subcommand_prompt(man, "rotate-keys")
        assert "testcmd.rotate_keys" in prompt


# ===========================================================================
# build_validation_prompt
# ===========================================================================


class TestBuildValidationPrompt:
    def test_validation_prompt_structure(self):
        man = _make_man_page(
            flags=(_make_flag(),),
        )
        spec_json = '{"name": "testcmd.run", "risk_level": "low"}'
        prompt = build_validation_prompt(spec_json, man)
        assert "Generated Specification" in prompt
        assert "testcmd.run" in prompt
        assert "Flag Verification" in prompt
        assert "Risk Assessment" in prompt
        assert "valid" in prompt

    def test_validation_prompt_with_many_flags(self):
        flags = tuple(
            _make_flag(name=f"f{i}", long=f"--f{i}", description=f"Flag {i}")
            for i in range(35)
        )
        man = _make_man_page(flags=flags)
        prompt = build_validation_prompt('{"name":"x"}', man)
        # Should include up to 30 flags
        assert "--f29" in prompt

    def test_validation_prompt_with_no_flags(self):
        man = _make_man_page()
        prompt = build_validation_prompt('{}', man)
        assert "testcmd(1)" in prompt
