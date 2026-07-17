"""Pure, HTTP-free logic behind configurable per-bot custom functions (Plan 02, Phase 2).

A "custom function" is a PM-configured HTTP call a bot can make before / during / after a
conversation (see backend/models.py:CustomFunction). The actual network round-trip lives in
the pipeline modules (bot.py, bot_dev.py, bot_pipeline.py), but everything that decides
*what request to make* and *what to pull out of the response* is factored here so it can be
unit-tested without a live server and shared by all three pipelines.

Nothing in this module performs I/O.
"""

from typing import Any, Callable

# Sentinel distinguishing "path resolved to None-ish nothing" from "key genuinely absent".
_MISSING = object()


def extract_json_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path (e.g. ``data.lead.name`` or ``results.0.id``) into ``obj``.

    Numeric segments index into lists. Returns ``None`` if any segment is missing or if the
    path descends into a non-container. Note a genuinely-present falsy value (``0``, ``False``,
    ``""``) is returned as-is — only truly-absent paths yield ``None``.
    """
    if not path:
        return None
    cur = obj
    for segment in path.split("."):
        if isinstance(cur, dict):
            if segment not in cur:
                return None
            cur = cur[segment]
        elif isinstance(cur, (list, tuple)):
            if not segment.lstrip("-").isdigit():
                return None
            idx = int(segment)
            if idx < -len(cur) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def apply_store_variables(
    store_variables: list[dict] | None,
    response: Any,
    target: dict,
) -> dict:
    """Apply a function's ``store_variables`` spec to its response.

    For each ``{"variable": name, "json_path": path}`` entry with both fields set, extract the
    value from ``response`` and write it into ``target`` (typically ``call_state["vars"]``).
    Missing paths are skipped silently. Returns the dict of values that were actually stored.
    """
    stored: dict = {}
    for spec in store_variables or []:
        variable = (spec.get("variable") or "").strip()
        json_path = (spec.get("json_path") or "").strip()
        if not variable or not json_path:
            continue
        value = extract_json_path(response, json_path)
        if value is None:
            continue
        target[variable] = value
        stored[variable] = value
    return stored


def resolve_timeout_seconds(fn_cfg: dict, default: float = 8.0) -> float:
    """Return the per-function timeout in seconds, or ``default`` if unset/invalid."""
    try:
        timeout_ms = int(fn_cfg.get("timeout_ms"))
    except (TypeError, ValueError):
        return default
    if timeout_ms <= 0:
        return default
    return timeout_ms / 1000.0


def build_http_call(fn_cfg: dict, args: dict) -> dict:
    """Build the request shape for a custom function call — pure, no I/O.

    Returns a dict ``{method, url, headers, params, json, data}`` that a caller feeds straight
    into ``aiohttp``. GET requests carry merged ``query_params + args`` as URL params. Non-GET
    requests keep ``query_params`` on the URL and put ``custom_body + args`` in the body, as
    either JSON (``body_mode='json'``, default) or form data (``body_mode='form'``, or the
    legacy ``body_format='form'``).
    """
    method = (fn_cfg.get("method") or "POST").upper()
    url = (fn_cfg.get("url") or "").strip()
    headers = dict(fn_cfg.get("headers") or {})
    query_params = dict(fn_cfg.get("query_params") or {})

    body_mode = fn_cfg.get("body_mode")
    if not body_mode:
        body_mode = "form" if fn_cfg.get("body_format") == "form" else "json"

    call = {"method": method, "url": url, "headers": headers, "params": None, "json": None, "data": None}

    if method == "GET":
        call["params"] = {**query_params, **args}
        return call

    call["params"] = query_params or None
    body = {**dict(fn_cfg.get("custom_body") or {}), **args}
    if body_mode == "form":
        call["data"] = body
    else:
        call["json"] = body
    return call


def build_tool_schema(fn_cfg: dict) -> dict:
    """Build a LiveKit RawFunctionTool schema for a during_call custom function.

    Returns ``{"name", "description", "parameters"}`` where ``parameters`` is a JSON Schema
    object. In JSON mode (``raw_body_schema`` set) that schema is used verbatim; otherwise it
    is generated from the ``parameters`` list (the 'Request Body → Parameters' rows).
    """
    raw = fn_cfg.get("raw_body_schema")
    if raw:
        parameters = raw
    else:
        properties: dict = {}
        required: list[str] = []
        for param in fn_cfg.get("parameters") or []:
            name = (param.get("name") or "").strip()
            if not name:
                continue
            prop: dict = {"type": param.get("type") or "string"}
            if param.get("description"):
                prop["description"] = param["description"]
            properties[name] = prop
            if param.get("required"):
                required.append(name)
        parameters = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
    return {
        "name": fn_cfg.get("name") or "",
        "description": fn_cfg.get("description") or "",
        "parameters": parameters,
    }


import re as _re

# Matches {{name}} or {{vars.name}} with optional surrounding whitespace.
_VAR_PLACEHOLDER_RE = _re.compile(r"\{\{\s*(?:vars\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def interpolate_vars(text: str, variables: dict | None) -> str:
    """Replace ``{{name}}`` / ``{{vars.name}}`` placeholders in ``text`` with values from
    ``variables``. Unknown placeholders are left untouched (so a typo never blanks the prompt).
    """
    if not text or not variables:
        return text

    def _sub(match: "_re.Match") -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)

    return _VAR_PLACEHOLDER_RE.sub(_sub, text)


def functions_for_trigger(functions: list[dict] | None, trigger: str) -> list[dict]:
    """Select enabled custom functions with a URL whose trigger matches ``trigger``
    (``pre_call`` or ``post_call``).

    Legacy entries with no ``trigger`` default to ``during_call`` and therefore never fire as
    lifecycle hooks — only functions a PM explicitly marked pre/post-call run before/after a
    conversation.
    """
    out: list[dict] = []
    for fn in functions or []:
        if (fn.get("trigger") or "during_call") != trigger:
            continue
        if not fn.get("enabled", True):
            continue
        if not (fn.get("url") or "").strip():
            continue
        out.append(fn)
    return out


async def run_lifecycle_functions(
    functions: list[dict] | None,
    trigger: str,
    runtime_params: dict,
    call_fn: "Callable[[dict, dict], Any]",
    vars_target: dict,
) -> list[dict]:
    """Fire all pre_call / post_call functions for ``trigger`` in order.

    ``call_fn(fn_cfg, runtime_params)`` performs the actual HTTP call (injected so this stays
    unit-testable) and returns the decoded response or ``None``. For each function, extracted
    ``store_variables`` are written into ``vars_target``. A failure in one function never stops
    the others — lifecycle hooks must never break a call. Returns a per-function result summary
    ``[{"name", "ok", "response", "stored", "error"}]`` for logging/analytics.
    """
    results: list[dict] = []
    for fn in functions_for_trigger(functions, trigger):
        name = fn.get("name") or ""
        entry = {"name": name, "ok": False, "response": None, "stored": {}, "error": None}
        try:
            response = await call_fn(fn, runtime_params)
            if response is None:
                entry["error"] = "no response"
            else:
                entry["ok"] = True
                entry["response"] = response
                entry["stored"] = apply_store_variables(fn.get("store_variables"), response, vars_target)
        except Exception as exc:  # noqa: BLE001 — hooks must never propagate
            entry["error"] = str(exc)
        results.append(entry)
    return results


def selectable_during_call_functions(functions: list[dict] | None, reserved: set[str]) -> list[dict]:
    """Pick the custom functions that should be registered as LLM tools during a call.

    Keeps only enabled entries whose trigger is ``during_call`` (the default when unset) and
    that have a name not already used by a built-in tool (``reserved``). Duplicate names are
    deduped, keeping the first occurrence, so a bad config can never register two tools with
    the same name.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for fn in functions or []:
        name = (fn.get("name") or "").strip()
        if not name or name in reserved or name in seen:
            continue
        if not fn.get("enabled", True):
            continue
        if (fn.get("trigger") or "during_call") != "during_call":
            continue
        seen.add(name)
        out.append(fn)
    return out
