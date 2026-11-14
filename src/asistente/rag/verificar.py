"""Verificación determinista de lo que propone el LLM. Si algo falla, no se muestra."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from asistente.rag.busqueda import Recuperado
from asistente.rag.texto import normalizar, numeros

MINIMO_CITA = 15  # caracteres normalizados; evita "citas" de una palabra suelta


class Cita(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fragmento_id: int
    texto: str = Field(min_length=1)


class Borrador(BaseModel):
    """Lo que el LLM debe devolver, como JSON."""

    model_config = ConfigDict(extra="forbid")
    abstenerse: bool
    respuesta: str = ""
    citas: list[Cita] = []


# Lo mismo que `Borrador`, escrito a mano en el subconjunto de JSON Schema que aceptan
# los proveedores para salida estructurada. Es una ayuda: quien manda es `Borrador`.
ESQUEMA_BORRADOR: dict[str, object] = {
    "type": "object",
    "properties": {
        "abstenerse": {"type": "boolean"},
        "respuesta": {"type": "string"},
        "citas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fragmento_id": {"type": "integer"},
                    "texto": {"type": "string"},
                },
                "required": ["fragmento_id", "texto"],
            },
        },
    },
    "required": ["abstenerse"],
}


def verificar(borrador: Borrador, recuperados: Sequence[Recuperado]) -> list[str]:
    """Problemas de una respuesta propuesta. Lista vacía = se puede mostrar.

    1. Tiene que haber al menos una cita.
    2. Cada cita apunta a un fragmento que de verdad se recuperó.
    3. El texto citado aparece literalmente en ese fragmento (tras normalizar).
    4. Toda cifra de la respuesta aparece en alguna cita.
    """
    if not borrador.respuesta.strip():
        return ["la respuesta está vacía"]
    if not borrador.citas:
        return ["la respuesta no trae ninguna cita"]

    problemas: list[str] = []
    por_id = {r.id: r for r in recuperados}
    cifras_citadas: set[str] = set()
    for cita in borrador.citas:
        fragmento = por_id.get(cita.fragmento_id)
        citado = normalizar(cita.texto)
        if fragmento is None:
            problemas.append(f"la cita apunta al fragmento {cita.fragmento_id}, que no se entregó")
        elif len(citado) < MINIMO_CITA:
            problemas.append(f"la cita «{cita.texto}» es demasiado corta para respaldar algo")
        elif citado not in normalizar(fragmento.texto):
            problemas.append(
                f"la cita «{cita.texto}» no aparece literalmente en el fragmento "
                f"{cita.fragmento_id}"
            )
        else:
            cifras_citadas |= numeros(cita.texto)

    sin_respaldo = sorted(numeros(borrador.respuesta) - cifras_citadas)
    if sin_respaldo:
        problemas.append(
            "cifras de la respuesta que no están en ninguna cita: " + ", ".join(sin_respaldo)
        )
    return problemas
