"""Registro de documentos públicos de la USFQ. Detalle y estado en docs/fuentes.md."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NamedTuple

from asistente.db import RAIZ

DIR_PUBLICO = RAIZ / "datos" / "publico"
HOST_PERMITIDO = "www.usfq.edu.ec"
_BASE = f"https://{HOST_PERMITIDO}/sites/default/files"


class Fuente(NamedTuple):
    archivo: str
    titulo: str
    url: str
    # manual y calendario se fragmentan para el RAG; un diagrama no tiene texto corrido.
    tipo: Literal["manual", "calendario", "diagrama"]

    @property
    def ruta(self) -> Path:
        return DIR_PUBLICO / self.archivo


FUENTES = (
    Fuente(
        "manual_estudiante_grado.pdf",
        "Manual del Estudiante de Grado (actualizado a abril de 2023)",
        f"{_BASE}/2023-04/Manual_del_Estudiante_de_Grado_abril_18_0.pdf",
        "manual",
    ),
    Fuente(
        "calendario_202610.pdf",
        "Calendario académico, primer semestre 2026-2027",
        f"{_BASE}/2026-08/calendario_academico_usfq_primer_semestre_2026-2027_202610.pdf",
        "calendario",
    ),
    Fuente(
        "calendario_202620.pdf",
        "Calendario académico, segundo semestre 2026-2027",
        f"{_BASE}/2026-07/calendario_academico_usfq_segundo_semestre_2026-2027_202620.pdf",
        "calendario",
    ),
    Fuente(
        "calendario_202630.pdf",
        "Calendario académico, verano 2026-2027",
        f"{_BASE}/2026-07/calendario_academico_usfq_verano_2026-2027_202630.pdf",
        "calendario",
    ),
    Fuente(
        "malla_bloques_cmp.pdf",
        "Malla de bloques, Ingeniería en Ciencias de la Computación (mayo 2026)",
        f"{_BASE}/2026-05/malla_de_bloques_ingenieria_en_ciencias_de_la_computacion_06052026.pdf",
        "diagrama",
    ),
)
