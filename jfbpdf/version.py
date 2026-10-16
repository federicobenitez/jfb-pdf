"""Metadatos centralizados de la aplicación JFB Pdf.

Toda la información de identidad (nombre, versión, autor, email y descripción)
vive aquí para evitar duplicarla entre el diálogo "Acerca De", la ventana
principal y el empaquetado .deb.
"""

APP_NAME = "JFB Pdf"
APP_ID = "jfbpdf"
VERSION = "1.1.1"

AUTHOR_NAME = "Federico Benitez"
AUTHOR_EMAIL = "benitez.federico@gmail.com"

DESCRIPTION = (
    "Lector de PDF ligero y rápido para Debian.\n"
    "Visualización con desplazamiento suave, zoom y navegación por páginas, "
    "desarrollado en Python con PySide6 y PyMuPDF."
)

HOMEPAGE = ""

__all__ = [
    "APP_NAME",
    "APP_ID",
    "VERSION",
    "AUTHOR_NAME",
    "AUTHOR_EMAIL",
    "DESCRIPTION",
    "HOMEPAGE",
]
