"""MCP stdio proxy: forward another MCP server's tools through PlanckBot.

Claude Code (or any MCP client) launches this as a subprocess. This process
then launches the real upstream MCP server as *its* subprocess. Every
list_tools and call_tool round-trips through here, giving PlanckBot an
intercept point without any changes to the upstream or to Claude's config.

Register with Claude Code:

    claude mcp add planckbot-fs -- planckbot-mcp \\
        --upstream-command 'uvx' --upstream-args 'mcp-server-filesystem' --upstream-args '/path/to/repo' \\
        --mode observe

    # or in ~/.claude.json:
    {
      "mcpServers": {
        "planckbot-fs": {
          "command": "planckbot-mcp",
          "args": [
            "--upstream-command", "uvx",
            "--upstream-args", "mcp-server-filesystem",
            "--upstream-args", "/path/to/repo",
            "--mode", "observe"
          ]
        }
      }
    }

Modes:
    observe    — log every call, never modify (safe default)
    suggest    — log + ask the active adapter for a prediction (still returns
                 raw). Requires a trained checkpoint for the tool.
    intervene  — apply the adapter above threshold (requires a checkpoint too).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any, Optional

from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.models.checkpoints import CheckpointManager
from planckbot.proxy import (
    InterveneMode,
    ObserveMode,
    PlanckProxy,
    ProxyMode,
    SuggestMode,
)
from planckbot.tools.triples import TriplesStore


def _build_mode(name: str, threshold: float) -> ProxyMode:
    name = name.lower()
    if name == "observe":
        return ObserveMode()
    if name == "suggest":
        return SuggestMode(confidence_threshold=threshold)
    if name == "intervene":
        return InterveneMode(confidence_threshold=threshold)
    raise SystemExit(f"unknown mode: {name}")


def _build_predictor(
    ckpt_mgr: CheckpointManager,
    device: str,
    lazy_load: bool = True,
):
    """Return a predictor(tool_name, input, output, strategy) -> (text, conf).

    Lazily loads the active adapter for each tool the first time it's asked
    about. Tools without a trained adapter return ("", 0.0), which means any
    gated mode falls through to the raw output.
    """
    from planckbot.models.inference import format_prompt, predict
    from planckbot.models.loader import load_model

    cache: dict[str, Any] = {}

    def _predict(tool_name: str, input_str: str, output_str: str, strategy: str):
        if tool_name not in cache:
            ckpt = ckpt_mgr.get_active(tool_name)
            if ckpt is None:
                cache[tool_name] = None
                return ("", 0.0)
            loaded = load_model(
                model_name=ckpt.base_model,
                device=device,
                adapter_path=ckpt.adapter_path,
            )
            cache[tool_name] = loaded
        loaded = cache[tool_name]
        if loaded is None:
            return ("", 0.0)
        prompt = format_prompt(tool_name, output_str, strategy)
        r = predict(
            loaded.model, loaded.tokenizer, prompt,
            max_new_tokens=128, device=loaded.device,
        )
        return (r.output_text.strip(), r.confidence)

    return _predict


def _content_to_text(content_blocks) -> str:
    """Flatten MCP content blocks to a single string for the proxy layer."""
    parts = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _text_to_content(text: str):
    """Wrap a string back into an MCP TextContent list."""
    from mcp.types import TextContent
    return [TextContent(type="text", text=text)]


def _strip_output_schema(tool):
    """Drop `outputSchema` from a Tool before we re-advertise it.

    Upstream servers (notably @modelcontextprotocol/server-filesystem) declare
    outputSchema but return unstructured content, which triggers
    "Output validation error" in strict clients like Claude Code. We don't
    need outputSchema for the proxy's purpose, so strip it.
    """
    try:
        return tool.model_copy(update={"outputSchema": None})
    except AttributeError:
        # Fallback for dataclass-style Tool objects.
        tool.outputSchema = None
        return tool


async def run_proxy(
    upstream_command: str,
    upstream_args: list[str],
    mode: ProxyMode,
    server_name: str = "planckbot",
    enable_predictor: bool = False,
    strategy: str = "filter_output",
) -> None:
    """Main async entrypoint: run the stdio proxy until the client closes."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool

    conn = get_connection(config.db_path)
    store = TriplesStore(conn)
    predictor = None
    if enable_predictor:
        ckpt_mgr = CheckpointManager(conn)
        predictor = _build_predictor(ckpt_mgr, device=config.device)

    proxy = PlanckProxy(
        store=store,
        mode=mode,
        predictor=predictor,
        strategy=strategy,
    )

    # Load ignore rules. Upstream tool servers like filesystem-server
    # take the served path as their LAST positional argument, so infer
    # the root from upstream_args. The HARD_DEFAULTS still apply when
    # we can't infer a served path (better to be conservative).
    from planckbot.proxy.ignore import (
        base_arg_of,
        filter_listing,
        load_rules,
        path_arg_of,
    )
    served_root = upstream_args[-1] if upstream_args else None
    ignore_rules = load_rules(served_root)
    if ignore_rules.custom_path:
        print(
            f"[planckbot-mcp] loaded .mcpignore from "
            f"{ignore_rules.custom_path} "
            f"({len(ignore_rules.patterns)} patterns total)",
            file=sys.stderr, flush=True,
        )

    async with AsyncExitStack() as stack:
        # 1) Spawn + connect to the upstream MCP server.
        upstream_params = StdioServerParameters(
            command=upstream_command, args=upstream_args,
        )
        read_up, write_up = await stack.enter_async_context(stdio_client(upstream_params))
        upstream = await stack.enter_async_context(ClientSession(read_up, write_up))
        await upstream.initialize()

        # Cache the upstream tool list (we'll re-list on demand too).
        upstream_tools = await upstream.list_tools()
        tool_schemas: dict[str, Tool] = {
            t.name: _strip_output_schema(t) for t in upstream_tools.tools
        }

        # 2) Build our own MCP server that proxies.
        server = Server(server_name)

        @server.list_tools()
        async def _list_tools() -> list[Tool]:
            refreshed = await upstream.list_tools()
            tools = [_strip_output_schema(t) for t in refreshed.tools]
            for t in tools:
                tool_schemas[t.name] = t
            return tools

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict):
            args = arguments or {}

            # Ignore-rules gate #1: if this is a 'read specific file' tool
            # and the path matches a blocked pattern, refuse WITHOUT
            # calling upstream. The file content never leaves the upstream
            # process and never gets recorded as a triple.
            tgt_path = path_arg_of(name, args)
            if tgt_path:
                match = ignore_rules.matches(tgt_path)
                if match is not None:
                    print(
                        f"[planckbot-mcp] blocked {name} on {tgt_path!r} "
                        f"(pattern: {match!r})",
                        file=sys.stderr, flush=True,
                    )
                    return _text_to_content(
                        f"Refused by PlanckBot .mcpignore policy: path "
                        f"matches pattern {match!r}. If this was a "
                        "mistake, edit .mcpignore at the served root."
                    )

            # We need to hand the proxy a sync callable, but the upstream
            # call is async. Perform the async call here, then wrap the
            # resulting text in a zero-work closure the proxy can "invoke"
            # while it records the triple and makes its decision.
            res = await upstream.call_tool(name, args)
            raw_text = _content_to_text(res.content)

            # Ignore-rules gate #2: for listing tools, strip entries that
            # match the rules. Claude never sees them, so downstream
            # `read_file` calls won't know to ask for them.
            base = base_arg_of(name, args)
            if base is not None:
                redacted_text, n_redacted = filter_listing(
                    raw_text, base, ignore_rules,
                )
                if n_redacted:
                    print(
                        f"[planckbot-mcp] redacted {n_redacted} entries "
                        f"from {name}({base!r})",
                        file=sys.stderr, flush=True,
                    )
                raw_text = redacted_text

            def _already_ran(**_ignored):
                return raw_text

            result = proxy.call(_already_ran, name, **args)

            # If the upstream returned structured content, preserve it for
            # the unmodified path. If we intervened (swapped text), just
            # return the swapped string as text content.
            return _text_to_content(result.final_output)

        # 3) Run our server over stdio until the client disconnects.
        async with stdio_server() as (read_down, write_down):
            await server.run(
                read_down,
                write_down,
                server.create_initialization_options(),
            )


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(
        prog="planckbot-mcp",
        description="Proxy an MCP server through PlanckBot for observation + "
                    "token-saving interception.",
        epilog=(
            "Pass the upstream command and its args after `--`. Example:\n"
            "  planckbot-mcp --mode observe -- "
            "npx -y @modelcontextprotocol/server-filesystem /path/to/repo\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Preferred form: `-- cmd args...`. Positional REMAINDER so dash-prefixed
    # upstream args (like `-y`) don't get mistaken for our flags.
    p.add_argument(
        "upstream", nargs=argparse.REMAINDER,
        help="Upstream command + args, after `--`.",
    )
    # Legacy form kept for backward-compat. Use `=` with values that start
    # with a dash (argparse: `--upstream-args=-y`).
    p.add_argument(
        "--upstream-command", default=None,
        help="[legacy] Upstream executable. Prefer `-- cmd args...`.",
    )
    p.add_argument(
        "--upstream-args", action="append", default=[],
        help="[legacy] Argument for the upstream command (repeatable). "
             "Use `--upstream-args=VALUE` if the value starts with `-`.",
    )
    p.add_argument(
        "--mode", default="observe", choices=["observe", "suggest", "intervene"],
        help="Proxy mode. observe=log only, suggest=log+predict, intervene=apply.",
    )
    p.add_argument(
        "--threshold", type=float, default=0.90,
        help="Confidence threshold for suggest/intervene modes.",
    )
    p.add_argument(
        "--strategy", default="filter_output",
        choices=["filter_output", "compress_input", "short_circuit"],
    )
    p.add_argument(
        "--name", default="planckbot",
        help="Name the proxy advertises to the downstream MCP client.",
    )
    args = p.parse_args(argv)

    # Resolve the upstream from either form.
    upstream_positional = list(args.upstream or [])
    if upstream_positional and upstream_positional[0] == "--":
        upstream_positional = upstream_positional[1:]

    if upstream_positional:
        upstream_command = upstream_positional[0]
        upstream_args = upstream_positional[1:]
    elif args.upstream_command:
        upstream_command = args.upstream_command
        upstream_args = args.upstream_args
    else:
        p.error(
            "missing upstream. Use either `-- cmd args...` (preferred) or "
            "`--upstream-command CMD --upstream-args=VALUE ...`."
        )

    mode = _build_mode(args.mode, args.threshold)
    enable_predictor = args.mode in ("suggest", "intervene")

    try:
        asyncio.run(run_proxy(
            upstream_command=upstream_command,
            upstream_args=upstream_args,
            mode=mode,
            server_name=args.name,
            enable_predictor=enable_predictor,
            strategy=args.strategy,
        ))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
