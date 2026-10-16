"""Self-check for safe email-link approval.

Verifies that:
1. GET requests only render a confirmation page and do NOT mutate approval state,
   protecting against automated link-prefetchers and email security scanners (SafeLinks, etc.).
2. POST requests execute the approval or rejection decision.
3. Invalid or tampered tokens return the safe error page.

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

    print("\nAll decide-by-link safety checks passed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
