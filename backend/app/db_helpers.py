from __future__ import annotations

from .context import AppContext
from .storage.db import utcnow


def finish_job(ctx: AppContext, job_id: str, state: str, error_code=None, error_message=None, output_mask_id=None) -> None:
    ctx.db.update("jobs", job_id, state=state, stage=state, error_code=error_code, error_message=error_message,
                  output_mask_id=output_mask_id, finished_at=utcnow())
