"""Trust boundary between the control plane (decides, signs) and the data plane (verifies, executes)."""

from .capabilities import CAPABILITIES, CapabilityDescriptor, CapabilityId
from .models import ActionRequest, ActionResult, Approval, BoundaryError, SignedActionRequest
from .queue import ActionQueue

__all__ = [
    "CAPABILITIES", "ActionQueue", "ActionRequest", "ActionResult", "Approval", "BoundaryError", "CapabilityDescriptor",
    "CapabilityId", "SignedActionRequest",
]
