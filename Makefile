.PHONY: test lint format serve build-corpus crawl

test:
	pytest tests/ -n auto --tb=short -q

test-unit:
	pytest tests/ -m "not integration" -n auto --tb=short -q

lint:
	ruff check fontmatch/ tests/

format:
	ruff format fontmatch/ tests/

serve:
	flask --app fontmatch.service.app:get_app run --host 0.0.0.0 --port 8087 --reload

build-corpus:
	python scripts/build_corpus.py

crawl:
	python scripts/crawl.py --limit 10000 --rate 0.5
