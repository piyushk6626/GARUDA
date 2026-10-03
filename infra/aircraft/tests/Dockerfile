FROM python:3.13.8-slim-bookworm@sha256:61169c2bdb8e6bb44a8dfad33f569d324d52f079fded9a204b322a6fb9c9f799
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tests
COPY infra/aircraft/tests/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock
COPY backend ./backend
COPY config ./config
COPY tests ./tests
ENTRYPOINT ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/integration/test_aircraft_stores.py"]
