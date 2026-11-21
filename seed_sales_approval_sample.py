"""Seed sample sales data and permissions for end-to-end approval flow testing.

Sets up:
1. Permissions: grants sales permissions to sales roles (Zonal Head, AVP, CEO, Founder, Area Manager).
2. Demo credentials: clears must_change_password for all demo accounts.
3. Hierarchy: confirms the reporting chain Zonal Head -> AVP -> CEO -> Founder.
4. Sample Orders & Approvals raised by Zonal Head:
   - Order 1: SO-NORTH-001 with 25% discount (AVP -> CEO -> Founder)
   - Order 2: SO-NORTH-002 with 15% discount (AVP -> CEO)
"""

import sys
from datetime import datetime, timezone

from app.database.postgres import SessionLocal
from app.models.approval import ApprovalDocument, ApprovalStatus, SalesApproval
from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.sales_order import SalesOrder
from app.models.user import User
from app.services.approval_service import ApprovalService
from app.services.password_service import PasswordService


def seed_sample_data():
    db = SessionLocal()
    try:
        print("=== 1. Ensuring Sales Permissions on Roles ===")
        sales_perm_names = [
            "sales.menu", "order.read", "quotation.read",
            "opportunity.read", "lead.read", "customer.read"
        ]
        perms = db.query(Permission).filter(Permission.permission_name.in_(sales_perm_names)).all()
        perm_map = {p.permission_name: p for p in perms}

        sales_roles = db.query(Role).filter(Role.role_name.in_([
            "Founder", "CEO", "AVP", "Zonal Head", "Area Manager", "Sales Person"
        ])).all()

        for role in sales_roles:
            for p_name, p_obj in perm_map.items():
                exists = db.query(RolePermission).filter(
                    RolePermission.role_id == role.id,
                    RolePermission.permission_id == p_obj.id,
                ).first()
                if not exists:
                    db.add(RolePermission(role_id=role.id, permission_id=p_obj.id))
        db.commit()
        print(f"  Sales permissions verified on {len(sales_roles)} roles.")

        print("\n=== 2. Setting Up Demo Accounts & Passwords ===")
        demo_emails = [
            "founder@mailinator.com",
            "ceo@mailinator.com",
            "avp@mailinator.com",
            "zh.north@mailinator.com",
            "am.north.1@mailinator.com",
            "superadmin@mailinator.com",
        ]
        users = {u.email: u for u in db.query(User).filter(User.email.in_(demo_emails)).all()}

        hashed_team_pw = PasswordService.hash_password("Synergy@123")
        for email, u in users.items():
            u.must_change_password = False
            u.is_active = True
            if email != "superadmin@mailinator.com":
                u.password = hashed_team_pw
        db.commit()
        print(f"  {len(users)} demo users updated with must_change_password=False and active status.")

        print("\n=== 3. Verifying Reporting Chain ===")
        founder = users.get("founder@mailinator.com")
        ceo = users.get("ceo@mailinator.com")
        avp = users.get("avp@mailinator.com")
        zh = users.get("zh.north@mailinator.com")
        am = users.get("am.north.1@mailinator.com")

        if am and zh:
            am.reports_to_id = zh.id
        if zh and avp:
            zh.reports_to_id = avp.id
        if avp and ceo:
            avp.reports_to_id = ceo.id
        if ceo and founder:
            ceo.reports_to_id = founder.id
        if founder:
            founder.reports_to_id = None
        db.commit()
        print("  Reporting line: Area Manager -> Zonal Head -> AVP -> CEO -> Founder verified.")

        print("\n=== 4. Creating Sample Sales Orders & Approvals ===")
        # Remove any old sample test orders so we start clean
        old_orders = db.query(SalesOrder).filter(
            SalesOrder.order_number.in_(["SO-NORTH-001", "SO-NORTH-002"])
        ).all()
        for o in old_orders:
            db.query(SalesApproval).filter(
                SalesApproval.document_type == ApprovalDocument.SALES_ORDER,
                SalesApproval.document_id == o.id,
            ).delete()
            db.delete(o)
        db.commit()

        # Order 1: 25% discount (AVP -> CEO -> Founder)
        order1 = SalesOrder(
            order_number="SO-NORTH-001",
            customer_name="Apex Technologies Pvt Ltd",
            order_date=datetime.now(timezone.utc),
            status="DRAFT",
            creator_id=zh.id,
            creator_name=f"{zh.first_name} {zh.last_name}".strip(),
            total_amount=250000.0,
            discount_amount=62500.0,
            gst_amount=33750.0,
            grand_total=221250.0,
            discount_input=25.0,
            discount_mode="PERCENT",
            billing_address={"city": "Delhi", "state": "Delhi", "country": "India"},
            items=[
                {
                    "sku": "SG-IFP-65-SPX-V100",
                    "product": "Interactive Flat Panel",
                    "model": "65\" SPX",
                    "qty": 5,
                    "rate": 50000.0,
                    "discount": 25.0,
                    "line_total": 187500.0,
                }
            ],
            remarks="High-volume enterprise client order requiring multi-tier discount approval.",
        )
        db.add(order1)
        db.commit()
        db.refresh(order1)

        # Request approval for Order 1
        approval1 = ApprovalService.request(
            document_type=ApprovalDocument.SALES_ORDER,
            document_id=order1.id,
            document_number=order1.order_number,
            price_type="ECP",
            discount_percent=25.0,
            discount_amount=62500.0,
            orc_percent=None,
            orc_amount=None,
            document_value=order1.grand_total,
            remarks=order1.remarks,
            current_user={"user_id": str(zh.id)},
            db=db,
        )

        status1_str = approval1.status.value if hasattr(approval1.status, "value") else str(approval1.status)
        print(f"  Created Order: {order1.order_number} (ID: {order1.id})")
        print(f"  Approval ID: {approval1.id}, Status: {status1_str}, Waiting on: {approval1.steps[approval1.current_step]['role']}")
        print(f"  Steps: {[s['role'] for s in approval1.steps]}")

        # Order 2: 15% discount (AVP -> CEO)
        order2 = SalesOrder(
            order_number="SO-NORTH-002",
            customer_name="Zenith Retail Solutions",
            order_date=datetime.now(timezone.utc),
            status="DRAFT",
            creator_id=zh.id,
            creator_name=f"{zh.first_name} {zh.last_name}".strip(),
            total_amount=150000.0,
            discount_amount=22500.0,
            gst_amount=22950.0,
            grand_total=150450.0,
            discount_input=15.0,
            discount_mode="PERCENT",
            billing_address={"city": "Jaipur", "state": "Rajasthan", "country": "India"},
            items=[
                {
                    "sku": "SG-IFP-65-SPX-V100",
                    "product": "Interactive Flat Panel",
                    "model": "65\" SPX",
                    "qty": 3,
                    "rate": 50000.0,
                    "discount": 15.0,
                    "line_total": 127500.0,
                }
            ],
            remarks="Retail chain rollout discount.",
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)

        approval2 = ApprovalService.request(
            document_type=ApprovalDocument.SALES_ORDER,
            document_id=order2.id,
            document_number=order2.order_number,
            price_type="ECP",
            discount_percent=15.0,
            discount_amount=22500.0,
            orc_percent=None,
            orc_amount=None,
            document_value=order2.grand_total,
            remarks=order2.remarks,
            current_user={"user_id": str(zh.id)},
            db=db,
        )

        status2_str = approval2.status.value if hasattr(approval2.status, "value") else str(approval2.status)
        print(f"  Created Order: {order2.order_number} (ID: {order2.id})")
        print(f"  Approval ID: {approval2.id}, Status: {status2_str}, Waiting on: {approval2.steps[approval2.current_step]['role']}")
        print(f"  Steps: {[s['role'] for s in approval2.steps]}")

        print("\n=== Seeding Complete Successfully ===")
        return order1.id, order2.id
    finally:
        db.close()


if __name__ == "__main__":
    seed_sample_data()
