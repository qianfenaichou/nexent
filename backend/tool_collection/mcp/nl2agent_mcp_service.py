from fastmcp import FastMCP
from tool_collection.mcp.nl2agent_mcp_tools import (
    NL2A_MCP_SERVICE_NAME,
    NL2A_WRAPPER_DESCRIPTION,
    NL2A_WRAPPER_LOCAL_NAME,
    NL2AGENT_MCP_TOOL_META,
    RECOMMEND_RESOURCES_DESCRIPTION,
    RECOMMEND_RESOURCES_LOCAL_NAME,
    SAVE_AGENT_DRAFT_FIELDS_DESCRIPTION,
    SAVE_AGENT_DRAFT_FIELDS_LOCAL_NAME,
    SEARCH_INSTALLED_MCP_TOOLS_DESCRIPTION,
    SEARCH_INSTALLED_MCP_TOOLS_LOCAL_NAME,
    SEARCH_INSTALLED_RESOURCES_DESCRIPTION,
    SEARCH_INSTALLED_RESOURCES_LOCAL_NAME,
    SEARCH_UNINSTALLED_RESOURCES_DESCRIPTION,
    SEARCH_UNINSTALLED_RESOURCES_LOCAL_NAME,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    nl2a_wrapper as _nl2a_wrapper,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    recommend_resources as _recommend_resources,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    save_agent_draft_fields as _save_agent_draft_fields,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    search_installed_mcp_tools as _search_installed_mcp_tools,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    search_installed_resources as _search_installed_resources,
)
from tool_collection.mcp.nl2agent_mcp_tools import (
    search_uninstalled_resources as _search_uninstalled_resources,
)

nl2agent_mcp_service = FastMCP(NL2A_MCP_SERVICE_NAME)
nl2agent_mcp_service.tool(
    _search_installed_mcp_tools,
    name=SEARCH_INSTALLED_MCP_TOOLS_LOCAL_NAME,
    description=SEARCH_INSTALLED_MCP_TOOLS_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
nl2agent_mcp_service.tool(
    _search_installed_resources,
    name=SEARCH_INSTALLED_RESOURCES_LOCAL_NAME,
    description=SEARCH_INSTALLED_RESOURCES_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
nl2agent_mcp_service.tool(
    _search_uninstalled_resources,
    name=SEARCH_UNINSTALLED_RESOURCES_LOCAL_NAME,
    description=SEARCH_UNINSTALLED_RESOURCES_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
nl2agent_mcp_service.tool(
    _recommend_resources,
    name=RECOMMEND_RESOURCES_LOCAL_NAME,
    description=RECOMMEND_RESOURCES_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
nl2agent_mcp_service.tool(
    _save_agent_draft_fields,
    name=SAVE_AGENT_DRAFT_FIELDS_LOCAL_NAME,
    description=SAVE_AGENT_DRAFT_FIELDS_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
nl2agent_mcp_service.tool(
    _nl2a_wrapper,
    name=NL2A_WRAPPER_LOCAL_NAME,
    description=NL2A_WRAPPER_DESCRIPTION,
    meta=NL2AGENT_MCP_TOOL_META,
)
