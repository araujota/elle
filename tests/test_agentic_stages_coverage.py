"""Tests for elle.cli.agentic.stages - targeting uncovered lines.

Covers:
  - Lines 66-78: LoopStage.description property (dict + fallback)
  - Lines 252-283: explain_stages() function
"""

from __future__ import annotations

from elle.cli.agentic.stages import LoopStage, explain_stages


class TestLoopStageDescription:
    """Cover the description property (lines 66-78)."""

    def test_all_stages_have_descriptions(self) -> None:
        """Every LoopStage member returns a non-empty description."""
        for stage in LoopStage:
            desc = stage.description
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_known_stage_descriptions(self) -> None:
        """Spot-check a few known descriptions."""
        assert LoopStage.OBSERVE.description == "Receiving input and gathering context"
        assert LoopStage.ACT.description == "Executing selected capability"
        assert LoopStage.ERROR.description == "Loop encountered an error"
        assert LoopStage.COMPLETE.description == "Loop completed successfully"

    def test_description_fallback_unknown_stage(self) -> None:
        """The .get() fallback 'Unknown stage' is returned for unmapped values.

        Since all enum members are in the dict, we exercise the fallback
        by calling the property implementation directly with a value not
        in the descriptions dict.
        """
        # Access the property's underlying function with a fake value.
        # LoopStage.description is a property; we call the fget with
        # a value that won't match any key in the descriptions dict.
        # The simplest way is to create a mock-like object.
        result = LoopStage.description.fget.__call__("not_a_real_stage")  # type: ignore[union-attr]
        assert result == "Unknown stage"


class TestExplainStages:
    """Cover explain_stages() function (lines 252-283)."""

    def test_returns_string(self) -> None:
        """explain_stages() returns a non-empty string."""
        result = explain_stages()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_header(self) -> None:
        """Output starts with the header."""
        result = explain_stages()
        assert "AGENTIC LOOP STAGES" in result

    def test_contains_all_active_stages(self) -> None:
        """All stages except COMPLETE and ERROR appear in the output."""
        result = explain_stages()
        for stage in LoopStage:
            if stage not in (LoopStage.COMPLETE, LoopStage.ERROR):
                assert stage.value.upper() in result

    def test_excludes_terminal_stages(self) -> None:
        """COMPLETE and ERROR are not listed as named stages."""
        result = explain_stages()
        lines = result.split("\n")
        # The stage names are indented; COMPLETE/ERROR should not appear as stage names
        stage_lines = [line.strip() for line in lines if line.startswith("  ") and line.strip().isupper()]
        assert "COMPLETE" not in stage_lines
        assert "ERROR" not in stage_lines

    def test_contains_stage_flow_section(self) -> None:
        """Output includes the STAGE FLOW section."""
        result = explain_stages()
        assert "STAGE FLOW:" in result
        assert "Simple query" in result
        assert "Action required:" in result
        assert "Multi-step action:" in result

    def test_contains_descriptions_for_active_stages(self) -> None:
        """Each active stage's description appears in the output."""
        result = explain_stages()
        for stage in LoopStage:
            if stage not in (LoopStage.COMPLETE, LoopStage.ERROR):
                assert stage.description in result
