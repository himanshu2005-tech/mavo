"""Non-secret, durable receipts distinguish local removal from provider revocation."""

from mavo.database import WorkspaceSetting, utcnow

MANAGE_URLS = {
    "google": "https://myaccount.google.com/connections",
    "github": "https://github.com/settings/apps/authorizations",
}


def read_receipt(session, provider):
    row = session.get(WorkspaceSetting, "connection-revocation-" + provider)
    return row.value if row else None


def save_receipt(factory, provider, remote_revoked, message, needs_action):
    value = {
        "provider": provider,
        "local_disconnected": True,
        "remote_revoked": remote_revoked,
        "requires_provider_action": needs_action,
        "message": message,
        "manage_url": MANAGE_URLS[provider],
        "at": utcnow().isoformat(),
    }
    with factory() as session:
        key = "connection-revocation-" + provider
        row = session.get(WorkspaceSetting, key)
        if row:
            row.value = value
        else:
            session.add(WorkspaceSetting(key=key, value=value))
        session.commit()
    return value
