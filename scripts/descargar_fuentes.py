"""Descarga los documentos públicos de la USFQ a datos/publico/ (no se versionan).

Uso:  python scripts/descargar_fuentes.py [--listar]

Solo baja lo que está en `asistente.fuentes.FUENTES`, solo desde www.usfq.edu.ec y
sin credenciales. Si la universidad publica una versión nueva, se actualiza allí
y en docs/fuentes.md.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

from asistente.fuentes import DIR_PUBLICO, FUENTES, HOST_PERMITIDO, Fuente


def descargar(fuente: Fuente) -> str:
    """Baja una fuente y devuelve una línea de resumen. Lanza ValueError si no es un PDF."""
    if urlparse(fuente.url).hostname != HOST_PERMITIDO:
        raise ValueError(f"host no permitido: {fuente.url}")
    peticion = urllib.request.Request(fuente.url, headers={"User-Agent": "asistente-academico/0.1"})
    with urllib.request.urlopen(peticion, timeout=60) as respuesta:
        contenido: bytes = respuesta.read()
    if not contenido.startswith(b"%PDF"):
        raise ValueError("la respuesta no es un PDF")
    DIR_PUBLICO.mkdir(parents=True, exist_ok=True)
    fuente.ruta.write_bytes(contenido)
    sha = hashlib.sha256(contenido).hexdigest()
    return f"{fuente.archivo}  {len(contenido) / 1024:.0f} KB  sha256={sha[:16]}…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listar", action="store_true", help="muestra las fuentes sin descargar")
    args = parser.parse_args(argv)

    fallos = 0
    for fuente in FUENTES:
        if args.listar:
            print(f"{fuente.archivo}: {fuente.titulo}\n  {fuente.url}")
            continue
        try:
            print("ok     " + descargar(fuente))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            fallos += 1
            print(f"FALLÓ  {fuente.archivo}: {exc}", file=sys.stderr)
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
