.PHONY: help install dev build test clean format lint

help:
	@echo "PG Isomap Development Commands"
	@echo "==============================="
	@echo "make install    - Install dependencies (backend + frontend)"
	@echo "make dev        - Run in development mode"
	@echo "make build      - Build frontend for production"
	@echo "make test       - Run tests"
	@echo "make format     - Format code"
	@echo "make lint       - Lint code"
	@echo "make clean      - Clean build artifacts"

install:
	@echo "Installing Python dependencies..."
	uv sync
	@echo "Installing frontend dependencies..."
	cd frontend && npm install

dev:
	@./run_dev.sh

build:
	@echo "Building frontend..."
	cd frontend && npm run build

test:
	@echo "Running Python tests..."
	uv run pytest
	@echo "Running frontend type checks..."
	cd frontend && npm run check

format:
	@echo "Formatting Python code..."
	uv run black src/ tests/
	uv run ruff check --fix src/ tests/

lint:
	@echo "Linting Python code..."
	uv run ruff check src/ tests/
	uv run mypy src/
	@echo "Checking frontend..."
	cd frontend && npm run check

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info
	rm -rf frontend/dist frontend/.svelte-kit
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
