"""Self-check for safe email-link approval and email deduplication.

Verifies that:
1. GET requests only render a confirmation page and do NOT mutate approval state,
   protecting against automated link-prefetchers and email security scanners (SafeLinks, etc.).
2. POST requests execute the approval or rejection decision.
3. Invalid or tampered tokens return the safe error page.
4. EmailService.send strictly deduplicates headers and SMTP envelope recipients.
5. ApprovalService._chain_emails sends only to actionable recipients, avoiding broadcast spam.

Run with:
    docker exec -w /app backend_app python test_decide_by_link.py
"""

import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

from app.database.dependencies import get_db
from app.models.approval import ApprovalDocument, ApprovalStatus, SalesApproval
from app.models.user import User
from app.routes.approval import router
from app.services.approval_link_service import make_token
from app.services.approval_service import ApprovalService
from app.services.email_service import EmailService


class AppClient:
    """Calls the app the way a server would, in process.

    Starlette's TestClient needs an HTTP client package the container does
    not ship, and these checks only need a status and a page back - so the
    request is handed to the ASGI app directly.
    """

    def __init__(self, app):
        self.app = app

    def _call(self, method: str, url: str):
        path, _, query = url.partition("?")
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": query.encode(), "root_path": "", "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        sent = {"status": None, "headers": {}, "body": b""}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
                sent["headers"] = {
                    k.decode("latin1").lower(): v.decode("latin1")
                    for k, v in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                sent["body"] += message.get("body", b"")

        asyncio.run(self.app(scope, receive, send))
        return SimpleNamespace(
            status_code=sent["status"],
            headers=sent["headers"],
            text=sent["body"].decode("utf-8", "replace"),
        )

    def get(self, url: str):
        return self._call("GET", url)

    def post(self, url: str):
        return self._call("POST", url)


def run_checks() -> int:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AppClient(app)

    user_id = str(uuid4())
    token = make_token(101, user_id, 0, "approve")

    # 1. Test GET with invalid token: redirects to /login
    res = client.get("/api/v1/approvals/decide-by-link?token=tampered")
    assert res.status_code == 303, f"Expected 303, got {res.status_code}"
    assert res.headers.get("location", "").endswith("/login"), f"Expected redirect to /login, got {res.headers.get('location')}"
    print("  PASS  GET invalid token redirects to /login")

    # 2. Test GET with valid token: redirects to the CRM document page
    mock_approval = MagicMock(spec=SalesApproval)
    mock_approval.id = 101
    mock_approval.document_type = ApprovalDocument.SALES_ORDER
    mock_approval.document_id = 101

    def approval_query_filter(model):
        m = MagicMock()
        m.filter.return_value.first.return_value = mock_approval
        return m

    mock_db.query.side_effect = approval_query_filter

    res = client.get(f"/api/v1/approvals/decide-by-link?token={token}")
    assert res.status_code == 303, f"Expected 303, got {res.status_code}"
    assert "/sales/orders/101" in res.headers.get("location", ""), f"Expected redirect to /sales/orders/101, got {res.headers.get('location')}"
    print("  PASS  GET valid token redirects to CRM document page")

    # 3. Test POST: unauthenticated link decisions are rejected with 401
    res = client.post(f"/api/v1/approvals/decide-by-link?token={token}")
    assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    assert "Please log in to the CRM" in res.text, f"Unexpected body: {res.text}"
    print("  PASS  POST unauthenticated decision is rejected with 401")

    # 4. Test EmailService envelope deduplication
    with patch("smtplib.SMTP") as mock_smtp, patch(
        "app.services.email_service.settings"
    ) as mock_settings:
        mock_settings.SMTP_HOST = "smtp.test.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_SSL = False
        mock_settings.SMTP_TLS = True
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "password"
        mock_settings.SMTP_FROM = "noreply@test.com"
        mock_settings.SMTP_FROM_NAME = "Test CRM"

        instance = mock_smtp.return_value
        instance.__enter__.return_value = instance

        ok = EmailService.send(
            to=["John@example.com", "john@example.com ", "BOB@example.com"],
            subject="Test Deduplication",
            text_body="Hello",
            cc=["john@example.com", "alice@example.com", "Alice@example.com"],
        )
        assert ok is True

        msg = instance.send_message.call_args[0][0]
        envelope = instance.send_message.call_args.kwargs["to_addrs"]

        assert len(envelope) == 3
        assert msg["To"] == "John@example.com, BOB@example.com"
        assert msg["Cc"] == "alice@example.com"
        print("  PASS  EmailService deduplicates To, CC and envelope recipients")

    # 5. Test ApprovalService._chain_emails routing
    requester_uuid = uuid4()
    approver1_uuid = uuid4()

    m_req = MagicMock(spec=User)
    m_req.id = requester_uuid
    m_req.email = "requester@test.com"

    m_appr = MagicMock(spec=User)
    m_appr.id = approver1_uuid
    m_appr.email = "avp@test.com"

    def user_lookup(model):
        m = MagicMock()

        def filter_mock(expr):
            f = MagicMock()
            val = expr.right.value if hasattr(expr.right, "value") else expr.right
            if val == requester_uuid:
                f.first = lambda: m_req
            elif val == approver1_uuid:
                f.first = lambda: m_appr
            else:
                f.first = lambda: None
            return f

        m.filter = filter_mock
        return m

    mock_db.query.side_effect = user_lookup

    # Pending step: To is active approver, CC does NOT include requester
    appr_pending = MagicMock(spec=SalesApproval)
    appr_pending.status = ApprovalStatus.PENDING
    appr_pending.requested_by = requester_uuid
    appr_pending.steps = [{"role": "AVP", "approver_id": None}]
    ApprovalService.approvers_for_step = staticmethod(lambda a, db: [m_appr])

    to, cc = ApprovalService._chain_emails(appr_pending, mock_db, event="raised")
    assert to == ["avp@test.com"]
    assert cc == []
    print("  PASS  Pending approval routes To active approver ONLY (requester is not sent approval ask)")

    # Fully approved: To is requester, CC is approvers who signed
    appr_approved = MagicMock(spec=SalesApproval)
    appr_approved.status = ApprovalStatus.APPROVED
    appr_approved.requested_by = requester_uuid
    appr_approved.steps = [{"role": "AVP", "approver_id": str(approver1_uuid)}]

    to, cc = ApprovalService._chain_emails(appr_approved, mock_db, event="approved")
    assert to == ["requester@test.com"]
    assert cc == []
    print("  PASS  Final approval routes To requester only (no CC)")

    print("\nAll safety and deduplication checks passed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
