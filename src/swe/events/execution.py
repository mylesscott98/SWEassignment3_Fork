import logging

from swe.domain.job.execution_state import ExecutionChanged
from swe.events.handlers import system
from swe.events.handling import handles

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


@handles(ExecutionChanged)
def notify_execution(event: ExecutionChanged) -> None:
    job = system().repo.get_job(event.job_id)
    log.info(
        "SIMULATED notification to customer %s and office staff: job %s, %s, state %s, record %s",
        job.customer_id,
        event.job_id,
        event.action,
        event.resulting_state,
        event.record_id,
    )
    if event.action == "issue_resumed" and job.delivery_staff_id:
        log.info("SIMULATED notification to driver %s: job %s resumed", job.delivery_staff_id, job.job_id)
