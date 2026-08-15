from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from family_spending.backend.application import (
    ApplicationError,
    ApplicationNotFoundError,
    ApplicationValidationError,
    FamilySpendingApplication,
)


class FamilySpendingHttpServer(HTTPServer):
    application: FamilySpendingApplication


def create_http_server(
    application: FamilySpendingApplication,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> FamilySpendingHttpServer:
    """Create the canonical local JSON transport without running application lifecycle work."""
    server = FamilySpendingHttpServer((host, port), _RequestHandler)
    server.application = application
    return server


class _RequestHandler(BaseHTTPRequestHandler):
    server: FamilySpendingHttpServer

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers(content_length=0)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return
            if path == "/api/financial-summary":
                self._send_json(
                    HTTPStatus.OK,
                    {"financial_summary": self.server.application.get_financial_summary()},
                )
                return
            if path == "/api/spending-statistics":
                self._send_json(
                    HTTPStatus.OK,
                    {"spending_statistics": self.server.application.get_spending_statistics()},
                )
                return
            if path == "/api/feedback":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "feedback": [
                            item.to_dict()
                            for item in self.server.application.list_feedback()
                        ]
                    },
                )
                return
            if path == "/api/categories":
                self._send_json(
                    HTTPStatus.OK,
                    {"categories": list(self.server.application.list_categories())},
                )
                return
            if path == "/api/manual-descriptions":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "descriptions": list(
                            self.server.application.list_manual_descriptions()
                        )
                    },
                )
                return
            if path == "/api/manual-inputs":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "manual_inputs": [
                            item.to_dict()
                            for item in self.server.application.list_manual_inputs()
                        ]
                    },
                )
                return
            if path == "/api/scheduled-inputs":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "scheduled_inputs": [
                            item.to_dict()
                            for item in self.server.application.list_scheduled_inputs()
                        ]
                    },
                )
                return
            if path == "/api/mapping-reviews":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "mapping_review": self.server.application.get_mapping_review_workspace().to_dict()
                    },
                )
                return
            if path == "/api/transactions":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "transactions": [
                            item.to_dict()
                            for item in self.server.application.list_transactions()
                        ]
                    },
                )
                return

            prefix = "/api/transactions/"
            if path.startswith(prefix) and path != prefix:
                transaction_id = unquote(path[len(prefix) :])
                if "/" in transaction_id:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                    return
                item = self.server.application.get_transaction(transaction_id)
                self._send_json(HTTPStatus.OK, {"transaction": item.to_dict()})
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
        except ApplicationNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except ApplicationValidationError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path).path
            if path == "/api/feedback":
                self._handle_feedback_create()
                return
            if path == "/api/manual-inputs":
                self._handle_manual_input_create()
                return
            if path == "/api/scheduled-inputs":
                self._handle_scheduled_input_create()
                return
            if path == "/api/scheduled-inputs/run-due":
                result = self.server.application.run_due_scheduled_inputs()
                self._send_json(
                    HTTPStatus.OK,
                    {"scheduled_input_run": result.to_dict()},
                )
                return

            manual_prefix = "/api/manual-inputs/"
            correction_suffix = "/corrections"
            if path.startswith(manual_prefix) and path.endswith(correction_suffix):
                source_record_id = unquote(
                    path[len(manual_prefix) : -len(correction_suffix)]
                )
                if not source_record_id or "/" in source_record_id:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                    return
                self._handle_manual_input_correction(source_record_id)
                return

            if path == "/api/mapping-reviews/preview":
                self._handle_mapping_review_preview()
                return
            if path == "/api/mapping-reviews/apply":
                self._handle_mapping_review_apply()
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
        except ApplicationNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except ApplicationValidationError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path).path
            feedback_prefix = "/api/feedback/"
            if path.startswith(feedback_prefix) and path != feedback_prefix:
                feedback_id = unquote(path[len(feedback_prefix) :])
                if not feedback_id or "/" in feedback_id:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                    return
                self._handle_feedback_update(feedback_id)
                return

            scheduled_prefix = "/api/scheduled-inputs/"
            if path.startswith(scheduled_prefix) and path != scheduled_prefix:
                rule_id = unquote(path[len(scheduled_prefix) :])
                if not rule_id or "/" in rule_id:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                    return
                self._handle_scheduled_input_update(rule_id)
                return

            transaction_prefix = "/api/transactions/"
            enrichment_suffix = "/enrichment"
            if not path.startswith(transaction_prefix) or not path.endswith(
                enrichment_suffix
            ):
                self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                return
            transaction_id = unquote(
                path[len(transaction_prefix) : -len(enrichment_suffix)]
            )
            if not transaction_id or "/" in transaction_id:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                return
            payload = self._read_json_object()
            allowed = {"merchant", "category", "note"}
            unknown = sorted(set(payload) - allowed)
            if unknown:
                raise ApplicationValidationError(
                    f"Unknown Enrichment fields: {unknown!r}"
                )
            if not payload:
                raise ApplicationValidationError(
                    "Enrichment update requires at least one field"
                )
            kwargs = {field: payload[field] for field in allowed if field in payload}
            item = self.server.application.update_enrichment(
                transaction_id,
                **kwargs,
            )
            self._send_json(HTTPStatus.OK, {"transaction": item.to_dict()})
        except ApplicationNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except (ApplicationValidationError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path).path
            scheduled_prefix = "/api/scheduled-inputs/"
            if path.startswith(scheduled_prefix) and path != scheduled_prefix:
                rule_id = unquote(path[len(scheduled_prefix) :])
                if not rule_id or "/" in rule_id:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                    return
                deleted = self.server.application.delete_scheduled_input(rule_id)
                self._send_json(
                    HTTPStatus.OK,
                    {"scheduled_input_deletion": {"id": deleted.id}},
                )
                return

            manual_prefix = "/api/manual-inputs/"
            if not path.startswith(manual_prefix) or path == manual_prefix:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                return
            source_record_id = unquote(path[len(manual_prefix) :])
            if not source_record_id or "/" in source_record_id:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Route not found")
                return
            result = self.server.application.delete_manual_input(source_record_id)
            self._send_json(
                HTTPStatus.OK,
                {"manual_input_deletion": result.to_dict()},
            )
        except ApplicationNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except ApplicationValidationError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except ApplicationError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _handle_feedback_create(self) -> None:
        payload = self._read_json_object()
        self._require_exact_fields(
            payload,
            {"content", "context"},
            {"content"},
            "Feedback",
        )
        item = self.server.application.create_feedback(
            content=payload["content"],
            context=payload.get("context"),
        )
        self._send_json(HTTPStatus.CREATED, {"feedback": item.to_dict()})

    def _handle_feedback_update(self, feedback_id: str) -> None:
        payload = self._read_json_object()
        self._require_exact_fields(
            payload,
            {"status"},
            {"status"},
            "Feedback update",
        )
        item = self.server.application.update_feedback(
            feedback_id,
            status=payload["status"],
        )
        self._send_json(HTTPStatus.OK, {"feedback": item.to_dict()})

    def _handle_manual_input_create(self) -> None:
        payload = self._read_json_object()
        allowed = {"type", "date", "amount", "description", "note"}
        required = {"type", "date", "amount", "description"}
        self._require_exact_fields(payload, allowed, required, "Manual Input")
        result = self.server.application.create_manual_input(
            transaction_type=payload["type"],
            transaction_date=payload["date"],
            amount=payload["amount"],
            description=payload["description"],
            note=payload.get("note"),
        )
        self._send_json(HTTPStatus.CREATED, {"manual_input": result.to_dict()})

    def _handle_manual_input_correction(self, source_record_id: str) -> None:
        payload = self._read_json_object()
        allowed = {"type", "date", "amount", "description", "note"}
        required = {"type", "date", "amount", "description"}
        self._require_exact_fields(
            payload,
            allowed,
            required,
            "Manual Input correction",
        )
        kwargs: dict[str, Any] = {
            "transaction_type": payload["type"],
            "transaction_date": payload["date"],
            "amount": payload["amount"],
            "description": payload["description"],
        }
        if "note" in payload:
            kwargs["note"] = payload["note"]
        result = self.server.application.correct_manual_input(
            source_record_id,
            **kwargs,
        )
        self._send_json(
            HTTPStatus.OK,
            {"manual_input_correction": result.to_dict()},
        )

    def _handle_scheduled_input_create(self) -> None:
        payload = self._read_json_object()
        values = self._scheduled_input_values(payload, "Scheduled Input")
        rule = self.server.application.create_scheduled_input(**values)
        self._send_json(HTTPStatus.CREATED, {"scheduled_input": rule.to_dict()})

    def _handle_scheduled_input_update(self, rule_id: str) -> None:
        payload = self._read_json_object()
        values = self._scheduled_input_values(payload, "Scheduled Input update")
        rule = self.server.application.update_scheduled_input(rule_id, **values)
        self._send_json(HTTPStatus.OK, {"scheduled_input": rule.to_dict()})

    def _scheduled_input_values(
        self,
        payload: dict[str, Any],
        label: str,
    ) -> dict[str, Any]:
        allowed = {
            "type",
            "amount",
            "description",
            "next_date",
            "note",
            "enabled",
        }
        required = {"type", "amount", "description", "next_date", "enabled"}
        self._require_exact_fields(payload, allowed, required, label)
        return {
            "transaction_type": payload["type"],
            "amount": payload["amount"],
            "description": payload["description"],
            "next_date": payload["next_date"],
            "note": payload.get("note"),
            "enabled": payload["enabled"],
        }

    def _handle_mapping_review_preview(self) -> None:
        payload = self._read_json_object()
        allowed = {"description", "merchant", "category"}
        self._require_exact_fields(
            payload,
            allowed,
            allowed,
            "Mapping Review preview",
        )
        preview = self.server.application.preview_mapping_review(
            description=payload["description"],
            merchant=payload["merchant"],
            category=payload["category"],
        )
        self._send_json(HTTPStatus.OK, {"preview": preview.to_dict()})

    def _handle_mapping_review_apply(self) -> None:
        payload = self._read_json_object()
        allowed = {
            "description",
            "merchant",
            "category",
            "preview_token",
            "confirm_new_merchant",
        }
        required = {"description", "merchant", "category", "preview_token"}
        self._require_exact_fields(
            payload,
            allowed,
            required,
            "Mapping Review apply",
        )
        preview = self.server.application.apply_mapping_review(
            description=payload["description"],
            merchant=payload["merchant"],
            category=payload["category"],
            preview_token=payload["preview_token"],
            confirm_new_merchant=payload.get("confirm_new_merchant", False),
        )
        self._send_json(HTTPStatus.OK, {"mapping_review": preview.to_dict()})

    @staticmethod
    def _require_exact_fields(
        payload: dict[str, Any],
        allowed: set[str],
        required: set[str],
        label: str,
    ) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApplicationValidationError(
                f"Unknown {label} fields: {unknown!r}"
            )
        missing = sorted(required - set(payload))
        if missing:
            raise ApplicationValidationError(
                f"{label} is missing required fields: {missing!r}"
            )

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
            raise ApplicationValidationError(
                "Request body must be valid UTF-8 JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ApplicationValidationError(
                "Request body must be a JSON object"
            )
        return payload

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
        return
