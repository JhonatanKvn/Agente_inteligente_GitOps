import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = Any  # type: ignore

from memory import save_message
from prompts import SYSTEM_PROMPT
from tools import run_tool


TRACE_PATH = Path("logs/trace.jsonl")


def append_trace(event: Dict[str, Any]) -> None:
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


class IntelligentAgent:
    def __init__(self, client: OpenAI, model: str) -> None:
        self.client = client
        self.model = model

    def _extract_text(self, response: Any) -> str:
        chunks: List[str] = []
        for item in response.output:
            if item.type == "message":
                for c in item.content:
                    if c.type == "output_text":
                        chunks.append(c.text)
        return "\n".join(chunks).strip()

    def chat(self, user_text: str) -> str:
        save_message("user", user_text)
        append_trace({"event": "user", "text": user_text})

        inputs: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]

        while True:
            response = self.client.responses.create(
                model=self.model,
                input=inputs,
                tools=TOOLS_SCHEMA,
            )

            append_trace({"event": "model_output", "raw": response.model_dump(mode="json")})

            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                final_text = self._extract_text(response)
                save_message("assistant", final_text)
                append_trace({"event": "assistant", "text": final_text})
                return final_text

            for call in function_calls:
                tool_name = call.name
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                tool_result = run_tool(tool_name, args)
                append_trace(
                    {
                        "event": "tool_call",
                        "tool_name": tool_name,
                        "args": args,
                        "result": tool_result,
                    }
                )

                inputs.extend(
                    [
                        {
                            "type": "function_call",
                            "call_id": call.call_id,
                            "name": tool_name,
                            "arguments": call.arguments,
                        },
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": tool_result,
                        },
                    ]
                )


class OfflineAgent:
    def __init__(self) -> None:
        self.system_prompt = SYSTEM_PROMPT

    def _format_todos(self, raw_json: str) -> str:
        try:
            data = json.loads(raw_json)
            items = data.get("items", [])
        except Exception:
            return "No pude leer las tareas."

        if not items:
            return "No tienes tareas registradas."

        lines = ["Estas son tus tareas:"]
        for it in items:
            state = "hecha" if it.get("done") else "pendiente"
            lines.append(f"- [{it.get('id')}] {it.get('task')} ({state})")
        return "\n".join(lines)

    def _format_memory(self, raw_json: str) -> str:
        try:
            data = json.loads(raw_json)
            items = data.get("matches", [])
        except Exception:
            return "No pude consultar la memoria."

        if not items:
            return "No encontré coincidencias en memoria."

        lines = ["Encontré esto en la memoria:"]
        for it in items:
            role = it.get("role", "desconocido")
            content = str(it.get("content", "")).strip()
            lines.append(f"- ({role}) {content}")
        return "\n".join(lines)

    def chat(self, user_text: str) -> str:
        text = user_text.strip()
        lower = text.lower()
        save_message("user", text)
        append_trace({"event": "user", "text": text, "mode": "offline"})

        if "lista mis tareas" in lower or ("listar" in lower and "tarea" in lower):
            out = run_tool("list_todos", {"limit": 20})
            answer = self._format_todos(out)
        elif (
            "agrega tarea" in lower
            or "agregar tarea" in lower
            or "anota tarea" in lower
            or "organiza mi tesis" in lower
        ):
            task = text
            for prefix in ["agrega tarea", "agregar tarea", "anota tarea"]:
                if lower.startswith(prefix):
                    task = text[len(prefix) :].strip(" :.-")
                    break
            if not task:
                task = "Tarea nueva de tesis"
            out = run_tool("add_todo", {"task": task})
            try:
                data = json.loads(out)
                answer = f"Tarea registrada con id {data.get('todo_id')}."
            except Exception:
                answer = "Registré tu tarea."
        elif "recuerd" in lower or "memoria" in lower:
            query = text
            out = run_tool("search_memory", {"query": query, "limit": 5})
            answer = self._format_memory(out)
        else:
            answer = (
                "Estoy en modo gratuito offline. Puedo: agregar tareas, listarlas y buscar memoria. "
                "Ejemplos: 'agrega tarea revisar marco teorico', 'lista mis tareas', 'recuerdame ...'."
            )

        save_message("assistant", answer)
        append_trace({"event": "assistant", "text": answer, "mode": "offline"})
        return answer
