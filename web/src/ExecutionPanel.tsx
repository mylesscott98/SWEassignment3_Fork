import { useCallback, useRef, useState, type FormEvent } from "react";
import { ApiError, call, type HistoryEvent, type JobDetail, type User } from "./api";
import { label, when, windowOf } from "./format";
import { usePolled } from "./usePolled";

type Run = (action: () => Promise<unknown>) => Promise<boolean>;
type Action = "attempts" | "issue-reviews" | "custody-transfers";
type Pending = { action: Action; body: Record<string, unknown> & { submission_id: string } };
type Proof = { recipient_name: string; handover_at: string; signature_ref: string | null; photo_ref: string | null };
type Tracking = JobDetail & {
  progress: { custodian_id: string | null; open_issue_id: string | null; collection_window: [string, string] | null };
  attempts: { id: string; delivery_staff_id: string; recorded_at: string; outcome: {
    kind: string; succeeded: boolean; occurred_at: string; failure_reason: string | null; proof: Proof | null;
  } }[];
  issues: { id: string; attempt_id: string; reason: string; raised_at: string }[];
  reviews: { id: string; reviewed_by: string; reviewed_at: string; notes: string;
    issue_addressed: boolean; parcels_with_driver: boolean; collection_window: [string, string] | null }[];
  transfers: { id: string; recorded_by: string; occurred_at: string; from_driver_id: string;
    to_driver_id: string; notes: string }[];
  history: HistoryEvent[];
};

function savedPending(key: string): Pending | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null") as Pending | null;
    if (value && ["attempts", "issue-reviews", "custody-transfers"].includes(value.action)
      && typeof value.body?.submission_id === "string") return value;
  } catch { /* A request is never sent unless its receipt can first be saved. */ }
  return null;
}

export function ExecutionPanel({ user, jobId, run, onChanged }: {
  user: User; jobId: string; run: Run; onChanged: () => void;
}) {
  const key = `yowd-execution:${user.id}:${jobId}`;
  const [pending, setPending] = useState<Pending | null>(() => savedPending(key));
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [message, setMessage] = useState("");
  const [readError, setReadError] = useState("");
  const [generation, setGeneration] = useState(0);
  const [succeeded, setSucceeded] = useState(true);
  const [addressed, setAddressed] = useState(false);
  const [retained, setRetained] = useState(false);
  const load = useCallback(async () => {
    try {
      const result = await call<Tracking>(user, "GET", `/tracking/jobs/${jobId}`);
      setReadError("");
      return result;
    } catch (error) {
      setReadError(String(error));
      throw error;
    }
  }, [user, jobId]);
  const [detail, refresh] = usePolled<Tracking>(load, 3000);

  async function send(request: Pending) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    let definitiveRefusal = false;
    try {
      localStorage.setItem(key, JSON.stringify(request));
      setPending(request);
      const ok = await run(async () => {
        try {
          await call<unknown>(user, "POST", `/jobs/${jobId}/${request.action}`, request.body);
        } catch (error) {
          definitiveRefusal = error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status);
          throw error;
        }
      });
      if (ok || definitiveRefusal) {
        localStorage.removeItem(key);
        setPending(null);
      }
      if (ok) {
        setMessage("Saved successfully. Earlier attempts and failures remain in the history.");
        setGeneration(n => n + 1);
        setSucceeded(true);
        setAddressed(false);
        setRetained(false);
      } else {
        setMessage(definitiveRefusal
          ? "The server refused this input. Check the error, refresh the job and correct the draft."
          : "The outcome is not confirmed. Do not create another submission. Retry the saved request below; its ID is unchanged.");
      }
    } catch (error) {
      setMessage(`Could not safely save the submission state: ${String(error)}. Check tracking before resubmitting.`);
    } finally {
      refresh();
      onChanged();
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>, action: Action) {
    event.preventDefault();
    if (busy || pending || !detail) return;
    const form = new FormData(event.currentTarget);
    const text = (name: string) => String(form.get(name) ?? "").trim();
    const time = (name: string) => new Date(text(name)).toISOString();
    try {
      const body: Pending["body"] = { submission_id: crypto.randomUUID() };
      if (action === "attempts") {
        const kind = detail.job.state === "allocated" ? "pickup" : "delivery";
        const occurred = time("occurred_at");
        const outcome: Record<string, unknown> = { kind, succeeded, occurred_at: occurred };
        if (!succeeded) outcome.failure_reason = text("reason");
        if (succeeded && kind === "delivery") {
          outcome.all_parcels_handed_over = form.has("handover");
          outcome.proof = { recipient_name: text("recipient"), handover_at: occurred,
            ...(text("signature") ? { signature_ref: text("signature") } : {}),
            ...(text("photo") ? { photo_ref: text("photo") } : {}) };
        }
        body.outcome = outcome;
      } else if (action === "issue-reviews") {
        Object.assign(body, { issue_id: detail.progress.open_issue_id, notes: text("notes"),
          issue_addressed: addressed, parcels_with_driver: retained });
        if (addressed && !retained) body.collection_window = [time("start"), time("end")];
      } else {
        Object.assign(body, { from_driver_id: detail.progress.custodian_id,
          occurred_at: time("occurred_at"), notes: text("notes") });
      }
      await send({ action, body });
    } catch (error) {
      setMessage(`Check the required dates and fields: ${String(error)}`);
    }
  }

  if (!detail) return <div className="card"><h3>Delivery execution</h3>
    <p role="status">{readError || "Loading execution history..."}</p><button onClick={refresh}>Retry loading</button></div>;
  const { job, progress } = detail;
  const driverCanAct = user.role === "driver" && job.delivery_staff_id === user.id
    && ["allocated", "in_transit"].includes(job.state);
  const canTransfer = user.role === "staff" && !!progress.custodian_id
    && progress.custodian_id !== job.delivery_staff_id && !!job.delivery_staff_id
    && ["in_transit", "on_hold"].includes(job.state);
  const locked = busy || pending !== null;
  return <>
    <div className="card"><h3>Delivery execution and tracking</h3>
      <p>Status: {label(job.state)}. Parcel custodian: {progress.custodian_id ?? "not with a driver"}.</p>
      {progress.collection_window && <p>Approved recollection: {windowOf(progress.collection_window)}</p>}
      {job.state === "delivered" && <p>Physical handover recorded. Payment and job closure are separate.</p>}
      {user.role === "driver" && job.delivery_staff_id !== user.id && <p>Previously allocated job: tracking only.</p>}
      {readError && <p role="alert">Tracking could not refresh: {readError}</p>}
      {message && <p role="status">{message}</p>}
      {pending && <div role="status"><p>A saved submission needs confirmation. Retry it instead of entering a new attempt.</p>
        <button disabled={busy} onClick={() => send(pending)}>Retry saved submission</button>
        <details><summary>Saved submission details</summary><pre>{JSON.stringify(pending, null, 2)}</pre></details>
      </div>}
      <button onClick={refresh}>Refresh tracking</button>
      {driverCanAct && <form key={`attempt-${generation}`} onSubmit={e => submit(e, "attempts")}
        onReset={() => setSucceeded(true)}><fieldset disabled={locked}>
        <legend>Record {job.state === "allocated" ? "pickup" : "delivery"} attempt</legend>
        <label>Outcome <select value={succeeded ? "success" : "failure"} onChange={e => setSucceeded(e.target.value === "success")}>
          <option value="success">Successful</option><option value="failure">Failed</option>
        </select></label>
        <label>Actual attempt time (local)<input name="occurred_at" type="datetime-local" step="1" required /></label>
        {!succeeded && <label>Failure reason<input name="reason" required maxLength={1000} /></label>}
        {succeeded && job.state === "in_transit" && <>
          <label>Recipient name<input name="recipient" required maxLength={1000} /></label>
          <label><input name="handover" type="checkbox" required />All parcels have been handed over</label>
          <label>Signature reference, when required<input name="signature" maxLength={1000} /></label>
          <label>Photo reference, when required<input name="photo" maxLength={1000} /></label>
          <p>The actual attempt time is the handover time. Store references only, not images.</p>
        </>}
        <button type="submit" className="primary">Save attempt</button><button type="reset">Clear draft</button>
      </fieldset></form>}
      {user.role === "staff" && progress.open_issue_id && <form key={`review-${generation}`}
        onSubmit={e => submit(e, "issue-reviews")} onReset={() => { setAddressed(false); setRetained(false); }}>
        <fieldset disabled={locked}><legend>Office issue review</legend>
          <label>Review and agreed next action<input name="notes" required maxLength={1000} /></label>
          <label><input type="checkbox" checked={addressed} onChange={e => setAddressed(e.target.checked)} />Issue addressed; authorise resumption</label>
          <label><input type="checkbox" checked={retained} onChange={e => setRetained(e.target.checked)} />The currently allocated driver still holds the parcels</label>
          <p>Leave custody unchecked only if parcels were returned or never collected. An unresolved issue remains on hold.</p>
          {addressed && !retained && <>
            <label>Recollection start (local)<input name="start" type="datetime-local" required /></label>
            <label>Recollection end (local)<input name="end" type="datetime-local" required /></label>
          </>}
          <button type="submit" className="primary">Save review</button><button type="reset">Clear draft</button>
        </fieldset>
      </form>}
      {canTransfer && <form key={`transfer-${generation}`} onSubmit={e => submit(e, "custody-transfers")}>
        <fieldset disabled={locked}><legend>Confirm physical custody transfer</legend>
          <p>From {progress.custodian_id} to {job.delivery_staff_id}. Record only after the physical transfer. This does not remove a hold.</p>
          <label>Actual transfer time (local)<input name="occurred_at" type="datetime-local" step="1" required /></label>
          <label>Transfer details<input name="notes" required maxLength={1000} /></label>
          <button type="submit" className="primary">Confirm transfer</button><button type="reset">Clear draft</button>
        </fieldset>
      </form>}
    </div>
    <div className="card"><h3>Actual attempts and proof</h3>
      {detail.attempts.length === 0 && <p>No attempts recorded.</p>}
      <ol className="history">{detail.attempts.map(a => <li key={a.id}>
        {label(a.outcome.kind)}: {a.outcome.succeeded ? "successful" : "failed"}, {when(a.outcome.occurred_at)}, by {a.delivery_staff_id}
        {a.outcome.failure_reason && <p>Failure: {a.outcome.failure_reason}</p>}
        {a.outcome.proof && <p>Recipient: {a.outcome.proof.recipient_name}. Handover: {when(a.outcome.proof.handover_at)}.
          Signature: {a.outcome.proof.signature_ref ?? "not supplied"}. Photo: {a.outcome.proof.photo_ref ?? "not supplied"}.</p>}
      </li>)}</ol>
      <h3>Issue reviews</h3><ol>{detail.reviews.map(r => <li key={r.id}>
        {when(r.reviewed_at)} by {r.reviewed_by}: {r.notes}. {r.issue_addressed ? "Resumption approved" : "Still on hold"}.
        {r.collection_window && ` Recollection: ${windowOf(r.collection_window)}`}
      </li>)}</ol>
      <h3>Custody transfers</h3><ol>{detail.transfers.map(t => <li key={t.id}>
        {when(t.occurred_at)}: {t.from_driver_id} to {t.to_driver_id}, recorded by {t.recorded_by}. {t.notes}
      </li>)}</ol>
    </div>
    <div className="card"><h3>Complete request and job history</h3><ol className="history">{detail.history.map(h => <li key={h.id}>
      {when(h.created_at)}: {label(h.event_type)} {typeof h.action === "string" ? label(h.action) : ""} by {h.created_by ?? "system"}
      {typeof h.resulting_state === "string" && `; ${label(h.resulting_state)}`}
    </li>)}</ol></div>
  </>;
}
