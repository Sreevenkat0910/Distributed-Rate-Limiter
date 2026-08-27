# Distributed Rate Limiter

A rate limiter that correctly enforces a shared limit across multiple application instances, backed by Redis, with a deliberate and documented fail-open vs. fail-closed policy for Redis degradation. Built with Python/FastAPI, load-balanced by nginx, orchestrated with Docker Compose, and validated under concurrency with k6 and Toxiproxy fault injection. This project is currently in early scaffolding — rate-limiting logic has not been implemented yet.
