from app.limiter.policy import RateLimitPolicy, client_ip_key, user_id_key

# (HTTP method, path) -> policy. Looked up by the middleware per request;
# routes with no entry here are not rate limited.
ROUTE_POLICIES: dict[tuple[str, str], RateLimitPolicy] = {
    ("POST", "/login"): RateLimitPolicy(
        name="login",
        limit=5,
        window_seconds=60,
        key_func=client_ip_key,
        # Safety-first: if the limiter can't be checked, deny. A degraded
        # limiter shouldn't let unlimited login attempts through.
        degraded_mode="fail_closed",
    ),
    ("GET", "/search"): RateLimitPolicy(
        name="search",
        limit=100,
        window_seconds=60,
        key_func=user_id_key,
        # Availability-first: if the limiter can't be checked, allow. A
        # read-only search endpoint shouldn't go fully dark because Redis
        # is degraded.
        degraded_mode="fail_open",
    ),
}
