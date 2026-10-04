FROM python:3.13.8-slim-bookworm@sha256:61169c2bdb8e6bb44a8dfad33f569d324d52f079fded9a204b322a6fb9c9f799
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tests
RUN pip install --no-cache-dir uv==0.11.21
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra dev
COPY backend ./backend
COPY config ./config
COPY tests ./tests
ENTRYPOINT ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", "tests/integration/test_crew_stores.py"]
