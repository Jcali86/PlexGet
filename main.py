from api import create_app
from config import config

app = create_app()

if __name__ == "__main__":
    # Loopback by default. Put it behind whatever you already use to reach your
    # own server from outside rather than opening this port to the world.
    settings = config.get("flask") or {}
    app.run(
        host=settings.get("host", "127.0.0.1"),
        port=settings.get("port", 5050),
        debug=settings.get("debug", False),
    )
