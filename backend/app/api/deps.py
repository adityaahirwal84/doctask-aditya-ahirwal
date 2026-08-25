from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

settings = get_settings()


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """Single shared key, checked on every request. This is documented in
    docs/architecture.md as a deliberate cut versus JWT/multi-tenant auth -
    the brief asks for a human approval gate, not multi-user access
    control, and building the latter without a requirement for it is
    exactly the kind of unrequested infrastructure this project avoids."""
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    return x_api_key
