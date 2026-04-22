"""Built-in tool: simulated verbose API for testing compression."""

import json
import random
from datetime import datetime, timezone


def dummy_api(endpoint: str = "users", params: dict | None = None) -> str:
    """Simulated API call that returns verbose JSON."""
    params = params or {}
    now = datetime.now(timezone.utc).isoformat()

    if endpoint == "users":
        users = []
        for i in range(20):
            users.append({
                "id": f"usr_{1000 + i}",
                "username": f"user_{i}",
                "email": f"user_{i}@example.com",
                "full_name": f"Test User {i}",
                "created_at": now,
                "updated_at": now,
                "is_active": random.choice([True, False]),
                "role": random.choice(["admin", "user", "viewer"]),
                "last_login": now,
                "preferences": {
                    "theme": "dark",
                    "language": "en",
                    "notifications": True,
                    "timezone": "UTC",
                },
                "metadata": {
                    "source": "api",
                    "version": "v2",
                    "request_id": f"req_{random.randint(10000, 99999)}",
                },
            })
        return json.dumps({
            "status": "success",
            "endpoint": f"/api/v2/{endpoint}",
            "params": params,
            "timestamp": now,
            "pagination": {"page": 1, "per_page": 20, "total": 150, "total_pages": 8},
            "data": users,
            "meta": {
                "response_time_ms": random.randint(50, 300),
                "cache_hit": False,
                "rate_limit_remaining": 95,
                "api_version": "2.1.0",
            },
        }, indent=2)

    elif endpoint == "metrics":
        return json.dumps({
            "status": "success",
            "endpoint": f"/api/v2/{endpoint}",
            "timestamp": now,
            "data": {
                "cpu_usage": round(random.uniform(10, 90), 2),
                "memory_usage": round(random.uniform(30, 80), 2),
                "disk_usage": round(random.uniform(20, 70), 2),
                "active_connections": random.randint(10, 500),
                "requests_per_second": round(random.uniform(100, 5000), 2),
                "error_rate": round(random.uniform(0, 5), 3),
                "latency_p50": random.randint(5, 50),
                "latency_p95": random.randint(50, 200),
                "latency_p99": random.randint(200, 1000),
            },
            "meta": {"response_time_ms": random.randint(10, 100)},
        }, indent=2)

    return json.dumps({
        "status": "success",
        "endpoint": f"/api/v2/{endpoint}",
        "timestamp": now,
        "data": {"message": f"Response from {endpoint}", "params": params},
    }, indent=2)
