"""
Every agent tool AutoMC has, in one list. Each entry: name, description (its limits in plain words), JSON schema of the arguments,
and the handler (a dict of arguments in, a JSON-serializable dict out). The agent core, the command line (`python -m automc tool`) and an
MCP server read this list; none of them wraps a tool differently.
"""
from __future__ import annotations

from agent import skill_tool
from tools.chats import tools as chat_tools
from tools.documents import tools as document_tools
from tools.files import tools as file_tools
from tools.memory import tools as memory_tools
from tools.openmc import authoring_tools
from tools.openmc import tools as openmc_tools
from tools.python import tools as python_tools
from tools.reactors.triga import tools as triga_tools
from tools.web import tools as web_tools

TOOLS = (triga_tools.TOOLS + openmc_tools.TOOLS + authoring_tools.TOOLS + skill_tool.TOOLS + file_tools.TOOLS + document_tools.TOOLS
         + web_tools.TOOLS + python_tools.TOOLS + chat_tools.TOOLS + memory_tools.TOOLS)
_BY_NAME = {t["name"]: t for t in TOOLS}
assert len(_BY_NAME) == len(TOOLS), "two tools share a name"


def names():
    return [t["name"] for t in TOOLS]


def describe():
    """Name, description and argument schema of every tool, without the handlers (JSON-serializable)."""
    return [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in TOOLS]


def call(name, args=None):
    """Call a tool by name. An unknown name is an error payload like any other."""
    t = _BY_NAME.get(name)
    if t is None:
        return {"ok": False, "error": {"type": "UnknownTool", "field": "name", "message": f"no tool {name!r}; tools: {names()}",
                                       "exit_code": 2}}
    return t["handler"](args)
