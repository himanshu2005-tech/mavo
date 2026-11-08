"""Safe OAuth diagnostics. Never surface provider response bodies or token values."""

MESSAGES = {
    "firebase_setup": "Add the Firebase Web app configuration in backend/firebase.json first.",
    "workspace_owner": "Use the original Google account that owns this Windows workspace.",
    "firebase_login": "Firebase login failed. Check Web configuration and enable Google sign-in.",
    "firebase_provider": "Enable Google in Firebase Authentication → Sign-in method, then retry.",
    "firebase_google_client": (
        "Firebase rejected the Google credential. In Firebase's Google sign-in provider, "
        "allow Mavo's existing Google client ID from the external project."
    ),
    "consent_denied": "Google access was not approved. Check the test-user account and try again.",
    "invalid_client": "Google rejected the client ID or secret. Check Desktop app credentials.",
    "invalid_grant": "Google's sign-in link expired or was used. Start a new connection.",
    "redirect_uri_mismatch": "Use a Google Desktop app client with loopback redirect support.",
    "invalid_scope": "Google rejected a requested permission. Check the app's Google scope setup.",
    "access_denied": "Google blocked access. Check test users and Google account policies.",
    "google_unavailable": "Google is temporarily unavailable. Try connecting again shortly.",
    "network_error": "Mavo could not reach Google. Check your connection and try again.",
    "token_exchange_failed": "Google could not complete sign-in. Start a new connection.",
    "missing_client_secret": (
        "Google requires this application's OAuth credential. "
        "The publisher must configure Google token exchange; your account is not the problem."
    ),
    "publisher_setup_required": (
        "Google token exchange is not configured on Mavo's publisher service. "
        "The publisher must add its Google Desktop OAuth credentials there."
    ),
    "oauth_rate_limited": "Too many Google connection attempts. Wait a minute and try again.",
    "unsupported_permission": "This Google grant contains permissions this build does not support.",
    "missing_identity": "Google returned no sign-in identity. Retry with the connected account.",
    "identity_expired": "Google's sign-in identity expired. Start a new connection.",
    "clock_skew": "Sync Windows date and time, then retry Google's identity verification.",
    "client_mismatch": "Google returned identity for a different client. Check the client ID.",
    "invalid_identity": "Google's identity could not be verified securely. Start a new connection.",
    "nonce_mismatch": "This response is for another sign-in attempt. Start a new connection.",
    "account_mismatch": "Choose the Google account already connected to Mavo to add services. "
    "To switch accounts, disconnect Google first.",
    "email_unverified": "Use a Google account with a verified email address.",
    "credential_storage": "Mavo could not securely save the Google connection. Try again.",
    "cancelled": "This Google sign-in attempt was cancelled. Your existing connection is kept.",
    "unexpected_response": "Google returned incomplete sign-in data. Start a new connection.",
}


class GoogleSignInError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in MESSAGES else "unexpected_response"
        super().__init__(MESSAGES[self.code])
