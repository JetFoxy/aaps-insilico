.PHONY: bootstrap test demo live

bootstrap:
	./tools/bootstrap.sh

test:
	.venv/bin/python -m pytest tests/ -q

demo:
	.venv/bin/python python/runner/export_demo.py

live:
	.venv/bin/python python/server/live.py --port 8766
