"""Safe, publisher-owned chat failures. Never forward arbitrary upstream error text."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# code -> (message, next step, retryable). These are diagnostics, never capability grants.
PROBLEMS = {
    "core_planning_route": (
        "Your selected planning model route could not answer this request.",
        "Check the planning route in Settings, its key, model availability and connection.",
        True,
    ),
    "core_route_credits": (
        "Your planning model's account is out of credits.",
        "Add credits with that provider, or choose another planning route in Settings.",
        False,
    ),
    "core_invalid_configuration": (
        "Mavo could not validate the chat's Crew or resource configuration.",
        "Update Mavo and report this request reference if it repeats. "
        "This is not fixed by changing your instructions or granting extra permissions.",
        False,
    ),
    "core_not_configured": (
        "Mavo Core has not been configured.",
        "The app publisher must configure the Core service. "
        "Crew model routes do not configure general chat.",
        False,
    ),
    "core_sign_in_required": (
        "Mavo could not verify your account session.",
        "Open Settings and sign in to Mavo again. Your Google resource connections are separate.",
        False,
    ),
    "core_identity_unavailable": (
        "Mavo's account-verification service is unavailable.",
        "Wait a moment and try again; signing out is not required.",
        True,
    ),
    "core_usage_limit": (
        "Mavo Core's usage limit has been reached.",
        "Try again after the usage window resets. Your Crew are unaffected.",
        True,
    ),
    "core_provider_key": (
        "The Core model provider rejected the publisher's API key.",
        "The app publisher must update the Core service key. "
        "Changing your Crew route or Google login will not fix this.",
        False,
    ),
    "core_provider_credits": (
        "The Core model provider reports insufficient credits.",
        "The app publisher must top up the Core service account. "
        "Your personal Crew credits are separate.",
        False,
    ),
    "core_model_unavailable": (
        "The configured Core model is unavailable.",
        "The app publisher must check the Core model ID and provider availability.",
        False,
    ),
    "core_provider_rate_limit": (
        "The Core model provider is rate-limiting requests.",
        "Wait briefly before retrying. No automatic paid retry was made.",
        True,
    ),
    "core_provider_unavailable": (
        "The Core model provider could not complete the request.",
        "Try again later. Existing Crew and their schedules are unaffected.",
        True,
    ),
    "core_provider_request": (
        "The Core model provider rejected the request format or model settings.",
        "The app publisher must check the model's supported options and Core configuration.",
        False,
    ),
    "core_unreachable": (
        "Mavo cannot reach its Core service.",
        "Check your connection. For local development, "
        "start the publisher service with scripts/start-mavo.ps1.",
        True,
    ),
    "core_timeout": (
        "Mavo Core's model took too long to respond.",
        "Your saved Crew is unchanged. Retry when ready; no automatic paid retry was made.",
        True,
    ),
    "core_invalid_response": (
        "Mavo Core's model returned a response that failed validation.",
        "No proposed Crew or task was activated from that response. "
        "Retry, or ask the publisher to check the planning model.",
        True,
    ),
    "core_empty_response": (
        "Mavo Core's model returned no usable answer.",
        "No task was started. Retry, or ask the publisher to check the Core model.",
        True,
    ),
    "core_truncated_response": (
        "Mavo Core's model response was cut off at its output limit.",
        "No proposed setup was activated. Ask for a smaller task, "
        "or ask the publisher to adjust the Core output budget.",
        True,
    ),
    "core_protocol_mismatch": (
        "The desktop and Core service disagree on the request format.",
        "Update and restart both Mavo and its publisher service so they use compatible versions.",
        False,
    ),
    "core_bad_response": (
        "The Core service returned an unreadable response.",
        "Update or restart the Core service. No successful reply could be verified.",
        True,
    ),
    "core_gateway_failed": (
        "The Core service failed this request but did not provide a specific reason.",
        "The publisher must check the service logs using the request reference. "
        "A newer Core service provides precise failure details.",
        True,
    ),
    "core_request_pending": (
        "This chat request is already being processed or its outcome is not yet known.",
        "Wait and check the conversation before sending it again, to avoid duplicate work.",
        False,
    ),
    "core_request_conflict": (
        "This request reference belongs to a different chat message.",
        "Send your message as a new request. Existing work was not overwritten.",
        False,
    ),
    "core_internal_error": (
        "Mavo encountered an internal error while processing your message.",
        "Check Activity before retrying; use the request reference when reporting this problem.",
        True,
    ),
}


def problem(code: str, request_id: str | None = None) -> dict:
    code = code if code in PROBLEMS else "core_gateway_failed"
    message, action, retryable = PROBLEMS[code]
    return {
        "code": code,
        "message": message,
        "action": action,
        "retryable": retryable,
        "request_id": request_id,
    }


class CoreHTTPError(HTTPException):
    def __init__(self, status: int, code: str, request_id: str | None = None):
        self.problem = problem(code, request_id)
        # Preserve the existing string detail for older desktop clients.
        super().__init__(
            status_code=status, detail=self.problem["message"] + " " + self.problem["action"]
        )


async def core_error_response(request: Request, error: CoreHTTPError):
    return JSONResponse(
        status_code=error.status_code, content={"detail": error.detail, "error": error.problem}
    )
