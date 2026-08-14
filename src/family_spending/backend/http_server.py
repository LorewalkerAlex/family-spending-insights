from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from family_spending.application import ApplicationError
from family_spending.backend.application import RuntimeFamilySpendingApplication
from family_spending.http_api import FamilySpendingHttpServer, _RequestHandler


class RuntimeFamilySpendingHttpServer(FamilySpendingHttpServer):
    application: RuntimeFamilySpendingApplication


class _RuntimeRequestHandler(_RequestHandler):
    """Extend the compatibility HTTP transport with runtime-owned read endpoints."""

    server: RuntimeFamilySpendingHttpServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path != "/api/spending-statistics":
            super().do_GET()
            return

        try:
            projection = self.server.application.get_spending_statistics()
            self._send_json(
                HTTPStatus.OK,
                {"spending_statistics": projection},
            )
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def create_runtime_http_server(
    application: RuntimeFamilySpendingApplication,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> RuntimeFamilySpendingHttpServer:
    """Create the canonical runtime HTTP server while preserving legacy transport routes."""
    server = RuntimeFamilySpendingHttpServer((host, port), _RuntimeRequestHandler)
    server.application = application
    return server
