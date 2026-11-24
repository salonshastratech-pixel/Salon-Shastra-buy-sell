"""Capability-based access control (PRD §3.2).

Roles grant default capabilities; `user_grants` add explicit, auditable, optionally
record-scoped and time-limited capabilities (e.g. a team lead designated as listing
approver, or a member granted access to one seller's restricted fields).

Every check happens server-side. Templates only use `can()` to hide controls.
Matrix version is recorded so audit entries can be interpreted later.
"""
from __future__ import annotations

from .timeutil import now_iso

MATRIX_VERSION = "2026-11-24.v1 (simple: team = everything except Finance; D-85)"

# Capability catalogue -------------------------------------------------------------
CAPABILITIES = {
    "crm.view": "View company-wide operational seller/buyer records",
    "crm.create": "Create leads, contacts and opportunities",
    "crm.edit": "Edit routine profile facts and assessment drafts (change-logged)",
    "crm.import": "Upload spreadsheets and commit bulk lead imports",
    "crm.assign": "Assign/reassign opportunities and tasks to other people",
    "crm.log_activity": "Log calls, notes and outcomes",
    "stage.operational": "Advance contact/assessment stages and holding states",
    "evidence.review": "Mark facts as staff-reviewed / document-supported / verified / disputed",
    "listing.approve": "Approve or reject a seller listing; close a deal as won",
    "seller.view_restricted": "See restricted seller fields (personal sale reasons, private notes)",
    "buyer.view_funding": "See buyer funding-proof detail (evidence notes and limitations)",
    "duplicates.resolve": "Resolve duplicate-contact review items",
    "users.manage": "Create, deactivate users and grant capabilities",
    "audit.view": "View the audit log",
    "finance.view": "View Salon Shastra fees, invoices, collections and costs",
    "finance.edit": "Record fee agreements, invoices, receipts and company expenses",
    "settings.manage": "Change integrations, secrets and global settings",
    "export.restricted": "Export restricted data",
}

# Founder decision (24 Nov 2026): keep it simple — the team can do everything the admin can, except Finance.
# Only adding/removing users and changing integrations & secrets stay with the admin.
_ADMIN_ONLY = {"finance.view", "finance.edit", "users.manage", "settings.manage"}
_TEAM = set(CAPABILITIES) - _ADMIN_ONLY

ROLE_CAPABILITIES: dict[str, set[str]] = {
    "super_admin": set(CAPABILITIES),
    "team_lead": set(_TEAM),
    "team_member": set(_TEAM),
    "finance": {"finance.view", "finance.edit"},
}

ROLE_LABELS = {
    "super_admin": "Admin",
    "team_lead": "Team member",
    "team_member": "Team member",
    "finance": "Finance only",
}
# What the Users page offers (team_lead is kept only for older accounts; it has the same access as team_member).
ROLE_CHOICES = {
    "team_member": "Team member — everything except Finance",
    "super_admin": "Admin — everything, including Finance and users",
    "finance": "Finance only",
}


def role_has(role: str, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, set())


def user_can(db, user: dict | None, capability: str, scope_type: str | None = None, scope_id: int | None = None) -> bool:
    if not user or not user.get("is_active"):
        return False
    if role_has(user["role"], capability):
        return True
    now = now_iso()
    grants = db.all(
        "SELECT scope_type, scope_id FROM user_grants WHERE user_id = ? AND capability = ? "
        "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
        (user["id"], capability, now),
    )
    for gr in grants:
        if gr["scope_type"] is None:
            return True
        if scope_type and gr["scope_type"] == scope_type and gr["scope_id"] == scope_id:
            return True
    return False


def can_view_restricted_seller(db, user: dict, opportunity: dict) -> bool:
    """Restricted seller fields: approvers/admins, explicit grant, or the primary owner who collected them."""
    if user_can(db, user, "seller.view_restricted", "opportunity", opportunity["id"]):
        return True
    return opportunity.get("primary_owner_id") == user["id"]
