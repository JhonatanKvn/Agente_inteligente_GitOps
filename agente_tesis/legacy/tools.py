import json
from typing import Any, Dict, List

from memory import add_todo, list_todos, search_messages


TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "add_todo",
        "description": "Agrega una tarea a la lista de pendientes.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Descripción corta de la tarea.",
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_todos",
        "description": "Lista tareas pendientes o registradas.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Número máximo de tareas a devolver.",
                    "default": 20,
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_memory",
        "description": "Busca mensajes previos del historial.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Texto a buscar en la memoria.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de coincidencias.",
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def run_tool(name: str, args: Dict[str, Any]) -> str:
    if name == "add_todo":
        task = str(args.get("task", "")).strip()
        if not task:
            return json.dumps({"ok": False, "error": "task vacio"})
        todo_id = add_todo(task)
        return json.dumps({"ok": True, "todo_id": todo_id, "task": task}, ensure_ascii=False)

    if name == "list_todos":
        limit = int(args.get("limit", 20))
        items = list_todos(limit=limit)
        data = [
            {"id": row[0], "task": row[1], "done": bool(row[2]), "created_at": row[3]}
            for row in items
        ]
        return json.dumps({"ok": True, "items": data}, ensure_ascii=False)

    if name == "search_memory":
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"ok": False, "error": "query vacia"})
        limit = int(args.get("limit", 5))
        rows = search_messages(query=query, limit=limit)
        data = [
            {"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]}
            for r in rows
        ]
        return json.dumps({"ok": True, "matches": data}, ensure_ascii=False)

    return json.dumps({"ok": False, "error": f"tool desconocida: {name}"}, ensure_ascii=False)
