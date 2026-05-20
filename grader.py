"""Compatibilidad: reexporta servicios de evaluacion."""

from app.services.grading import EvaluationResult, evaluate_demo, evaluate_with_ocr_space

__all__ = ["EvaluationResult", "evaluate_demo", "evaluate_with_ocr_space"]

