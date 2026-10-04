.PHONY: up down migrate seed seed-dry viz all test

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE := docker compose
DATABASE_URL ?= postgresql://garuda:garuda@localhost:5433/garuda
NEO4J_URI ?= bolt://localhost:7687
NEO4J_USER ?= neo4j
NEO4J_PASSWORD ?= garuda-dev
REDIS_URL ?= redis://localhost:6379/0
KAFKA_BOOTSTRAP ?= localhost:19092
GARUDA_SEED ?= 42
MINIO_ENDPOINT ?= http://localhost:9000
MINIO_ROOT_USER ?= garuda
MINIO_ROOT_PASSWORD ?= garuda-secret

ifeq ($(wildcard $(ROOT)/backend/.venv/bin/python),)
PYTHON := python3
else
PYTHON := $(ROOT)/backend/.venv/bin/python
endif

export DATABASE_URL NEO4J_URI NEO4J_USER NEO4J_PASSWORD REDIS_URL KAFKA_BOOTSTRAP GARUDA_SEED MINIO_ENDPOINT MINIO_ROOT_USER MINIO_ROOT_PASSWORD

up:
	$(COMPOSE) up -d --build --wait postgres neo4j redis redpanda minio
	$(COMPOSE) --profile init run --rm --no-deps neo4j-init
	$(COMPOSE) --profile init run --rm --no-deps redpanda-init
	$(COMPOSE) --profile init run --rm --no-deps minio-init

down:
	$(COMPOSE) --profile init down

migrate:
	cd $(ROOT)/backend && $(PYTHON) -m alembic upgrade head

seed: migrate
	cd $(ROOT)/backend && PYTHONPATH=src $(PYTHON) -m garuda.generate --config ../config/scenario.yaml --seed $(GARUDA_SEED)

seed-dry:
	cd $(ROOT)/backend && PYTHONPATH=src $(PYTHON) -m garuda.generate --config ../config/scenario.yaml --seed $(GARUDA_SEED) --dry-run

viz:
	cd $(ROOT)/backend && PYTHONPATH=src $(PYTHON) -m garuda.viz --config ../config/scenario.yaml --seed $(GARUDA_SEED) --out ../data/viz/index.html

all: up seed viz

test:
	cd $(ROOT)/backend && PYTHONPATH=src $(PYTHON) -m pytest
