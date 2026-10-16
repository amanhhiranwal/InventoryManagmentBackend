"""Self-check for safe email-link approval and email deduplication.

Verifies that:
1. GET requests only render a confirmation page and do NOT mutate approval state,
   protecting against automated link-prefetchers and email security scanners (SafeLinks, etc.).
2. POST requests execute the approval or rejection decision.
3. Invalid or tampered tokens return the safe error page.
4. EmailService.send strictly deduplicates headers and SMTP envelope recipients.
5. ApprovalService._chain_emails sends only to actionable recipients, avoiding broadcast spam.

Run with:
    venv/bin/python test_decide_by_link.py
"""

import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.dependencies import get_db
from app.models.approval import ApprovalStatus, SalesApproval
from app.models.user import User
from app.routes.approval import router
from app.services.approval_link_service import make_token
from app.services.approval_service import ApprovalService
from app.services.email_service import EmailService


def run_checks() -> int:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)

    user_id = str(uuid4())
    token = make_token(101, user_id, 0, "approve")

    # 1. Test GET with invalid token
    res = client.get("/api/v1/approvals/decide-by-link?token=tampered")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "no longer valid" in res.text, "Expected 'no longer valid' message"
    print("  PASS  GET invalid token rejected safely")

    # 2. Test GET with valid token: renders confirmation page, does NOT decide
    mock_approval = MagicMock(spec=SalesApproval)
    mock_approval.id = 101
    mock_approval.document_type = "SALES_ORDER"
    mock_approval.document_number = "SO-001"
    mock_approval.document_id = 101
    mock_approval.status = ApprovalStatus.PENDING
    mock_approval.current_step = 0
    mock_approval.steps = [{"role": "AVP"}]
    mock_approval.requested_by_name = "Demo User"
    mock_approval.document_value = 50000.0
    mock_approval.discount_percent = 12.0

    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.is_active = True
    mock_user.is_super_admin = False

    with patch(
        "app.services.approval_service.ApprovalService.validate_link",
        return_value=(mock_approval, mock_user),
    ) as mock_val, patch(
        "app.services.approval_service.ApprovalService.decide_by_link"
    ) as mock_dec:
        res = client.get(f"/api/v1/approvals/decide-by-link?token={token}")
        assert res.status_code == 200
        assert "Confirm Approve" in res.text
        assert "SO-001" in res.text
        assert '<form method="POST"' in res.text
        mock_val.assert_called_once()
        mock_dec.assert_not_called()
        print("  PASS  GET renders confirmation page and does NOT decide")

    # 3. Test POST with valid token: executes decision
    mock_approval.steps[0]["role"] = "CEO"
    with patch(
        "app.services.approval_service.ApprovalService.decide_by_link",
        return_value=mock_approval,
    ) as mock_dec:
        res = client.post(f"/api/v1/approvals/decide-by-link?token={token}")
        assert res.status_code == 200
        assert "has been approved" in res.text
        mock_dec.assert_called_once()
        print("  PASS  POST executes decision")

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

    # Pending step: To is active approver, CC is requester
    appr_pending = MagicMock(spec=SalesApproval)
    appr_pending.status = ApprovalStatus.PENDING
    appr_pending.requested_by = requester_uuid
    appr_pending.steps = [{"role": "AVP", "approver_id": None}]
    ApprovalService.approvers_for_step = staticmethod(lambda a, db: [m_appr])

    to, cc = ApprovalService._chain_emails(appr_pending, mock_db, event="raised")
    assert to == ["avp@test.com"]
    assert cc == ["requester@test.com"]
    print("  PASS  Pending approval routes To active approver, CC to requester")

    # Fully approved: To is requester, CC is approvers who signed
    appr_approved = MagicMock(spec=SalesApproval)
    appr_approved.status = ApprovalStatus.APPROVED
    appr_approved.requested_by = requester_uuid
    appr_approved.steps = [{"role": "AVP", "approver_id": str(approver1_uuid)}]

    to, cc = ApprovalService._chain_emails(appr_approved, mock_db, event="approved")
    assert to == ["requester@test.com"]
    assert cc == ["avp@test.com"]
    print("  PASS  Final approval routes To requester, CC to approvers who signed")

    print("\nAll safety and deduplication checks passed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
