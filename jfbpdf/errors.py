"""Excepciones propias de JFB Pdf.

Permiten distinguir los fallos esperables al abrir un documento (contraseña
requerida o incorrecta) de otros errores, sin depender del texto del mensaje.
"""

from __future__ import annotations


class DocumentError(Exception):
    """Error genérico al abrir o procesar un documento."""


class PasswordRequiredError(DocumentError):
    """El documento está protegido y hace falta una contraseña."""

    def __init__(self) -> None:
        super().__init__("El documento está protegido con contraseña.")


class InvalidPasswordError(DocumentError):
    """La contraseña proporcionada no es correcta."""

    def __init__(self) -> None:
        super().__init__("La contraseña no es correcta.")


__all__ = ["DocumentError", "PasswordRequiredError", "InvalidPasswordError"]
