"""Colleague assistant: answers questions about the platform's data, controls and models.

Retrieval over the project docs (with section citations) + read-only SQL over the
PII-masked reporting layer, orchestrated by Claude with tool use. Also exposed as an
MCP server so Claude Desktop / Claude Code can use the same tools.
"""
