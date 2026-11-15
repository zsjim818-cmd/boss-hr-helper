"""Contexts are supplied by trusted server code, never deserialized as login proof."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AccessContext:
    org_id: str
    user_id: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.org_id, self.user_id)):
            raise ValueError("AccessContext requires nonempty org_id and user_id")


@dataclass(frozen=True)
class ProvisioningContext:
    """Deployment-only identity/org bootstrap; no HTTP endpoint exposes this API."""
    reason: str

    def __post_init__(self):
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Provisioning requires an administrative reason")
