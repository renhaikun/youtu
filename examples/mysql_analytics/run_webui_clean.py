"""
Clean Web UI launcher for the new MySQL BI agent.
Minimal runtime wiring:
 - Wire `ask_user` to UI so mid-run confirmations block correctly.
 - Optionally convert Mermaid code blocks from tool outputs into plain text so the UI renders them.
No other behavior changes to the library.
"""

from __future__ import annotations

import os


def main() -> None:
    # Use the built-in WebUIAgents server, with minimal patching
    # Ensure OrchestraAgent has a no-op build() so WebUIAgents can await it safely
    from utu.agents.orchestra_agent import OrchestraAgent as _OrchestraAgent
    from typing import Any as _Any

    async def _noop_build(self: _Any) -> None:  # type: ignore
        return None

    if not hasattr(_OrchestraAgent, "build"):
        setattr(_OrchestraAgent, "build", _noop_build)

    from utu.ui.webui_agents import WebUIAgents, WebSocketHandler
    from utu.ui import common as ui_common
    from utu.ui.common import Event, TextDeltaContent

    # Patch instantiate_agent to wire `ask_user` into user_interaction toolkits (Simple and Orchestra workers)
    _orig_instantiate = WebSocketHandler.instantiate_agent

    async def _patched_instantiate(self, config):  # type: ignore
        await _orig_instantiate(self, config)
        try:
            from utu.agents.simple_agent import SimpleAgent
            from utu.agents.orchestra_agent import OrchestraAgent

            if isinstance(self.agent, SimpleAgent):
                ui = getattr(self.agent, "_toolkits", {}).get("user_interaction")
                if ui and hasattr(ui, "set_ask_function"):
                    ui.set_ask_function(self.ask_user)
            elif isinstance(self.agent, OrchestraAgent):
                workers = getattr(self.agent, "worker_agents", {}) or {}
                for worker in workers.values():
                    orig_build = worker.build  # bound method

                    async def _patched_worker_build(_self, __orig=orig_build):  # type: ignore
                        await __orig()
                        try:
                            # wire ask_user
                            ui_map = getattr(_self.agent, "_toolkits", {})
                            uikit = ui_map.get("user_interaction")
                            if uikit and hasattr(uikit, "set_ask_function"):
                                uikit.set_ask_function(self.ask_user)

                            # guard: require real DB read before Python execution; reset on ER/table changes
                            if not hasattr(self, "_observed_db_read"):
                                setattr(self, "_observed_db_read", False)

                            ms = ui_map.get("mysql_schema")
                            if ms is not None:
                                if hasattr(ms, "exec_sql"):
                                    _orig_exec_sql = ms.exec_sql

                                    async def _exec_sql_guarded(*a, **kw):  # type: ignore
                                        # ER must be confirmed before reading after an ER regeneration
                                        if getattr(self, "_er_required", False) and not getattr(self, "_er_confirmed", False):
                                            return {
                                                "success": False,
                                                "status": False,
                                                "message": "Guard: ER change detected. Please confirm the ER diagram before reading data.",
                                                "error": "GUARD_ER_CONFIRM_REQUIRED",
                                                "files": [],
                                            }
                                        out = await _orig_exec_sql(*a, **kw)
                                        setattr(self, "_observed_db_read", True)
                                        return out

                                    ms.exec_sql = _exec_sql_guarded  # type: ignore

                                if hasattr(ms, "export_query_tsv"):
                                    _orig_export = ms.export_query_tsv

                                    async def _export_guarded(*a, **kw):  # type: ignore
                                        out = await _orig_export(*a, **kw)
                                        setattr(self, "_observed_db_read", True)
                                        return out

                                    ms.export_query_tsv = _export_guarded  # type: ignore

                                if hasattr(ms, "generate_er_mermaid"):
                                    _orig_er = ms.generate_er_mermaid

                                    async def _er_guarded(*a, **kw):  # type: ignore
                                        setattr(self, "_observed_db_read", False)
                                        # ER changed, require confirmation before reads
                                        setattr(self, "_er_required", True)
                                        setattr(self, "_er_confirmed", False)
                                        return await _orig_er(*a, **kw)

                                    ms.generate_er_mermaid = _er_guarded  # type: ignore

                                if hasattr(ms, "set_active_tables"):
                                    _orig_sat = ms.set_active_tables

                                    async def _sat_guarded(*a, **kw):  # type: ignore
                                        setattr(self, "_observed_db_read", False)
                                        setattr(self, "_er_required", True)
                                        setattr(self, "_er_confirmed", False)
                                        return await _orig_sat(*a, **kw)

                                    ms.set_active_tables = _sat_guarded  # type: ignore

                            pexe = ui_map.get("python_executor")
                            if pexe is not None and hasattr(pexe, "execute_python_code"):
                                _orig_exec_py = pexe.execute_python_code

                                async def _exec_py_guarded(code: str, timeout: int = 30):  # type: ignore
                                    if getattr(self, "_er_required", False) and not getattr(self, "_er_confirmed", False):
                                        return {
                                            "success": False,
                                            "status": False,
                                            "message": "Guard: ER change detected. Please confirm the ER diagram before executing Python.",
                                            "error": "GUARD_ER_CONFIRM_REQUIRED",
                                            "files": [],
                                        }
                                    if not getattr(self, "_observed_db_read", False):
                                        return {
                                            "success": False,
                                            "status": False,
                                            "message": "Guard: A real DB read is required before executing Python. Please call mysql_schema.exec_sql or export_query_tsv first.",
                                            "error": "GUARD_DB_REQUIRED",
                                            "files": [],
                                        }
                                    return await _orig_exec_py(code, timeout=timeout)

                                pexe.execute_python_code = _exec_py_guarded  # type: ignore
                        except Exception:
                            pass

                    worker.build = _patched_worker_build.__get__(worker, worker.__class__)
        except Exception:
            pass

    WebSocketHandler.instantiate_agent = _patched_instantiate  # type: ignore

    # Optional: Patch tool output to show Mermaid code blocks directly as text for rendering
    _orig_handle_tool_output = ui_common.handle_tool_call_output

    async def _patched_handle_tool_output(event):  # type: ignore
        ui_event = await _orig_handle_tool_output(event)
        try:
            item = event.item
            out = getattr(item, "output", None)
            if isinstance(out, str) and ("erDiagram" in out or out.strip().startswith("```mermaid")):
                return Event(type="raw", data=TextDeltaContent(type="text", delta=out, inprogress=False))
            if isinstance(out, dict):
                blob = "\n\n".join(
                    [str(out.get(k)) for k in ("message", "stdout", "output", "error") if isinstance(out.get(k), str)]
                ).strip()
                if blob.startswith("```mermaid") or "erDiagram" in blob:
                    return Event(type="raw", data=TextDeltaContent(type="text", delta=blob, inprogress=False))
            return ui_event
        except Exception:
            return ui_event

    ui_common.handle_tool_call_output = _patched_handle_tool_output  # type: ignore
    import utu.ui.webui_agents as ui_web_module
    ui_web_module.handle_tool_call_output = _patched_handle_tool_output  # type: ignore

    # Patch ask_user to detect ER confirmation answers and flip flags
    _orig_ask_user = WebSocketHandler.ask_user

    async def _patched_ask_user(self, question: str):  # type: ignore
        # remember the question to detect ER confirmation replies
        setattr(self, "_last_ask_question", question or "")
        answer = await _orig_ask_user(self, question)
        try:
            q = getattr(self, "_last_ask_question", "")
            if any(key in q for key in ["ER", "ER图", "ER diagram", "确认ER", "关系图"]):
                # any answer toggles confirmation; user may request modification in next round
                setattr(self, "_er_required", False)
                setattr(self, "_er_confirmed", True)
        except Exception:
            pass
        return answer

    WebSocketHandler.ask_user = _patched_ask_user  # type: ignore

    default_config = os.environ.get("UTU_DEFAULT_CONFIG", "generated/mysql_bi_clean.yaml")
    webui = WebUIAgents(default_config=default_config)
    # Bind to all interfaces so remote clients can access
    webui.launch(ip="0.0.0.0", port=8848)


if __name__ == "__main__":
    main()
