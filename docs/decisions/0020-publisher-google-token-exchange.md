# 0020: Publisher-side Google token exchange

Status: Accepted. Supersedes the no-broker assumption in the initial Google setup.

The packaged sidecar omitted the private Google client credential by design. A real invalid-code diagnostic established that Google rejected omission as `invalid_request` / missing `client_secret`, while supplying the configured credential reached authorization-code validation. Therefore the generic retry message was misleading; it was not evidence of a bad user's account or insufficient test-user access.

Keep the credential off the desktop. Add bounded `/v1/google/token` handling to the already-required publisher gateway. It accepts only configured public client IDs and typed authorization-code+PKCE+loopback or refresh grants. The endpoint targets Google only, rejects unsupported returned scopes, returns only token fields, sanitizes errors and does not retry a consumed/uncertain code. Bootstrap cannot depend on a Firebase login it is used to establish; valid Google code/PKCE or possession of a private refresh token is required. Durable per-address/global quotas, body limits, denied browser Origin, bounded concurrency and proxy/TLS controls constrain abuse, not official-binary authenticity.

User credentials transiently cross the publisher over HTTPS/development loopback, but are never saved in its database or logs. Native code retains state/nonce, callback lifecycle, signed OIDC issuer/audience/expiry checks and DPAPI token storage. Refresh uses the same route and preserves refresh-token rotation. Source development may exchange directly with its private `.env` credential. Installer metadata still contains only public identity and gateway URL.

Configuration, Google/Firebase setup, privacy notices and real consent tests must reflect this extra trust boundary. Automated coverage tests the actual native→broker→Google contract using signed fixture identities, plus argument/scope/client/rate validation, no credential reflection/storage, cancellation, missing-credential diagnostics and refresh.
