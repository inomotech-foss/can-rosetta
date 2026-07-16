# Convenience targets. Each component is also usable directly (see its README).
.PHONY: help install test lint sample demo validate ios

help:
	@echo "make install   - install server + edge (editable, with dev deps)"
	@echo "make test      - run server + edge test suites"
	@echo "make lint      - ruff check server + edge"
	@echo "make sample    - regenerate datasets/sample-session"
	@echo "make demo      - run identification on the sample session"
	@echo "make validate  - validate the sample session against the schemas"
	@echo "make ios       - generate the iOS Xcode project (needs xcodegen)"

install:
	cd server && pip install -e ".[dev]"
	cd edge/autopi && pip install -e ".[dev]"

test:
	cd server && pytest -q
	cd edge/autopi && pytest -q

lint:
	cd server && ruff check .
	cd edge/autopi && ruff check .

sample:
	cd server && canrosetta make-sample ../datasets/sample-session --duration 30

demo:
	cd server && canrosetta identify ../datasets/sample-session --out /tmp/canrosetta-out

validate:
	python .github/scripts/validate_session.py datasets/sample-session

ios:
	cd companion/ios && xcodegen generate
