"""Classify Google failures without returning response bodies or credentials."""


def token_error(response):
    try:
        data = response.json()
        code = data.get("error")
        if not isinstance(code, str):
            code = None
        description = str(data.get("error_description", "")).lower()
    except (ValueError, AttributeError, TypeError):
        code, description = None, ""
    if (
        code == "invalid_request"
        and "client_secret" in description
        and ("missing" in description or "required" in description)
    ):
        return "missing_client_secret"
    known = {
        "invalid_client",
        "invalid_grant",
        "redirect_uri_mismatch",
        "invalid_scope",
        "access_denied",
        "missing_client_secret",
        "publisher_setup_required",
        "oauth_rate_limited",
        "unsupported_permission",
    }
    if code in known:
        return code
    if response.status_code == 429:
        return "oauth_rate_limited"
    if response.status_code >= 500 or code in {"server_error", "temporarily_unavailable"}:
        return "google_unavailable"
    return "token_exchange_failed"
