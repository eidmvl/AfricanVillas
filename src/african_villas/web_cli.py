from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("AFRICAN_VILLAS_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("AFRICAN_VILLAS_WEB_PORT", "8092"))
    if not os.environ.get("AFRICAN_VILLAS_SESSION_SECRET"):
        raise RuntimeError("AFRICAN_VILLAS_SESSION_SECRET is required")
    if not os.environ.get("AFRICAN_VILLAS_WEB_PASSWORD"):
        raise RuntimeError("AFRICAN_VILLAS_WEB_PASSWORD is required")
    uvicorn.run(
        "african_villas.web:app",
        host=host,
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=True,
    )


if __name__ == "__main__":
    main()
