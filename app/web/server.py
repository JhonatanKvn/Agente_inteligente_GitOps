import csv
import io
import json
import os
import threading
import webbrowser
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for
from PIL import UnidentifiedImageError

from app.db.repository import (
    delete_student,
    get_student,
    init_db,
    list_evaluations,
    list_latest_evaluations,
    list_student_evaluations,
    list_student_alerts,
    list_student_summary,
    list_students,
    save_evaluation,
    save_student,
)
from app.services.grading import evaluate_with_ocr_space
from app.services.image_processing import prepare_image_for_ocr


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_COURSES = ["Algoritmia y Programacion 1", "Algoritmia y Programacion 2"]

load_dotenv()
init_db()

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret")


def _to_result_dict(result: Any) -> Dict[str, Any]:
    return {
        "score": result.score,
        "max_score": result.max_score,
        "feedback": result.feedback,
        "code_transcription": result.code_transcription,
        "strengths": result.strengths or [],
        "improvements": result.improvements or [],
        "rubric_breakdown": result.rubric_breakdown or [],
    }


def _load_json_list(value: str) -> list:
    try:
        loaded = json.loads(value or "[]")
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def _normalize_header(value: str) -> str:
    value = value.strip().lower()
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _looks_like_code(value: str) -> bool:
    compact = value.replace("-", "").replace(".", "").strip()
    return compact.isdigit() and len(compact) >= 2


def _row_to_student(row: list[str], code_index: Optional[int], name_index: Optional[int]) -> Optional[Dict[str, str]]:
    values = [cell.strip() for cell in row if cell and cell.strip()]
    if len(values) < 2:
        return None

    if code_index is not None and name_index is not None:
        if code_index >= len(row) or name_index >= len(row):
            return None
        student_code = row[code_index].strip()
        student_name = row[name_index].strip()
    else:
        code_candidates = [cell for cell in values if _looks_like_code(cell)]
        name_candidates = [cell for cell in values if not _looks_like_code(cell)]
        if not code_candidates or not name_candidates:
            student_code, student_name = values[0], values[1]
        else:
            student_code, student_name = code_candidates[0], name_candidates[0]

    if not student_code or not student_name:
        return None
    return {"student_code": student_code, "student_name": student_name}


def _extract_students_from_rows(rows: list[list[str]]) -> list[Dict[str, str]]:
    clean_rows = [[str(cell or "").strip() for cell in row] for row in rows if any(str(cell or "").strip() for cell in row)]
    if not clean_rows:
        return []

    first_row = [_normalize_header(cell) for cell in clean_rows[0]]
    code_headers = {"codigo", "cod", "code", "id", "identificacion", "documento"}
    name_headers = {"nombre", "nombres", "estudiante", "alumno", "student", "name"}
    code_index = next((i for i, cell in enumerate(first_row) if cell in code_headers or "codigo" in cell), None)
    name_index = next((i for i, cell in enumerate(first_row) if cell in name_headers or "nombre" in cell), None)
    has_header = code_index is not None and name_index is not None

    data_rows = clean_rows[1:] if has_header else clean_rows
    students = []
    seen = set()
    for row in data_rows:
        student = _row_to_student(row, code_index, name_index)
        if not student:
            continue
        key = student["student_code"]
        if key in seen:
            continue
        seen.add(key)
        students.append(student)
    return students


def _parse_delimited_students(content: str) -> list[Dict[str, str]]:
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if ";" in sample else ","
    rows = list(csv.reader(io.StringIO(content), dialect))
    return _extract_students_from_rows(rows)


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value = cell.find("main:v", namespace)
    inline = cell.find("main:is/main:t", namespace)
    if inline is not None and inline.text:
        return inline.text
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return value.text


def _parse_xlsx_students(file_bytes: bytes) -> list[Dict[str, str]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as workbook:
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(text.itertext()).strip()
                for text in shared_root.findall(".//main:si", namespace)
            ]

        sheet_name = next((name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")), "")
        if not sheet_name:
            return []
        sheet_root = ElementTree.fromstring(workbook.read(sheet_name))
        rows = []
        for row in sheet_root.findall(".//main:sheetData/main:row", namespace):
            rows.append([_xlsx_cell_value(cell, shared_strings, namespace) for cell in row.findall("main:c", namespace)])
    return _extract_students_from_rows(rows)


def _parse_students_file(file_bytes: bytes, filename: str) -> list[Dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        return _parse_xlsx_students(file_bytes)
    content = file_bytes.decode("utf-8-sig", errors="ignore")
    return _parse_delimited_students(content)


@app.get("/")
def index():
    return redirect(url_for("students_page"))


@app.get("/students")
def students_page():
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    message = request.args.get("message", "").strip()
    return render_template(
        "students.html",
        message=message,
        allowed_courses=ALLOWED_COURSES,
        students=list_students(course_name=course_filter, query=student_filter),
        course_filter=course_filter,
        student_filter=student_filter,
    )


@app.get("/evaluate")
def evaluate_page():
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    students = list_students(course_name=course_filter, query=student_filter)
    return render_template(
        "evaluate.html",
        error=request.args.get("error", "").strip(),
        students=students,
        allowed_courses=ALLOWED_COURSES,
        course_filter=course_filter,
        student_filter=student_filter,
        selected_student_id="",
        activity_name="Actividad 1",
        activity_type="Taller",
        semester="2026-1",
        max_score=5.0,
        rubric_text=(
            "Criterio: Logica del algoritmo (40%)\n"
            "Criterio: Sintaxis y estructura en Python (30%)\n"
            "Criterio: Buenas practicas y legibilidad (30%)\n"
        ),
    )


@app.get("/history")
def history_page():
    filter_name = request.args.get("filter_name", "").strip()
    course_filter = request.args.get("course_filter", "").strip()
    history = list_evaluations(student_name=filter_name, course_name=course_filter, limit=50)
    summary = list_student_summary(student_name=filter_name, course_name=course_filter, limit=50)
    alerts = list_student_alerts(student_name=filter_name, course_name=course_filter, limit=50)
    return render_template(
        "history.html",
        courses=[{"course_name": course, "course_description": "", "students": 0} for course in ALLOWED_COURSES],
        history=history,
        summary=summary,
        alerts=alerts,
        total_evaluations=len(history),
        total_students=len(summary),
        filter_name=filter_name,
        course_filter=course_filter,
    )


@app.get("/report")
def report_page():
    selected_student_id = request.args.get("student_id", "").strip()
    course_filter = request.args.get("course_filter", "").strip()
    student_filter = request.args.get("student_filter", "").strip()
    message = request.args.get("message", "").strip()
    students = list_students(course_name=course_filter, query=student_filter)
    student = get_student(int(selected_student_id)) if selected_student_id else None
    evaluations = []
    latest = None
    first = None
    comparison = None

    if student:
        evaluations = list_student_evaluations(student["student_code"], student["course_name"], limit=100)
        if evaluations:
            first = evaluations[0]
            latest = evaluations[-1]
            latest["strengths"] = _load_json_list(latest.get("strengths_json", "[]"))
            latest["improvements"] = _load_json_list(latest.get("improvements_json", "[]"))
            latest["rubric_breakdown"] = _load_json_list(latest.get("rubric_breakdown_json", "[]"))
            if len(evaluations) == 1:
                comparison = {
                    "status": "first",
                    "text": "Primera evaluacion registrada. Aun no hay comparativas de rendimiento.",
                }
            else:
                delta = round(float(latest["score"]) - float(first["score"]), 2)
                comparison = {
                    "status": "comparison",
                    "delta": delta,
                    "text": f"Comparacion entre Evaluacion 1 y la evaluacion mas reciente: variacion de {delta:.2f} puntos.",
                }

    return render_template(
        "report.html",
        message=message,
        students=students,
        allowed_courses=ALLOWED_COURSES,
        selected_student_id=selected_student_id,
        course_filter=course_filter,
        student_filter=student_filter,
        student=student,
        evaluations=evaluations,
        latest=latest,
        first=first,
        comparison=comparison,
        latest_evaluations=list_latest_evaluations(limit=10),
    )


@app.post("/students")
def create_student():
    student_name = request.form.get("student_name", "").strip()
    student_code = request.form.get("student_code", "").strip()
    course_name = request.form.get("course_name", "").strip()
    course_description = request.form.get("course_description", "").strip()

    if not student_name or not student_code or not course_name:
        return redirect(url_for("students_page", message="Completa nombre, codigo y curso del estudiante."))
    if course_name not in ALLOWED_COURSES:
        return redirect(url_for("students_page", message="Curso no permitido. Usa Algoritmia y Programacion 1 o 2."))

    save_student(
        {
            "student_name": student_name,
            "student_code": student_code,
            "course_name": course_name,
            "course_description": course_description,
        }
    )
    return redirect(url_for("students_page", course_filter=course_name, message="Estudiante guardado."))


@app.post("/students/import")
def import_students():
    course_name = request.form.get("course_name", "").strip()
    course_description = request.form.get("course_description", "").strip()
    uploaded = request.files.get("students_file")

    if course_name not in ALLOWED_COURSES:
        return redirect(url_for("students_page", message="Selecciona un curso permitido para importar."))
    if not uploaded or not uploaded.filename:
        return redirect(url_for("students_page", course_filter=course_name, message="Debes subir un archivo de estudiantes."))

    supported_formats = {".txt", ".csv", ".tsv", ".xlsx"}
    file_suffix = Path(uploaded.filename).suffix.lower()
    if file_suffix not in supported_formats:
        return redirect(
            url_for(
                "students_page",
                course_filter=course_name,
                message="Formato no soportado. Usa TXT, CSV, TSV o XLSX.",
            )
        )

    parsed_students = _parse_students_file(uploaded.read(), uploaded.filename)
    if not parsed_students:
        return redirect(
            url_for(
                "students_page",
                course_filter=course_name,
                message="No se encontraron nombres y codigos en el archivo.",
            )
        )
    imported = 0
    for student in parsed_students:
        save_student(
            {
                "student_name": student["student_name"],
                "student_code": student["student_code"],
                "course_name": course_name,
                "course_description": course_description,
            }
        )
        imported += 1

    return redirect(url_for("students_page", course_filter=course_name, message=f"Importados {imported} estudiantes."))


@app.post("/students/<int:student_id>/delete")
def remove_student(student_id: int):
    course_filter = request.form.get("course_filter", "").strip()
    student_filter = request.form.get("student_filter", "").strip()
    deleted = delete_student(student_id)
    if not deleted:
        message = "No se encontro el estudiante para borrar."
    else:
        message = f"Estudiante {deleted['student_name']} eliminado del listado. Sus evaluaciones historicas se conservan."
        course_filter = course_filter or deleted["course_name"]
    return redirect(
        url_for(
            "students_page",
            course_filter=course_filter,
            student_filter=student_filter,
            message=message,
        )
    )


@app.post("/evaluate")
def evaluate():
    selected_student_id = request.form.get("student_id", "").strip()
    activity_name = request.form.get("activity_name", "").strip()
    activity_type = request.form.get("activity_type", "").strip()
    semester = request.form.get("semester", "").strip()
    rubric_text = request.form.get("rubric_text", "").strip()
    course_filter = request.form.get("course_filter", "").strip()

    try:
        max_score = float(request.form.get("max_score", "5"))
    except ValueError:
        max_score = 5.0

    uploaded = request.files.get("code_image")
    error: Optional[str] = None
    result = None
    eval_id = None
    selected_student = None

    if not selected_student_id:
        error = "Debes seleccionar un estudiante."
    else:
        selected_student = get_student(int(selected_student_id))
        if not selected_student:
            error = "El estudiante seleccionado no existe."

    if not error and not activity_name:
        error = "Debes escribir el nombre de la evaluacion."
    elif not error and not activity_type:
        error = "Debes escribir el tipo de actividad."
    elif not activity_name:
        error = "Debes escribir el nombre de la evaluacion."
    elif not activity_type:
        error = "Debes escribir el tipo de actividad."
    elif not semester:
        error = "Debes escribir el semestre (ej: 2026-1)."
    elif not rubric_text:
        error = "Debes escribir las rubricas de evaluacion."
    elif not uploaded or not uploaded.filename:
        error = "Debes subir una imagen del codigo."

    if not error:
        try:
            api_key = os.getenv("OCRSPACE_API_KEY", "helloworld").strip()
            processed_bytes, processed_name = prepare_image_for_ocr(uploaded.read(), uploaded.filename)
            raw_result = evaluate_with_ocr_space(
                api_key=api_key,
                rubric_text=rubric_text,
                image_bytes=processed_bytes,
                filename=processed_name,
                max_score=max_score,
            )
            result = _to_result_dict(raw_result)
            eval_id = save_evaluation(
                {
                    "student_name": selected_student["student_name"],
                    "student_code": selected_student["student_code"],
                    "activity_name": activity_name,
                    "activity_type": activity_type,
                    "semester": semester,
                    "course_name": selected_student["course_name"],
                    "course_description": selected_student["course_description"],
                    "mode": "ocrspace",
                    "score": result["score"],
                    "max_score": result["max_score"],
                    "feedback": result["feedback"],
                    "code_transcription": result["code_transcription"],
                    "strengths_json": json.dumps(result["strengths"], ensure_ascii=False),
                    "improvements_json": json.dumps(result["improvements"], ensure_ascii=False),
                    "rubric_breakdown_json": json.dumps(result["rubric_breakdown"], ensure_ascii=False),
                    "rubric_text": rubric_text,
                    "image_filename": processed_name,
                }
            )
            return redirect(
                url_for(
                    "report_page",
                    student_id=selected_student_id,
                    message=f"Evaluacion guardada con ID {eval_id}.",
                )
            )
        except UnidentifiedImageError:
            error = "El archivo no es una imagen valida. Sube JPG, JPEG, PNG o WEBP."
        except Exception as exc:
            error = f"No se pudo evaluar la entrega: {exc}"

    return render_template(
        "evaluate.html",
        max_score=max_score,
        selected_student_id=selected_student_id,
        activity_name=activity_name,
        activity_type=activity_type,
        semester=semester,
        rubric_text=rubric_text,
        error=error,
        students=list_students(course_name=course_filter),
        allowed_courses=ALLOWED_COURSES,
        course_filter=course_filter,
        student_filter="",
    )


@app.get("/new")
def new_evaluation():
    return redirect(url_for("evaluate_page"))


def run() -> None:
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip() == "1"
    port = int(os.getenv("APP_PORT", "5000"))
    auto_open = os.getenv("AUTO_OPEN_BROWSER", "1").strip() == "1"
    if auto_open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)
