from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from family_spending.application.errors import (
    ApplicationError,
    ApplicationNotFoundError,
    ApplicationValidationError,
)
from family_spending.application.service import FamilySpendingApplication
from family_spending.interfaces.http.serialization import (
    feedback_payload,
    manual_input_deletion_payload,
    manual_input_payload,
    manual_input_record_payload,
    mapping_review_preview_payload,
    mapping_review_workspace_payload,
    scheduled_rule_payload,
    scheduled_run_payload,
    transaction_payload,
)


class FamilySpendingHttpServer(ThreadingHTTPServer):
    """Threaded local JSON transport sharing one canonical Application instance."""

    daemon_threads = True
    application: FamilySpendingApplication


def create_http_server(
    application: FamilySpendingApplication,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> FamilySpendingHttpServer:
    """Create the HTTP transport without performing Application lifecycle work."""
    server = FamilySpendingHttpServer((host, port), _RequestHandler)
    server.application = application
    return server


class _RequestHandler(BaseHTTPRequestHandler):
    server: FamilySpendingHttpServer

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers(content_length=0)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._dispatch(self._get)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self._dispatch(self._post)

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
        self._dispatch(self._patch)

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        self._dispatch(self._delete)

    def _dispatch(self, handler) -> None:
        try:
            handler(urlsplit(self.path).path)
        except ApplicationNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except (ApplicationValidationError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _get(self, path: str) -> None:
        app = self.server.application
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/financial-summary":
            self._send_json(
                HTTPStatus.OK,
                {"financial_summary": app.get_financial_summary()},
            )
            return
        if path == "/api/spending-statistics":
            self._send_json(
                HTTPStatus.OK,
                {"spending_statistics": app.get_spending_statistics()},
            )
            return
        if path == "/api/feedback":
            self._send_json(
                HTTPStatus.OK,
                {"feedback": [feedback_payload(item) for item in app.list_feedback()]},
            )
            return
        if path == "/api/categories":
            self._send_json(HTTPStatus.OK, {"categories": list(app.list_categories())})
            return
        if path == "/api/manual-descriptions":
            self._send_json(
                HTTPStatus.OK,
                {"descriptions": list(app.list_manual_descriptions())},
            )
            return
        if path == "/api/manual-inputs":
            self._send_json(
                HTTPStatus.OK,
                {
                    "manual_inputs": [
                        manual_input_record_payload(item)
                        for item in app.list_manual_inputs()
                    ]
                },
            )
            return
        if path == "/api/scheduled-inputs":
            self._send_json(
                HTTPStatus.OK,
                {
                    "scheduled_inputs": [
                        scheduled_rule_payload(item)
                        for item in app.list_scheduled_input_views()
                    ]
                },
            )
            return
        if path == "/api/mapping-reviews":
            self._send_json(
                HTTPStatus.OK,
                {
                    "mapping_review": mapping_review_workspace_payload(
                        app.get_mapping_review_workspace()
                    )
                },
            )
            return
        if path == "/api/transactions":
            self._send_json(
                HTTPStatus.OK,
                {
                    "transactions": [
                        transaction_payload(item) for item in app.list_transactions()
                    ]
                },
            )
            return

        prefix = "/api/transactions/"
        if path.startswith(prefix) and path != prefix:
            transaction_id = self._single_route_id(path[len(prefix) :])
            item = app.get_transaction(transaction_id)
            self._send_json(HTTPStatus.OK, {"transaction": transaction_payload(item)})
            return
        self._route_not_found()

    def _post(self, path: str) -> None:
        app = self.server.application
        if path == "/api/feedback":
            payload = self._read_json_object()
            self._require_exact_fields(
                payload,
                {"content", "context"},
                {"content"},
                "Feedback",
            )
            item = app.create_feedback(
                content=payload["content"],
                context=payload.get("context"),
            )
            self._send_json(HTTPStatus.CREATED, {"feedback": feedback_payload(item)})
            return

        if path == "/api/manual-inputs":
            payload = self._read_json_object()
            allowed = {"type", "date", "amount", "description", "note"}
            required = {"type", "date", "amount", "description"}
            self._require_exact_fields(payload, allowed, required, "Manual Input")
            result = app.create_manual_input(
                transaction_type=payload["type"],
                transaction_date=payload["date"],
                amount=payload["amount"],
                description=payload["description"],
                note=payload.get("note"),
            )
            self._send_json(
                HTTPStatus.CREATED,
                {"manual_input": manual_input_payload(result)},
            )
            return

        if path == "/api/scheduled-inputs":
            payload = self._read_json_object()
            values = self._scheduled_input_values(payload, "Scheduled Input")
            rule = app.create_scheduled_input(**values)
            view = app.get_scheduled_input_view(rule.id)
            self._send_json(
                HTTPStatus.CREATED,
                {"scheduled_input": scheduled_rule_payload(view)},
            )
            return

        if path == "/api/scheduled-inputs/run-due":
            result = app.run_due_scheduled_inputs()
            self._send_json(
                HTTPStatus.OK,
                {"scheduled_input_run": scheduled_run_payload(result)},
            )
            return

        manual_prefix = "/api/manual-inputs/"
        suffix = "/corrections"
        if path.startswith(manual_prefix) and path.endswith(suffix):
            evidence_id = self._single_route_id(path[len(manual_prefix) : -len(suffix)])
            payload = self._read_json_object()
            allowed = {"type", "date", "amount", "description", "note"}
            required = {"type", "date", "amount", "description"}
            self._require_exact_fields(payload, allowed, required, "Manual Input correction")
            kwargs: dict[str, Any] = {
                "transaction_type": payload["type"],
                "transaction_date": payload["date"],
                "amount": payload["amount"],
                "description": payload["description"],
            }
            if "note" in payload:
                kwargs["note"] = payload["note"]
            result = app.correct_manual_input(evidence_id, **kwargs)
            self._send_json(
                HTTPStatus.OK,
                {
                    "manual_input_correction": {
                        "replaced_source_record_id": evidence_id,
                        "manual_input": manual_input_payload(result),
                    }
                },
            )
            return

        if path == "/api/mapping-reviews/preview":
            payload = self._read_json_object()
            allowed = {"description", "merchant", "category"}
            self._require_exact_fields(payload, allowed, allowed, "Mapping Review preview")
            preview = app.preview_mapping_review(
                description=payload["description"],
                merchant=payload["merchant"],
                category=payload["category"],
            )
            self._send_json(
                HTTPStatus.OK,
                {"preview": mapping_review_preview_payload(preview)},
            )
            return

        if path == "/api/mapping-reviews/apply":
            payload = self._read_json_object()
            allowed = {
                "description",
                "merchant",
                "category",
                "preview_token",
                "confirm_new_merchant",
            }
            required = {"description", "merchant", "category", "preview_token"}
            self._require_exact_fields(payload, allowed, required, "Mapping Review apply")
            preview = app.apply_mapping_review(
                description=payload["description"],
                merchant=payload["merchant"],
                category=payload["category"],
                preview_token=payload["preview_token"],
                confirm_new_merchant=payload.get("confirm_new_merchant", False),
            )
            self._send_json(
                HTTPStatus.OK,
                {"mapping_review": mapping_review_preview_payload(preview)},
            )
            return

        self._route_not_found()

    def _patch(self, path: str) -> None:
        app = self.server.application
        feedback_prefix = "/api/feedback/"
        if path.startswith(feedback_prefix) and path != feedback_prefix:
            feedback_id = self._single_route_id(path[len(feedback_prefix) :])
            payload = self._read_json_object()
            self._require_exact_fields(
                payload,
                {"status"},
                {"status"},
                "Feedback update",
            )
            item = app.update_feedback(feedback_id, status=payload["status"])
            self._send_json(HTTPStatus.OK, {"feedback": feedback_payload(item)})
            return

        scheduled_prefix = "/api/scheduled-inputs/"
        if path.startswith(scheduled_prefix) and path != scheduled_prefix:
            rule_id = self._single_route_id(path[len(scheduled_prefix) :])
            payload = self._read_json_object()
            values = self._scheduled_input_values(payload, "Scheduled Input update")
            app.update_scheduled_input(rule_id, **values)
            view = app.get_scheduled_input_view(rule_id)
            self._send_json(
                HTTPStatus.OK,
                {"scheduled_input": scheduled_rule_payload(view)},
            )
            return

        transaction_prefix = "/api/transactions/"
        enrichment_suffix = "/enrichment"
        if path.startswith(transaction_prefix) and path.endswith(enrichment_suffix):
            transaction_id = self._single_route_id(
                path[len(transaction_prefix) : -len(enrichment_suffix)]
            )
            payload = self._read_json_object()
            allowed = {"merchant", "category", "note"}
            unknown = sorted(set(payload) - allowed)
            if unknown:
                raise ApplicationValidationError(f"Unknown Enrichment fields: {unknown!r}")
            if not payload:
                raise ApplicationValidationError("Enrichment update requires at least one field")
            kwargs = {field: payload[field] for field in allowed if field in payload}
            item = app.update_enrichment(transaction_id, **kwargs)
            self._send_json(HTTPStatus.OK, {"transaction": transaction_payload(item)})
            return

        self._route_not_found()

    def _delete(self, path: str) -> None:
        app = self.server.application
        scheduled_prefix = "/api/scheduled-inputs/"
        if path.startswith(scheduled_prefix) and path != scheduled_prefix:
            rule_id = self._single_route_id(path[len(scheduled_prefix) :])
            deleted = app.delete_scheduled_input(rule_id)
            self._send_json(
                HTTPStatus.OK,
                {"scheduled_input_deletion": {"id": deleted.id}},
            )
            return

        manual_prefix = "/api/manual-inputs/"
        if path.startswith(manual_prefix) and path != manual_prefix:
            evidence_id = self._single_route_id(path[len(manual_prefix) :])
            result = app.delete_manual_input(evidence_id)
            self._send_json(
                HTTPStatus.OK,
                {"manual_input_deletion": manual_input_deletion_payload(result)},
            )
            return

        self._route_not_found()

    @staticmethod
    def _single_route_id(raw: str) -> str:
        value = unquote(raw)
        if not value or "/" in value:
            raise ApplicationNotFoundError("Route not found")
        return value

    @staticmethod
    def _require_exact_fields(
        payload: dict[str, Any],
        allowed: set[str],
        required: set[str],
        label: str,
    ) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApplicationValidationError(f"Unknown {label} fields: {unknown!r}")
        missing = sorted(required - set(payload))
        if missing:
            raise ApplicationValidationError(
                f"{label} is missing required fields: {missing!r}"
            )

    @staticmethod
    def _scheduled_input_values(payload: dict[str, Any], label: str) -> dict[str, Any]:
        allowed = {"type", "amount", "description", "next_date", "note", "enabled"}
        required = {"type", "amount", "description", "next_date", "enabled"}
        _RequestHandler._require_exact_fields(payload, allowed, required, label)
        return {
            "transaction_type": payload["type"],
            "amount": payload["amount"],
            "description": payload["description"],
            "next_date": payload["next_date"],
            "note": payload.get("note"),
            "enabled": payload["enabled"],
        }

    def _read_json_object(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ApplicationValidationError("Request body is required")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ApplicationValidationError("Invalid Content-Length") from exc
        if length <= 0 or length > 64 * 1024:
            raise ApplicationValidationError("Request body size is invalid")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApplicationValidationError("Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ApplicationValidationError("Request body must be a JSON object")
        return payload

    def _route_not_found(self) -> None:
        self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self._send_common_headers(content_length=len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_common_headers(self, *, content_length: int) -> None:
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PATCH, DELETE, OPTIONS",
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: object) -> None:
        del format, args
