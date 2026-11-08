from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mavo.database import ModelRoute, ProviderConfig

PROVIDER_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "airouter": "AIRouter",
}


def import_legacy_provider_routes(session: Session) -> None:
    """Preserve provider settings created before named model routes existed."""
    existing_credentials = {
        value for value in session.scalars(select(ModelRoute.credential_id)).all() if value
    }
    changed = False
    for config in session.scalars(select(ProviderConfig)).all():
        if config.provider in existing_credentials:
            continue
        session.add(
            ModelRoute(
                id=f"legacy-{config.provider}",
                name=f"{PROVIDER_NAMES.get(config.provider, config.provider.title())} default",
                provider=config.provider,
                model=config.default_model,
                credential_id=config.provider,
            )
        )
        changed = True
    if changed:
        session.commit()
