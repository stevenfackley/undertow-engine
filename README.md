# undertow-engine <sub>`>o)`</sub>
A standalone Python microservice for automated short-form video compositing and headless social media deployment. Built with FastAPI, Celery, MoviePy, and Playwright.

## Local Test Runner

Phase 6 standardizes the local test path so pytest does not depend on machine
temp-directory permissions or a pre-created virtual environment.

### Recommended

```bash
uv run --python 3.11 --with-requirements requirements-test.txt python scripts/run_tests.py
```

That command will:

- use Python 3.11 as expected by the repo
- install `requirements.txt` and test dependencies into an isolated uv env
- run pytest with repo-local cache and temp directories under `.cache/pytest`

### Existing venv

If you already have the project venv set up, you can also run:

```bash
python scripts/run_tests.py
```
