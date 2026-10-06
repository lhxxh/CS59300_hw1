"""Local entry point and Gunicorn WSGI application."""
import os

from research_assistant import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")), threaded=True, debug=False)
