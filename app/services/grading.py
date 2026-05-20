from dataclasses import dataclass
from typing import Any, Dict, List

import requests


@dataclass
class EvaluationResult:
    score: float
    max_score: float
    feedback: str
    code_transcription: str
    strengths: List[str]
    improvements: List[str]
    rubric_breakdown: List[Dict[str, Any]]


def _normalize_criteria(rubric_text: str) -> List[str]:
    lines = [ln.strip() for ln in rubric_text.splitlines() if ln.strip()]
    out: List[str] = []
    for ln in lines:
        cleaned = ln.replace("Criterio:", "").replace("criterio:", "").strip(" -")
        if cleaned:
            out.append(cleaned)
    return out or ["Logica", "Sintaxis", "Buenas practicas"]


def _rule_based_eval(transcription: str, rubric_text: str, max_score: float) -> EvaluationResult:
    code = (transcription or "").strip()
    criteria = _normalize_criteria(rubric_text)

    low = code.lower()
    has_logic = any(k in low for k in ["if", "for", "while", "return"])
    has_function = "def " in low or "function " in low
    has_indent = "\n " in code or "\n\t" in code
    has_pairs = code.count("(") == code.count(")") and code.count("[") == code.count("]")

    factors: List[float] = []
    for c in criteria:
        c_low = c.lower()
        if "log" in c_low or "algorit" in c_low:
            f = 0.6 + (0.2 if has_logic else 0) + (0.1 if has_function else 0)
        elif "sintax" in c_low or "estructura" in c_low:
            f = 0.55 + (0.2 if has_pairs else 0) + (0.1 if has_indent else 0)
        elif "practica" in c_low or "legib" in c_low or "estilo" in c_low:
            f = 0.5 + (0.15 if has_indent else 0) + (0.1 if has_function else 0)
        else:
            f = 0.5 + (0.1 if has_logic else 0) + (0.1 if has_pairs else 0)
        factors.append(max(0.2, min(0.95, f)))

    per_max = max_score / max(len(criteria), 1)
    breakdown: List[Dict[str, Any]] = []
    total = 0.0
    for i, c in enumerate(criteria):
        s = round(per_max * factors[i], 2)
        total += s
        comment = "Buen nivel." if factors[i] >= 0.75 else "Nivel aceptable, con mejoras pendientes."
        breakdown.append({"criterion": c, "score": s, "max": round(per_max, 2), "comment": comment})

    score = round(min(max_score, total), 2)

    strengths: List[str] = []
    improvements: List[str] = []
    if has_logic:
        strengths.append("Se observa logica de programacion en el desarrollo.")
    if has_function:
        strengths.append("Incluye definicion de funciones.")
    if has_pairs:
        strengths.append("Estructura de parentesis/corchetes generalmente consistente.")
    if not has_indent:
        improvements.append("Mejorar indentacion y orden visual del codigo.")
    if not has_pairs:
        improvements.append("Revisar cierre de parentesis y corchetes.")
    if not has_logic:
        improvements.append("Agregar estructuras de control (if/for/while) cuando aplique.")

    strengths = strengths or ["Se identifica intento de estructura de solucion."]
    improvements = improvements or ["Agregar casos de prueba y comentarios cortos para mayor claridad."]

    feedback = (
        f"Nota estimada {score}/{max_score}. Evaluacion automatica basada en OCR y rubrica textual. "
        "Te recomiendo revisar legibilidad, sintaxis y validaciones de entrada."
    )
    return EvaluationResult(
        score=score,
        max_score=max_score,
        feedback=feedback,
        code_transcription=code or "No se pudo transcribir texto legible desde la imagen.",
        strengths=strengths,
        improvements=improvements,
        rubric_breakdown=breakdown,
    )


def evaluate_with_ocr_space(
    *, api_key: str, rubric_text: str, image_bytes: bytes, filename: str, max_score: float
) -> EvaluationResult:
    url = "https://api.ocr.space/parse/image"
    files = {"file": (filename, image_bytes)}
    data = {"language": "eng", "isOverlayRequired": "false", "OCREngine": "2", "scale": "true"}

    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.post(url, headers={"apikey": api_key}, files=files, data=data, timeout=90)
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "No se pudo conectar con OCR.Space. Revisa tu conexion a internet "
            "o intenta nuevamente en unos segundos."
        ) from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"Error OCR.Space ({resp.status_code}): {resp.text}")

    payload = resp.json()
    if payload.get("IsErroredOnProcessing"):
        msgs = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "Fallo de OCR.Space"
        raise RuntimeError(f"OCR.Space no pudo procesar la imagen: {msgs}")

    parsed = payload.get("ParsedResults", [])
    text = str(parsed[0].get("ParsedText", "")).strip() if parsed else ""
    return _rule_based_eval(text, rubric_text, max_score)


def evaluate_demo(*, rubric_text: str, max_score: float) -> EvaluationResult:
    _ = rubric_text
    score = round(max_score * 0.72, 2)
    return EvaluationResult(
        score=score,
        max_score=max_score,
        feedback=(
            "La solucion tiene una estructura funcional, pero requiere mejorar manejo de errores "
            "y claridad en nombres de variables."
        ),
        code_transcription="def suma(a,b):\n  return a+b\n\nprint(suma(2,3))",
        strengths=[
            "La logica principal del algoritmo esta presente.",
            "Uso correcto de funcion simple y retorno.",
        ],
        improvements=[
            "Agregar validacion de tipos de entrada.",
            "Mejorar indentacion y nombres de variables.",
            "Incluir casos de prueba adicionales.",
        ],
        rubric_breakdown=[
            {
                "criterion": "Logica",
                "score": round(max_score * 0.32, 2),
                "max": round(max_score * 0.4, 2),
                "comment": "Cumple parcialmente.",
            },
            {
                "criterion": "Sintaxis",
                "score": round(max_score * 0.2, 2),
                "max": round(max_score * 0.3, 2),
                "comment": "Presenta detalles menores.",
            },
            {
                "criterion": "Buenas practicas",
                "score": round(max_score * 0.2, 2),
                "max": round(max_score * 0.3, 2),
                "comment": "Puede mejorar legibilidad.",
            },
        ],
    )
