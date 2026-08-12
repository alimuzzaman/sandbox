from __future__ import annotations

from sandbox.core._paths import RUNTIME_DIR

from .service import FeedbackService, FeedbackStore


def feedback_service() -> FeedbackService:
    return FeedbackService(FeedbackStore(RUNTIME_DIR / "feedback"))
