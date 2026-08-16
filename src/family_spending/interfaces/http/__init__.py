"""HTTP transport for the canonical Family Spending Application."""

from family_spending.interfaces.http.server import create_http_server

__all__ = ["create_http_server"]
