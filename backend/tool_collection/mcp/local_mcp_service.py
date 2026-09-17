from fastmcp import FastMCP

from tool_collection.mcp.kg_tools import SERVICE_NAME as KNOWEVO_MCP_SERVICE_NAME
from tool_collection.mcp.kg_tools import wire as wire_knowevo_mcp
from tool_collection.mcp.nl2agent_mcp_service import nl2agent_mcp_service
from tool_collection.mcp.nl2agent_mcp_tools import NL2A_MCP_TOOL_NAMES

LOCAL_MCP_TOOL_NAME_OVERRIDES = {
    name: name
    for name in NL2A_MCP_TOOL_NAMES
}

# Create MCP server
local_mcp_service = FastMCP("local")
local_mcp_service.mount(
    nl2agent_mcp_service,
    nl2agent_mcp_service.name,
)
# T-08 wiring: mount the KnowEvo kg_search/kg_stats surface (same handlers
# as the standalone knowevo_mcp server - single schema source, no drift).
local_mcp_service.mount(
    wire_knowevo_mcp(),
    KNOWEVO_MCP_SERVICE_NAME,
)


@local_mcp_service.tool(
    name="test_tool_name",
    description="test_tool_description",
)
async def demo_tool(para_1: str, para_2: int) -> str:
    print("tool is called successfully")
    print(para_1, para_2)
    return "success"
