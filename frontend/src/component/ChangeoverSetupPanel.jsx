import React, { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { useNotification } from "../context/NotificationContext";
import "../css/setuptime.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

const minutes = (value) =>
  value === null || value === undefined ? "--" : Number(value).toFixed(1);

// <input type="datetime-local"> wants exactly "YYYY-MM-DDTHH:mm". Every
// timestamp here is naive local wall-clock already (USE_TZ is False), so
// the leading 16 characters of the ISO string are the answer -- parsing
// through Date() would only risk a timezone shift on the way back out.
const toDateTimeInput = (value) => {
  if (!value) return "";
  const text = String(value);
  return text.length >= 16 && text[10] === "T" ? text.slice(0, 16) : "";
};

const clock = (value) => {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value).slice(11, 16)
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

/**
 * One changeover's setup time, split across the operators who worked it.
 *
 * Lives inside the changeover detail modal rather than behind a batch
 * lookup of its own: the person questioning a figure is already looking
 * at that changeover, and asking them to copy a batch number into a
 * search box somewhere else is work the screen can do for them.
 *
 * Interruptions are recorded here for the same reason -- the changeover
 * is known, so there is no batch field to fill in or get wrong.
 */
const ChangeoverSetupPanel = ({ changeoverId }) => {
  const { showNotification } = useNotification();

  // The API enforces all of this; the UI only decides what to show.
  // Shift incharges and managers hold the same signature here -- they
  // assign unattributed time and they approve interruptions.
  const currentUser = JSON.parse(localStorage.getItem("user") || "{}");
  const canSupervise = ["manager", "shift_incharge"].includes(currentUser.role);

  // Withdrawing an interruption moves minutes back onto whoever was on
  // the line, so it belongs to a supervisor or to the person who
  // recorded it -- not to any operator who happens to be looking.
  const canRemove = (item) =>
    canSupervise ||
    (!!currentUser.employee_id &&
      item.recorded_by_employee_id === currentUser.employee_id);

  const [detail, setDetail] = useState(null);
  const [reasons, setReasons] = useState([]);
  const [operators, setOperators] = useState([]);
  const [assigning, setAssigning] = useState(null);   // segment_start
  const [assignTo, setAssignTo] = useState("");
  const [reviewing, setReviewing] = useState(null);   // interruption id
  const [rejecting, setRejecting] = useState(null);   // interruption id
  const [rejectNote, setRejectNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    reason: "",
    note: "",
    started_at: "",
    ended_at: "",
  });

  const authHeaders = useCallback(() => ({
    headers: { Authorization: `Token ${localStorage.getItem("authToken")}` },
  }), []);

  const readError = (err, fallback) => {
    const data = err.response?.data;
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (data.non_field_errors?.length) return data.non_field_errors[0];
    for (const key of ["note", "ended_at", "started_at", "reason",
                       "changeover", "error", "detail"]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === "string") return value;
    }
    return fallback;
  };

  const load = useCallback(async () => {
    if (!changeoverId) return;
    setLoading(true);
    try {
      const response = await axios.get(
        `${API_BASE_URL}/api/setup-time/${changeoverId}/`, authHeaders());
      setDetail(response.data);
      setError("");
    } catch (err) {
      setError(readError(err, "Could not load the setup split."));
    } finally {
      setLoading(false);
    }
  }, [changeoverId, authHeaders]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!canSupervise) return undefined;
    let cancelled = false;
    axios
      .get(`${API_BASE_URL}/api/duty/operators/`, authHeaders())
      .then((response) => {
        if (cancelled) return;
        setOperators(response.data);
      })
      .catch(() => {
        // Assignment simply stays unavailable.
      });
    return () => { cancelled = true; };
  }, [authHeaders, canSupervise]);

  useEffect(() => {
    let cancelled = false;
    axios
      .get(`${API_BASE_URL}/api/overshoot-options/`, authHeaders())
      .then((response) => {
        if (cancelled) return;
        setReasons(response.data);
        setForm((current) =>
          current.reason
            ? current
            : { ...current, reason: String(response.data[0]?.id || "") });
      })
      .catch(() => {
        // The select stays empty and the server rejects a submission
        // without a reason, which is the honest failure.
      });
    return () => { cancelled = true; };
  }, [authHeaders]);

  // The master's own "Other" rows are the vague ones, so they need a
  // note. Matching on the text keeps this in step with whatever managers
  // call those rows, at the cost of depending on that wording.
  const noteRequired = useMemo(() => {
    const chosen = reasons.find((r) => String(r.id) === String(form.reason));
    if (!chosen) return false;
    return `${chosen.category} ${chosen.reason}`.toLowerCase().includes("other");
  }, [reasons, form.reason]);

  // Both ends default to the setup's own start: it saves retyping a date
  // already on screen, and the day and hour are nearly always right, so
  // only the minutes need changing.
  //
  // That makes an untouched form a zero-length pause, which the server
  // refuses -- deliberately. Somebody who submits without setting the
  // times has not said when the line stopped, and silently recording a
  // pause of no length would be worse than saying so.
  const openForm = () => {
    const setupStart = toDateTimeInput(detail?.setup_start);
    setForm((current) => ({
      ...current,
      started_at: current.started_at || setupStart,
      ended_at: current.ended_at || setupStart,
    }));
    setAdding(true);
  };

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      // No changeover: a stoppage is a fact about the line, matched to
      // whichever setup windows it overlaps when they are rebuilt. The
      // times default to this setup's own, so one added here lands on
      // it as the person expects.
      await axios.post(`${API_BASE_URL}/api/line-stoppages/`, {
        reason: form.reason,
        note: form.note,
        started_at: form.started_at,
        ended_at: form.ended_at || null,
      }, authHeaders());

      showNotification("Interruption recorded. Setup time updated.", "success");
      const setupStart = toDateTimeInput(detail?.setup_start);
      setForm({
        ...form,
        note: "",
        started_at: setupStart,
        ended_at: setupStart,
      });
      setAdding(false);
      await load();
    } catch (err) {
      showNotification(readError(err, "Could not record that."), "error");
    } finally {
      setSaving(false);
    }
  };

  const assign = async (segmentStart) => {
    if (!assignTo) return;
    try {
      await axios.post(
        `${API_BASE_URL}/api/setup-time/${changeoverId}/attribute/`,
        { segment_start: segmentStart, employee_id: assignTo },
        authHeaders());
      showNotification("Setup time assigned.", "success");
      setAssigning(null);
      setAssignTo("");
      await load();
    } catch (err) {
      showNotification(readError(err, "Could not assign that."), "error");
    }
  };

  const unassign = async (segmentStart) => {
    try {
      await axios.delete(
        `${API_BASE_URL}/api/setup-time/${changeoverId}/attribute/` +
        `?segment_start=${encodeURIComponent(segmentStart)}`,
        authHeaders());
      showNotification("Assignment removed.", "success");
      await load();
    } catch (err) {
      showNotification(readError(err, "Could not undo that."), "error");
    }
  };

  const review = async (interruptionId, action, note = "") => {
    setReviewing(interruptionId);
    try {
      await axios.post(
        `${API_BASE_URL}/api/line-stoppages/${interruptionId}/review/`,
        { action, note }, authHeaders());
      showNotification(
        action === "approve"
          ? "Approved. The delay no longer counts as setup time."
          : "Rejected. Those minutes count as setup time again.",
        "success");
      setRejecting(null);
      setRejectNote("");
      await load();
    } catch (err) {
      showNotification(readError(err, "Could not record that."), "error");
    } finally {
      setReviewing(null);
    }
  };

  const remove = async (interruptionId) => {
    try {
      await axios.delete(
        `${API_BASE_URL}/api/line-stoppages/${interruptionId}/`,
        authHeaders());
      showNotification("Interruption removed. Setup time updated.", "success");
      await load();
    } catch (err) {
      showNotification(readError(err, "Could not remove that."), "error");
    }
  };

  if (loading) return <div className="stm-empty">Loading setup split…</div>;
  if (error) return <div className="stm-banner stm-banner-error">{error}</div>;
  if (!detail) return null;

  const totalAllocated = detail.allocations.reduce(
    (sum, row) => sum + (row.allocated_minutes || 0), 0);
  const totalPaused = detail.allocations.reduce(
    (sum, row) => sum + (row.interrupted_minutes || 0), 0);
  const totalWindow = totalAllocated + totalPaused;
  const awaiting = detail.interruptions.filter(
    (item) => item.status === "pending").length;

  return (
    <div className="csp-panel">
      <div className="csp-head">
        <h4 className="csp-title">Setup time by operator</h4>
        {/* Spelled out as a sum, because the two columns do not add up
            to anything obvious on their own -- and the whole argument an
            operator might make is "that delay was not my setup time". */}
        <span className="csp-window">
          {clock(detail.setup_start)} – {clock(detail.setup_complete)}
          {" · "}{minutes(totalWindow)} min on the line
          {totalPaused > 0 && (
            <>
              {" = "}{minutes(totalAllocated)} setup
              {" + "}{minutes(totalPaused)} delay
            </>
          )}
          {awaiting > 0 && (
            <span className="csp-awaiting">
              {awaiting} awaiting approval
            </span>
          )}
        </span>
      </div>

      {detail.allocations.length === 0 ? (
        <p className="stm-muted">
          No split yet — this changeover has no resolved setup window.
        </p>
      ) : (
        <table className="stm-table stm-table-compact">
          <thead>
            <tr>
              <th>Operator</th>
              <th>Shift</th>
              <th>From</th>
              <th>To</th>
              <th className="stm-num">Interruptions (min)</th>
              <th className="stm-num">Setup Time (min)</th>
            </tr>
          </thead>
          <tbody>
            {detail.allocations.map((row) => (
              <tr key={row.id}>
                <td className="stm-strong">
                  {row.employee_id ? (
                    <>
                      {row.operator_name}
                      <span className="stm-muted"> · {row.employee_id}</span>
                      {row.is_manual && (
                        <span className="csp-badge csp-badge-manual"
                              title="Assigned by a supervisor, not from a sign-in">
                          assigned
                        </span>
                      )}
                      {row.is_manual && canSupervise && (
                        <button type="button" className="csp-link"
                                onClick={() => unassign(row.segment_start)}>
                          undo
                        </button>
                      )}
                    </>
                  ) : assigning === row.segment_start ? (
                    <span className="csp-assign">
                      <select className="stm-select csp-assign-select"
                              value={assignTo}
                              onChange={(e) => setAssignTo(e.target.value)}>
                        <option value="">Who worked this?</option>
                        {operators.map((person) => (
                          <option key={person.employee_id}
                                  value={person.employee_id}>
                            {person.name} ({person.employee_id})
                          </option>
                        ))}
                      </select>
                      <button type="button" className="csp-add"
                              disabled={!assignTo}
                              onClick={() => assign(row.segment_start)}>
                        Assign
                      </button>
                      <button type="button" className="csp-cancel"
                              onClick={() => { setAssigning(null); setAssignTo(""); }}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <>
                      <span className="stm-muted">Nobody signed in</span>
                      {canSupervise && (
                        <button type="button" className="csp-link"
                                onClick={() => { setAssigning(row.segment_start); setAssignTo(""); }}>
                          assign
                        </button>
                      )}
                    </>
                  )}
                </td>
                <td>{row.shift}</td>
                <td>{clock(row.segment_start)}</td>
                <td>{clock(row.segment_end)}</td>
                <td className="stm-num">{minutes(row.interrupted_minutes)}</td>
                <td className="stm-num stm-strong">
                  {minutes(row.allocated_minutes)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="csp-subhead">
        <h4 className="csp-title">Line stoppages</h4>
        {!adding && (
          <button type="button" className="csp-add" onClick={openForm}>
            + Record one
          </button>
        )}
      </div>

      {detail.interruptions.length === 0 ? (
        <p className="stm-muted">
          None recorded. A stoppage during this setup is not setup time —
          report it from the header while it is happening, or add it here
          afterwards, and those minutes stop counting against the operator.
        </p>
      ) : (
        <ul className="stm-interruptions">
          {detail.interruptions.map((item) => (
            <li key={item.id} className={item.counts ? "" : "csp-pending"}>
              <span className="stm-tag">{item.category}</span>
              {item.reason_label}
              {" · "}{clock(item.started_at)} – {item.ended_at ? clock(item.ended_at) : "ongoing"}
              {item.duration_minutes !== null && (
                <strong> · {minutes(item.duration_minutes)} min</strong>
              )}
              {item.note && <em> · {item.note}</em>}
              <span className="stm-muted"> · {item.recorded_by_label}</span>

              <span className={`csp-badge csp-badge-${item.status}`}>
                {item.status_display}
              </span>
              {item.status === "rejected" && item.review_note && (
                <span className="stm-muted"> · {item.review_note}</span>
              )}

              {/* Only an approved one has taken minutes off anybody, so
                  say plainly when it has not. */}
              {item.status === "pending" && (
                <span className="csp-note">
                  still counts as setup time until approved
                </span>
              )}

              {canSupervise && item.status !== "approved" && (
                <button type="button" className="csp-approve"
                        disabled={reviewing === item.id}
                        onClick={() => review(item.id, "approve")}>
                  Approve
                </button>
              )}
              {canSupervise && item.status !== "rejected" && (
                <button type="button" className="csp-reject"
                        disabled={reviewing === item.id}
                        onClick={() => { setRejecting(item.id); setRejectNote(""); }}>
                  Reject
                </button>
              )}
              {canRemove(item) && (
                <button type="button" className="csp-remove"
                        onClick={() => remove(item.id)}>
                  Remove
                </button>
              )}

              {/* Inline rather than window.prompt: a native dialog does
                  not match the app, and its "Don't show this again"
                  checkbox suppresses prompt() for the whole origin --
                  after which rejecting would silently do nothing. */}
              {rejecting === item.id && (
                <form
                  className="csp-reject-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!rejectNote.trim()) return;
                    review(item.id, "reject", rejectNote);
                  }}
                >
                  <label className="stm-label" htmlFor={`csp-why-${item.id}`}>
                    Why are you rejecting this?
                  </label>
                  <div className="csp-reject-row">
                    <input
                      id={`csp-why-${item.id}`}
                      className="stm-input"
                      type="text"
                      autoFocus
                      placeholder="The person who recorded it will see this."
                      value={rejectNote}
                      onChange={(event) => setRejectNote(event.target.value)}
                    />
                    <button type="submit" className="csp-reject"
                            disabled={!rejectNote.trim() || reviewing === item.id}>
                      {reviewing === item.id ? "Rejecting…" : "Confirm reject"}
                    </button>
                    <button type="button" className="csp-cancel"
                            onClick={() => { setRejecting(null); setRejectNote(""); }}>
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form className="csp-form" onSubmit={submit}>
          <div className="stm-field stm-field-reason">
            <label className="stm-label" htmlFor="csp-reason">Reason</label>
            <select id="csp-reason" className="stm-select" required
                    value={form.reason}
                    onChange={(e) => setForm({ ...form, reason: e.target.value })}>
              {reasons.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.category} — {option.reason}
                </option>
              ))}
            </select>
          </div>

          <div className="stm-field">
            <label className="stm-label" htmlFor="csp-from">Stopped at</label>
            <input id="csp-from" className="stm-input" type="datetime-local"
                   required value={form.started_at}
                   onChange={(e) => setForm({ ...form, started_at: e.target.value })} />
          </div>

          <div className="stm-field">
            <label className="stm-label" htmlFor="csp-to">Resumed at</label>
            <input id="csp-to" className="stm-input" type="datetime-local"
                   value={form.ended_at}
                   onChange={(e) => setForm({ ...form, ended_at: e.target.value })} />
            <span className="stm-hint">Clear it if the line is still stopped.</span>
          </div>

          <div className="stm-field stm-field-wide">
            <label className="stm-label" htmlFor="csp-note">
              Note{noteRequired ? " (required)" : ""}
            </label>
            <input id="csp-note" className="stm-input" type="text"
                   placeholder="What happened?" required={noteRequired}
                   value={form.note}
                   onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </div>

          <div className="csp-actions">
            <button type="submit" className="stm-submit" disabled={saving}>
              {saving ? "Saving…" : "Record"}
            </button>
            <button type="button" className="csp-cancel"
                    onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
};

export default ChangeoverSetupPanel;
