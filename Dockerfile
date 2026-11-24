# Imagen para correr la ETL y la ingesta en una máquina limpia.
# No lleva .env, base de datos ni PDF: la base se genera y los PDF públicos se descargan al correr.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instalación editable: el código ubica sql/ y datos/ relativos a la raíz del repo.
COPY pyproject.toml ./
COPY src/ src/
COPY sql/ sql/
COPY scripts/ scripts/
COPY datos/ejemplo/ datos/ejemplo/
COPY datos/malla_prerrequisitos.csv datos/
RUN pip install -e . \
    && useradd --create-home asistente \
    && mkdir -p datos/local datos/publico \
    && chown -R asistente datos

USER asistente
CMD ["python", "-m", "asistente.etl"]
