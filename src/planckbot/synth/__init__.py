"""Layer D — tool synthesis from observed usage patterns.

Three moving parts:

- `detector`: scans `triples` for sequences of tools called close together
  that repeat often enough to justify a merged tool. Output goes into the
  `gap_reports` table.
- `meta.synthesize_tool`: persists a new tool (name + schema + code) into
  `synthesized_tools`, gated by the same AST whitelist the `edit_tool`
  meta-tool uses.
- `mcp_server`: a standalone MCP server (`planckbot-synth`) that exposes
  every `status='active'` row in `synthesized_tools` as an MCP tool. Sends
  `notifications/tools/list_changed` on SIGHUP so Claude Code re-discovers
  the catalog without a session restart.
"""

from planckbot.synth.detector import (
    build_gap_reports,
    find_tool_sequences,
    GapReportStore,
    SequenceMatch,
)
from planckbot.synth.meta import (
    SynthesizedToolStore,
    synthesize_tool,
    activate_tool,
    deactivate_tool,
)

__all__ = [
    "build_gap_reports",
    "find_tool_sequences",
    "GapReportStore",
    "SequenceMatch",
    "SynthesizedToolStore",
    "synthesize_tool",
    "activate_tool",
    "deactivate_tool",
]
