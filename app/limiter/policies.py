from app.limiter.policy import RateLimitPolicy, client_ip_key, user_id_key

# (HTTP method, path) -> policy. Looked up by the middleware per request;
# routes with no entry here are not rate limited.
ROUTE_POLICIES: dict[tuple[str, str], RateLimitPolicy] = {
    ("POST", "/login"): RateLimitPolicy(
        name="login",
        limit=5,
        window_seconds=60,
        key_func=client_ip_key,
    ),
    ("GET", "/search"): RateLimitPolicy(
        name="search",
        limit=100,
        window_seconds=60,
        key_func=user_id_key,
    ),
}
