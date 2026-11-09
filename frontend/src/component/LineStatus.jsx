import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { useNotification } from "../context/NotificationContext";
import "../css/shift.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

/**
 * Now, as naive local wall-clock time.
 *
 * NOT toISOString(), which converts to UTC: this project runs with
 * USE_TZ = False and stores bare local wall-clock times, so sending
 * UTC made a stoppage reported at 18:02 IST arrive as 12:32 and read
 * as "already down for 330 minutes" -- exactly the +05:30 offset.
 */
const localNow = () => {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  );
};

const clock = (value) => {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value).slice(11, 16)
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

/**
 * Report that the line has stopped, while it is stopped.
 *
 * This sits in the header rather than on a changeover, because at the
 * moment the line goes down there is no changeover to put it on -- one
 * is only written once the pipeline has resolved a completed setup
 * window, by which time the operator who saw it may have been signed
 * out at a shift boundary.
 *
 * Two states, one button. Running: "Line stopped" opens a reason. Down:
 * a live counter and "Line running again".
 */
const LineStatus = () => {
  const { showNotification } = useNotification();

  const currentUser = JSON.parse(localStorage.getItem("user") || "{}");
  const canRecord = ["operator", "shift_incharge", "manager"]
    .includes(currentUser.role);

  const [stoppage, setStoppage] = useState(null);
  // A stoppage the shift-end task closed, which nobody has said was
  // resolved. The incoming shift is asked before anything else.
  const [handover, setHandover] = useState(null);
  const [reasons, setReasons] = useState([]);
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const authHeaders = useCallback(() => ({
    headers: { Authorization: `Token ${localStorage.getItem("authToken")}` },
  }), []);

  const readError = (err, fallback) => {
    const data = err.response?.data;
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (data.non_field_errors?.length) return data.non_field_errors[0];
    for (const key of ["started_at", "ended_at", "note", "reason", "detail"]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === "string") return value;
    }
    return fallback;
  };

  const refresh = useCallback(async () => {
    if (!canRecord || !localStorage.getItem("authToken")) return;
    try {
      const response = await axios.get(
        `${API_BASE_URL}/api/line-stoppages/current/`, authHeaders());
      setStoppage(response.data.stoppage);
      setHandover(response.data.handover);
    } catch {
      // A 401 is handled globally by the axios interceptor.
    }
  }, [authHeaders, canRecord]);

  useEffect(() => {
    refresh();
    // Another terminal may have reported it, so this is shared state,
    // not just this tab's.
    const poll = setInterval(refresh, 60 * 1000);
    return () => clearInterval(poll);
  }, [refresh]);

  useEffect(() => {
    if (!canRecord) return undefined;
    let cancelled = false;
    axios
      .get(`${API_BASE_URL}/api/overshoot-options/`, authHeaders())
      .then((response) => {
        if (cancelled) return;
        setReasons(response.data);
        setChosen((current) => current || String(response.data[0]?.id || ""));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [authHeaders, canRecord]);

  // Counts up from when the line went down, so the number on screen is
  // how long it has actually been stopped -- not how long this tab has
  // been open.
  useEffect(() => {
    if (!stoppage) {
      setElapsed(0);
      return undefined;
    }
    const since = new Date(stoppage.started_at).getTime();
    const tick = () => setElapsed(Math.max(Math.round((Date.now() - since) / 60000), 0));
    tick();
    const timer = setInterval(tick, 15 * 1000);
    return () => clearInterval(timer);
  }, [stoppage]);

  const noteRequired = (() => {
    const row = reasons.find((r) => String(r.id) === String(chosen));
    return row
      ? `${row.category} ${row.reason}`.toLowerCase().includes("other")
      : false;
  })();

  const answerHandover = async (answer) => {
    setBusy(true);
    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/line-stoppages/${handover.id}/handover/`,
        { answer }, authHeaders());
      setHandover(null);
      setStoppage(response.data.stoppage || null);
      showNotification(
        answer === "continued"
          ? "Carried over — the line is still recorded as stopped."
          : "Noted — the line had already resumed.",
        "success");
    } catch (err) {
      showNotification(readError(err, "Could not record that."), "error");
    } finally {
      setBusy(false);
    }
  };

  const reportStopped = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      // The time is taken here, not typed: the point of reporting it
      // live is that nobody has to remember when it happened.
      const response = await axios.post(`${API_BASE_URL}/api/line-stoppages/`, {
        reason: chosen,
        note,
        started_at: localNow(),
        ended_at: null,
      }, authHeaders());
      setStoppage(response.data);
      setPicking(false);
      setNote("");
      showNotification("Line reported as stopped.", "success");
    } catch (err) {
      showNotification(readError(err, "Could not record that."), "error");
    } finally {
      setBusy(false);
    }
  };

  const reportRunning = async () => {
    setBusy(true);
    try {
      await axios.patch(`${API_BASE_URL}/api/line-stoppages/${stoppage.id}/`,
        { ended_at: localNow() }, authHeaders());
      setStoppage(null);
      showNotification("Line reported as running again.", "success");
    } catch (err) {
      showNotification(readError(err, "Could not record that."), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!canRecord) return null;

  // Asked before anything else: until it is answered, nobody knows
  // whether the line is running.
  if (handover) {
    return (
      <div className="ls-chip ls-chip-ask" role="status">
        <span className="ls-text">
          Line was left stopped at {clock(handover.ended_at)}
          <span className="ls-sub">
            {handover.reason_label} · is it still down?
          </span>
        </span>
        <button type="button" className="ls-still-down"
                onClick={() => answerHandover("continued")} disabled={busy}>
          Still down
        </button>
        <button type="button" className="ls-resume"
                onClick={() => answerHandover("ended")} disabled={busy}>
          It resumed
        </button>
      </div>
    );
  }

  if (stoppage) {
    return (
      <div className="ls-chip ls-chip-down" role="status">
        <span className="ls-dot" />
        <span className="ls-text">
          Line stopped {elapsed} min
          <span className="ls-sub">
            {stoppage.reason_label} · since {clock(stoppage.started_at)}
          </span>
        </span>
        <button type="button" className="ls-resume"
                onClick={reportRunning} disabled={busy}>
          {busy ? "Saving…" : "Line running again"}
        </button>
      </div>
    );
  }

  if (!picking) {
    return (
      <button type="button" className="ls-report"
              onClick={() => setPicking(true)}>
        Line stopped
      </button>
    );
  }

  return (
    <form className="ls-form" onSubmit={reportStopped}>
      <select className="ls-select" value={chosen} required
              onChange={(event) => setChosen(event.target.value)}>
        {reasons.map((option) => (
          <option key={option.id} value={option.id}>
            {option.category} — {option.reason}
          </option>
        ))}
      </select>
      {noteRequired && (
        <input className="ls-note" type="text" required value={note}
               placeholder="What happened?"
               onChange={(event) => setNote(event.target.value)} />
      )}
      <button type="submit" className="ls-confirm" disabled={busy}>
        {busy ? "Saving…" : "Report now"}
      </button>
      <button type="button" className="ls-cancel"
              onClick={() => setPicking(false)}>
        Cancel
      </button>
    </form>
  );
};

export default LineStatus;
