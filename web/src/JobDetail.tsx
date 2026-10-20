import { useCallback, useState } from "react";
import { api, type Candidate, type JobDetail as Detail, type User } from "./api";
import { ExecutionPanel } from "./ExecutionPanel";
import { label, when, windowOf } from "./format";
import { usePolled } from "./usePolled";

type Run = (action: () => Promise<unknown>) => Promise<boolean>;

// states a job can be (re)allocated in; delivered and closed jobs keep their driver
const ASSIGNABLE = ["unallocated", "allocated", "in_transit", "on_hold"];

export function JobDetail({ user, jobId, run }: { user: User; jobId: string; run: Run }) {
  const load = useCallback(() => api(user).getJob(jobId), [user, jobId]);
  // the job is created by an event handler just after acceptance, so it may 404 at first; check often
  const [detail, refresh] = usePolled<Detail>(load, 1500);
  if (!detail) return <div className="card muted">Creating the delivery job…</div>;

  const { job, request, quote, allocations } = detail;
  return (
    <div className="detail">
      <div className="card">
        <div className="title">
          <h2>
            {request.pickup_address.suburb} → {request.dropoff_address.suburb}
          </h2>
          <span className={`badge ${job.state}`}>{label(job.state)}</span>
        </div>
        <dl>
          <dt>Job</dt>
          <dd className="mono">{job.job_id}</dd>
          <dt>Request</dt>
          <dd className="mono">{request.request_id}</dd>
          <dt>Customer</dt>
          <dd>{job.customer_id}</dd>
          <dt>Contact</dt>
          <dd>
            {request.contact_name}, {request.contact_phone}
          </dd>
          <dt>Parcels</dt>
          <dd>{request.parcels.map((p) => `${p.quantity}× ${p.size}, ${p.weight_kg} kg`).join("; ")}</dd>
          <dt>Pickup</dt>
          <dd>
            {request.pickup_address.street}, {request.pickup_address.suburb} {request.pickup_address.postcode}
          </dd>
          <dt>Drop-off</dt>
          <dd>
            {request.dropoff_address.street}, {request.dropoff_address.suburb} {request.dropoff_address.postcode}
          </dd>
          <dt>Created</dt>
          <dd>{when(job.created_at)}</dd>
          <dt>Driver</dt>
          <dd>{job.delivery_staff_id ?? "not allocated yet"}</dd>
        </dl>
      </div>

      {user.role === "staff" && ASSIGNABLE.includes(job.state) && (
        <Allocate user={user} jobId={job.job_id} current={job.delivery_staff_id} pickup={quote.eta_pickup} run={(a) => run(a).then((ok) => (refresh(), ok))} />
      )}

      <div className="card">
        <h3>Accepted quote</h3>
        <div className="quote">
          <div className="title">
            <strong>${quote.price}</strong>
          </div>
          <div className="muted">
            Pickup {windowOf(quote.eta_pickup)} · Drop-off {windowOf(quote.eta_dropoff)}
          </div>
          <div className="muted">
            Issued by {quote.issued_by} · accepted {quote.responded_at && when(quote.responded_at)}
          </div>
        </div>
      </div>

      <ExecutionPanel key={`${job.job_id}-${user.id}`} user={user} jobId={job.job_id} run={run} onChanged={refresh} />

      {allocations.length > 0 && (
        <div className="card">
          <h3>Allocations</h3>
          <ol className="history">
            {allocations.map((a) => (
              <li key={a.id}>
                <strong>{a.delivery_staff_id}</strong> by {a.created_by}
                <span className="muted">
                  {" "}
                  · {when(a.created_at)}
                  {a.reason && ` · reallocated: ${a.reason}`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

type AllocateProps = { user: User; jobId: string; current: string | null; pickup: [string, string]; run: Run };

// Task 1: the drivers free for the whole pickup window, least busy first
function Allocate({ user, jobId, current, pickup, run }: AllocateProps) {
  const load = useCallback(() => api(user).candidates(jobId), [user, jobId]);
  const [candidates, refresh] = usePolled<Candidate[]>(load);
  const [reason, setReason] = useState("");
  const others = candidates?.filter((c) => c.driver.user_id !== current);

  async function assign(driverId: string) {
    const client = api(user);
    const ok = await run(() => (current ? client.reallocate(jobId, driverId, reason) : client.allocate(jobId, driverId)));
    refresh();
    if (ok) setReason("");
  }

  return (
    <div className="card">
      <h3>{current ? "Reallocate to another driver" : "Allocate a driver"}</h3>
      <div className="muted">Pickup window {windowOf(pickup)}</div>
      {current && <input placeholder="Reason for reallocating" value={reason} onChange={(e) => setReason(e.target.value)} />}
      {others?.length === 0 && (
        <p className="muted">No other driver is free for the whole pickup window. Renegotiate the window with the customer.</p>
      )}
      {others?.map(({ driver, load }) => (
        <div key={driver.user_id} className="quote title">
          <span>
            <strong>{driver.name}</strong> <span className="muted">({driver.user_id})</span>
          </span>
          <span className="muted">
            {load} active {load === 1 ? "job" : "jobs"}
          </span>
          <button className="primary" disabled={!!current && !reason.trim()} onClick={() => assign(driver.user_id)}>
            {current ? "Reallocate" : "Allocate"}
          </button>
        </div>
      ))}
    </div>
  );
}
