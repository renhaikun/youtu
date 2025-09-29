"""
Run the WebUIAgents server with a small runtime patch so OrchestraAgent works
without modifying the library code. This enables real mid-run user prompts via
`ask_user` (the UI will display an input and pause the agent until you answer).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any


def main() -> None:
    # Monkey-patch: add a no-op async build() to OrchestraAgent so WebUIAgents
    # can instantiate it without error. This does NOT change repository code.
    from utu.agents.orchestra_agent import OrchestraAgent
    from utu.agents.simple_agent import SimpleAgent as _SimpleAgent
    from agents import StopAtTools

    async def _noop_build(self: Any) -> None:  # type: ignore[no-redef]
        return None

    if not hasattr(OrchestraAgent, "build"):
        setattr(OrchestraAgent, "build", _noop_build)

    # We NO LONGER enforce StopAtTools("ask_user"). We want the `ask_user` tool to be executed
    # so it can emit a UI `ask` event and truly await the user's answer. The default behavior of
    # the SimpleAgent is to execute tools; we only patch how `ask_user` is wired and how messages
    # are handled in the WebSocket layer (see below).

    # Start WebUIAgents with our generated config
    from utu.ui.webui_agents import WebUIAgents, WebSocketHandler
    from utu.ui import common as ui_common
    from utu.ui.common import Event, TextDeltaContent

    # Patch WebSocketHandler.instantiate_agent to wire `ask_user` into user_interaction toolkits
    _orig_instantiate = WebSocketHandler.instantiate_agent

    async def _patched_instantiate(self, config):  # type: ignore[no-redef]
        await _orig_instantiate(self, config)
        try:
            # For SimpleAgent: inject ask function immediately if toolkit exists
            from utu.agents.simple_agent import SimpleAgent
            from utu.agents.orchestra_agent import OrchestraAgent

            if isinstance(self.agent, SimpleAgent):
                ui = getattr(self.agent, "_toolkits", {}).get("user_interaction")
                if ui and hasattr(ui, "set_ask_function"):
                    ui.set_ask_function(self.ask_user)

            # For OrchestraAgent: inject after each worker build() and set guards
            elif isinstance(self.agent, OrchestraAgent):
                workers = getattr(self.agent, "worker_agents", {}) or {}
                for worker in workers.values():
                    orig_build = worker.build  # bound method

                    async def _patched_worker_build(_self, __orig=orig_build):  # type: ignore
                        # call the captured original bound method for THIS worker
                        await __orig()
                        try:
                            # wire ask_user to UI
                            ui_map = getattr(_self.agent, "_toolkits", {})
                            uikit = ui_map.get("user_interaction")
                            if uikit and hasattr(uikit, "set_ask_function"):
                                uikit.set_ask_function(self.ask_user)

                            # Guard: require a real DB read (exec_sql/export_query_tsv) before any Python execution
                            if not hasattr(self, "_observed_db_read"):
                                setattr(self, "_observed_db_read", False)

                            ms = ui_map.get("mysql_schema")
                            if ms is not None:
                                if hasattr(ms, "exec_sql"):
                                    _orig_exec_sql = ms.exec_sql

                                    async def _exec_sql_guarded(*a, **kw):  # type: ignore
                                        # ER must be confirmed before reading after ER regeneration
                                        if getattr(self, "_er_required", False) and not getattr(self, "_er_confirmed", False):
                                            return {
                                                "success": False,
                                                "status": False,
                                                "message": "Guard: ER change detected. Please confirm the ER diagram before reading data.",
                                                "error": "GUARD_ER_CONFIRM_REQUIRED",
                                                "files": [],
                                            }
                                        # Generic guard only; do not enforce business-specific table names
                                        out = await _orig_exec_sql(*a, **kw)
                                        setattr(self, "_observed_db_read", True)
                                        return out

                                    ms.exec_sql = _exec_sql_guarded  # type: ignore

                                if hasattr(ms, "export_query_tsv"):
                                    _orig_export = ms.export_query_tsv

                                    async def _export_guarded(*a, **kw):  # type: ignore
                                        if getattr(self, "_er_required", False) and not getattr(self, "_er_confirmed", False):
                                            return {
                                                "success": False,
                                                "status": False,
                                                "message": "Guard: ER change detected. Please confirm the ER diagram before exporting data.",
                                                "error": "GUARD_ER_CONFIRM_REQUIRED",
                                                "files": [],
                                            }
                                        # Generic guard only; do not enforce business-specific table names
                                        out = await _orig_export(*a, **kw)
                                        setattr(self, "_observed_db_read", True)
                                        return out

                                    ms.export_query_tsv = _export_guarded  # type: ignore

                                # Reset guard when ER is regenerated or active tables change
                                if hasattr(ms, "generate_er_mermaid"):
                                    _orig_er = ms.generate_er_mermaid

                                    async def _er_guarded(*a, **kw):  # type: ignore
                                        setattr(self, "_observed_db_read", False)
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

                    # bind to instance to preserve `self`
                    worker.build = _patched_worker_build.__get__(worker, worker.__class__)
        except Exception:
            # best-effort wiring; ignore failures so UI can still start
            pass

    WebSocketHandler.instantiate_agent = _patched_instantiate  # type: ignore

    # Patch ask_user to record pending state so normal chat can be treated as an answer fallback
    _orig_ask_user = WebSocketHandler.ask_user

    async def _patched_ask_user(self, question: str):  # type: ignore
        from utu.ui.common import Event, AskContent
        import uuid as _uuid

        ask_id = str(_uuid.uuid4())
        # mark pending so `on_message` can route next chat to answer if user types in the main box
        setattr(self, "_pending_ask", True)
        setattr(self, "_pending_ask_id", ask_id)

        event_to_send = Event(
            type="ask",
            data=AskContent(type="ask", question=question, ask_id=ask_id),
        )
        await self.send_event(event_to_send)
        answer = await self.answer_queue.get()
        # clear state
        setattr(self, "_pending_ask", False)
        setattr(self, "_pending_ask_id", None)
        from utu.ui.common import UserAnswer
        assert isinstance(answer, UserAnswer) and answer.ask_id == ask_id
        return answer.answer

    WebSocketHandler.ask_user = _patched_ask_user  # type: ignore

    # Detect ER confirmation answers to flip flags for guards (wrap on top)
    _orig_ask_for_er = WebSocketHandler.ask_user

    async def _patched_ask_user_for_er(self, question: str):  # type: ignore
        setattr(self, "_last_ask_question", question or "")
        ans = await _orig_ask_for_er(question)
        try:
            q = getattr(self, "_last_ask_question", "")
            if any(key in q for key in ["ER", "ER图", "ER diagram", "确认ER", "关系图"]):
                setattr(self, "_er_required", False)
                setattr(self, "_er_confirmed", True)
        except Exception:
            pass
        return ans

    WebSocketHandler.ask_user = _patched_ask_user_for_er  # type: ignore

    # Patch handle_tool_call_output to render Mermaid/inline images directly as text so UI shows them
    _orig_handle_tool_output = ui_common.handle_tool_call_output

    async def _patched_handle_tool_output(event):  # type: ignore
        ui_event = await _orig_handle_tool_output(event)
        try:
            item = event.item
            out = getattr(item, "output", None)
            if isinstance(out, str) and ("erDiagram" in out or out.strip().startswith("```mermaid")):
                # send as plain text so SafeMarkdown renders it
                return Event(
                    type="raw",
                    data=TextDeltaContent(type="text", delta=out, inprogress=False),
                )
            # PythonExecutor returns dict; extract Mermaid blocks or inline base64 images
            if isinstance(out, dict):
                text_fields = []
                for k in ("message", "stdout", "output", "error"):
                    v = out.get(k)
                    if isinstance(v, str):
                        text_fields.append(v)
                blob = "\n\n".join(text_fields)
                if blob:
                    s = blob.strip()
                    if s.startswith("```mermaid") or "erDiagram" in s:
                        return Event(
                            type="raw",
                            data=TextDeltaContent(type="text", delta=s, inprogress=False),
                        )
                    if "data:image/png;base64," in s or "data:image/svg+xml;base64," in s:
                        return Event(
                            type="raw",
                            data=TextDeltaContent(type="text", delta=s, inprogress=False),
                        )
            return ui_event
        except Exception:
            return ui_event

    ui_common.handle_tool_call_output = _patched_handle_tool_output  # type: ignore
    # Also patch the reference used inside webui_agents module
    import utu.ui.webui_agents as ui_web_module
    ui_web_module.handle_tool_call_output = _patched_handle_tool_output  # type: ignore

    # Patch on_message to treat a normal chat as an answer when a pending ask exists
    _orig_on_message = WebSocketHandler.on_message

    async def _patched_on_message(self, message: str):  # type: ignore
        try:
            import json as _json
            data = _json.loads(message)
            # if a "query" arrives while there's a pending ask, convert it to an answer
            if getattr(self, "_pending_ask", False) and data.get("type") == "query":
                from utu.ui.common import UserAnswer
                # shape: {type:"query", content:{query:"..."}}
                answer_text = data.get("content", {}).get("query", "")
                # deliver as answer and return without starting a new run
                await self._handle_answer_noexcept(UserAnswer(type="answer", ask_id=getattr(self, "_pending_ask_id", ""), answer=answer_text))
                return
        except Exception:
            pass
        # fallback to original handler
        await _orig_on_message(self, message)

    WebSocketHandler.on_message = _patched_on_message  # type: ignore

    default_config = os.environ.get(
        "UTU_DEFAULT_CONFIG",
        "generated/mysql_bi_clean.yaml",
    )
    webui = WebUIAgents(default_config=default_config)
    # Use defaults: 127.0.0.1:8848
    webui.launch()


if __name__ == "__main__":
    main()
