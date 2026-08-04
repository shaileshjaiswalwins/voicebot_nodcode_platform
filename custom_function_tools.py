"""Turn a bot's during_call custom functions into LiveKit RawFunctionTool objects
(Plan 02, Phase 3).

This is the one place that touches LiveKit's tool API, so the three pipelines (bot.py,
bot_dev.py, bot_pipeline.py) share identical dynamic-tool behavior. The pure schema/selection
logic lives in custom_functions.py; this module only wires it to `function_tool`.
"""

from typing import Any, Awaitable, Callable

from livekit.agents import RunContext, function_tool

from custom_functions import build_tool_schema, selectable_during_call_functions

# Tool names already provided as hardcoded built-ins by every pipeline. Custom functions with
# these names are skipped so they never double-register.
BUILTIN_TOOL_NAMES = {"FetchLead", "FetchCategorySchema"}

# execute_fn signature mirrors bot._execute_function_call.
ExecuteFn = Callable[..., Awaitable[dict]]


def build_during_call_tools(
    functions: list[dict] | None,
    execute_fn: ExecuteFn,
    call_state: dict,
) -> list:
    """Build one RawFunctionTool per selectable during_call custom function.

    Each tool forwards the LLM-supplied ``raw_arguments`` to ``execute_fn`` under the
    function's own name. The per-tool factory binds ``name`` and ``schema`` eagerly so late
    binding can't make every tool dispatch under the last function's name.
    """
    tools: list = []
    for fn in selectable_during_call_functions(functions, reserved=BUILTIN_TOOL_NAMES):
        tools.append(_make_tool(fn.get("name") or "", build_tool_schema(fn), functions, execute_fn, call_state))
    return tools


def _make_tool(name: str, schema: dict, functions, execute_fn: ExecuteFn, call_state: dict):
    @function_tool(raw_schema=schema)
    async def _tool(ctx: RunContext, raw_arguments: dict[str, Any]) -> dict:
        return await execute_fn(name, raw_arguments or {}, functions=functions, call_state=call_state)

    return _tool
