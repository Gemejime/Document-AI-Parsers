.PHONY: help install lint format typecheck test proto check clean docker-vision docker-audio

PYTHON ?= python

help:
	@echo "Available commands:"
	@echo "  make install     Install project and development dependencies"
	@echo "  make lint        Run ruff linter and format checker"
	@echo "  make format      Run ruff check with --fix and apply formatting"
	@echo "  make typecheck   Run mypy static type checking"
	@echo "  make test        Run unit and integration tests"
	@echo "  make proto       Validate and compile protobuf definitions"
	@echo "  make check       Run lint, typecheck, and test sequentially"
	@echo "  make clean       Remove caches and build artifacts"

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy cmd internal test

test:
	$(PYTHON) -m pytest test/unit test/integration -v

proto:
	$(PYTHON) -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. api/proto/v1/common.proto api/proto/v1/document_ai.proto

check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
