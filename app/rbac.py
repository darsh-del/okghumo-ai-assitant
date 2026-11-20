"""Role -> allowed Okghumo MCP tools. READ-ONLY BY DESIGN (see CLAUDE.md).

Deny by default: every toolset starts with all tools disabled, and only names in
READ_ONLY_TOOLS can ever be enabled. `authenticate` is deliberately NOT listed: it sends an
OTP to a phone, and the service token comes from OKGHUMO_MCP_TOKEN instead.
Tool-level RBAC is enforced here; row/data-level RBAC must be enforced by the MCP server itself.
"""

READ_ONLY_TOOLS = frozenset({
    "get_analysis_capabilities", "get_analysis_summary", "get_booking_source_breakdown",
    "get_booking_trends", "get_agent_outstandings", "get_agent_ledger",
    "get_collection_summary", "get_bookings", "get_booking_details",
})

_ANALYTICS = ["get_analysis_capabilities", "get_analysis_summary", "get_booking_source_breakdown",
              "get_booking_trends", "get_collection_summary"]

ROLE_TOOLS: dict[str, list[str]] = {
    "admin": sorted(READ_ONLY_TOOLS),
    "staff": _ANALYTICS + ["get_bookings", "get_booking_details"],  # no agent balances
    "viewer": _ANALYTICS,
    # OkGhumo vendor roles (WhatsApp bot): own-client data only, enforced by the token's clientId
    "vendor": sorted(READ_ONLY_TOOLS),
    "superVendor": sorted(READ_ONLY_TOOLS),
}

# Fail at import, not at request time, if anyone adds a non-allowlisted (potentially write) tool.
for _role, _tools in ROLE_TOOLS.items():
    assert set(_tools) <= READ_ONLY_TOOLS, f"{_role} has non-read-only tools: {set(_tools) - READ_ONLY_TOOLS}"


def mcp_toolset(role: str | None) -> dict:
    """Build the mcp_toolset for a role. Raises KeyError for unknown roles (deny by default)."""
    allowed = ROLE_TOOLS[role]
    return {
        "type": "mcp_toolset",
        "mcp_server_name": "okghumo",
        "default_config": {"enabled": False},  # tools the server adds later stay off
        "configs": {t: {"enabled": True} for t in allowed},
    }
