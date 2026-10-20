"""Entry point for the Lexia Read Mode API service.

Runs the FastAPI app under uvicorn on port 8100 when FastAPI and uvicorn
are importable. Otherwise falls back to the stdlib http.server app.
"""

from __future__ import annotations


def main() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ImportError:
        from lexia.api.stdlib_app import serve

        serve()
        return

    uvicorn.run("lexia.api.app:app", host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
