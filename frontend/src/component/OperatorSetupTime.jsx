import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { useApiData } from "../context/ApiContext";
import "../css/setuptime.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

const GROUPINGS = [
  { value: "operator", label: "By operator" },
  { value: "shift", label: "By shift" },
  { value: "date", label: "By date" },
];

const minutes = (value) =>
  value === null || value === undefined ? "--" : `${Number(value).toFixed(1)}`;

/**
 * Minutes over or under the standard.
 *
 * Signed and coloured, because the sign is the whole message: +25.0 and
 * -25.0 are opposite verdicts on the same operator and must not be read
 * at a glance as the same number. Null means no standard was configured
 * for any of their changeovers -- "no target" is not "on target".
 */
const variance = (value) => {
  if (value === null || value === undefined) {
    return <span className="stm-muted">--</span>;
  }
  const over = value > 0;
  return (
    <span className={over ? "stm-over" : "stm-under"}>
      {over ? "+" : ""}{Number(value).toFixed(1)}
    </span>
  );
};

/**
 * Operator-wise setup time, totalled over the selected range.
 *
 * Three figures rather than one: time on the line, the interruptions taken off
 * it, and the setup time that remains. "Two hours, forty minutes of it a
 * breakdown" is a different story from two hours, and an operator judged
 * on the first number deserves to see the second.
 *
 * Per-changeover work -- seeing how one setup was split, recording an
 * interruption, assigning unattributed time -- lives in that
 * changeover's own detail panel, where the changeover is already known.
 */
const OperatorSetupTime = () => {
  const { dateRange, selectedShift, selectedRecipe } = useApiData();

  const [groupBy, setGroupBy] = useState("operator");
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const authHeaders = useCallback(() => ({
    headers: { Authorization: `Token ${localStorage.getItem("authToken")}` },
  }), []);

  const readError = (err, fallback) => {
    const data = err.response?.data;
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (data.non_field_errors?.length) return data.non_field_errors[0];
    for (const key of ["note", "ended_at", "started_at", "changeover",
                       "category", "error", "detail"]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === "string") return value;
    }
    return fallback;
  };

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        group_by: groupBy,
        start_date: dateRange.from_date,
        end_date: dateRange.to_date,
      });
      if (selectedShift) params.set("shift", selectedShift);
      // Recipe goes to the server here, unlike the other tabs which
      // filter it in the browser -- these are totals, and a total
      // cannot be filtered after it has been summed.
      if (selectedRecipe) params.set("recipe", selectedRecipe);

      const response = await axios.get(
        `${API_BASE_URL}/api/setup-time/summary/?${params}`,
        authHeaders()
      );
      setSummary(response.data);
      setError("");
    } catch (err) {
      setError(readError(err, "Could not load setup time."));
    } finally {
      setLoading(false);
    }
  }, [groupBy, dateRange.from_date, dateRange.to_date, selectedShift,
      selectedRecipe, authHeaders]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  const totals = summary?.totals;
  const unattributed = summary?.unattributed_minutes || 0;

  return (
    <div className="stm-wrapper">
      <div className="stm-toolbar">
        <div className="stm-toolbar-left">
          <h2 className="stm-title">Operator Setup Time</h2>
          <p className="stm-subtitle">
            {dateRange.from_date} to {dateRange.to_date}
            {selectedShift ? ` · ${selectedShift} shift` : " · all shifts"}
            {selectedRecipe ? ` · ${selectedRecipe}` : ""}
          </p>
        </div>

        <div className="stm-group-field">
          <label className="stm-label" htmlFor="stm-group">Group</label>
          <select
            id="stm-group"
            className="stm-select"
            value={groupBy}
            onChange={(event) => setGroupBy(event.target.value)}
          >
            {GROUPINGS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <div className="stm-banner stm-banner-error">{error}</div>}

      <div className="stm-kpis">
        <div className="stm-kpi">
          <span className="stm-kpi-label">Setup time</span>
          <span className="stm-kpi-value">{minutes(totals?.allocated_minutes)}<em>min</em></span>
        </div>
        <div className="stm-kpi">
          <span className="stm-kpi-label">Interruptions</span>
          <span className="stm-kpi-value">{minutes(totals?.interrupted_minutes)}<em>min</em></span>
        </div>
        <div className="stm-kpi">
          <span className="stm-kpi-label">Changeovers</span>
          <span className="stm-kpi-value">{totals?.setups ?? 0}</span>
        </div>
        <div className="stm-kpi">
          <span className="stm-kpi-label">Avg act vs standard</span>
          <span className="stm-kpi-value">
            {totals?.average_standard_minutes === null ||
             totals?.average_standard_minutes === undefined
              ? <span className="stm-muted">not set</span>
              : variance(totals.variance_minutes)}
            <em>min</em>
          </span>
        </div>
        <div className={`stm-kpi ${unattributed > 0 ? "stm-kpi-warn" : ""}`}>
          <span className="stm-kpi-label">Unattributed</span>
          <span className="stm-kpi-value">{minutes(unattributed)}<em>min</em></span>
        </div>
      </div>

      <p className="stm-legend">
        <strong>Avg actual</strong> is setup time per changeover, with any
        approved interruptions already taken off — a standard is a target for setup
        work, and a breakdown is not setup work.
      </p>

      {unattributed > 0 && (
        <p className="stm-note">
          Unattributed minutes are setup time with nobody signed in at the
          terminal. The work still happened -- it just cannot be credited to
          anyone.
        </p>
      )}

      {loading ? (
        <div className="stm-empty">Loading…</div>
      ) : !summary?.rows?.length ? (
        <div className="stm-empty">
          No setup time recorded for this range.
        </div>
      ) : (
        <table className="stm-table">
          <thead>
            <tr>
              <th>{groupBy === "operator" ? "Operator" : "Group"}</th>
              {groupBy === "operator" && <th>Employee ID</th>}
              <th className="stm-num">Changeovers</th>
              <th className="stm-num">On the line</th>
              <th className="stm-num">Interruptions</th>
              <th className="stm-num">Setup Time</th>
              <th className="stm-num">Avg actual</th>
              <th className="stm-num">Avg standard</th>
              <th className="stm-num">vs standard</th>
            </tr>
          </thead>
          <tbody>
            {summary.rows.map((row) => (
                <tr key={`${row.label}-${row.employee_id || ""}`}>
                  <td className="stm-strong">{row.label}</td>
                  {groupBy === "operator" && (
                    <td>{row.employee_id || <span className="stm-muted">—</span>}</td>
                  )}
                  <td className="stm-num">{row.setups}</td>
                  <td className="stm-num">{minutes(row.gross_minutes)}</td>
                  <td className="stm-num">{minutes(row.interrupted_minutes)}</td>
                  <td className="stm-num stm-strong">{minutes(row.allocated_minutes)}</td>
                  <td className="stm-num stm-strong">{minutes(row.average_minutes)}</td>
                  <td className="stm-num">
                    {row.average_standard_minutes === null ? (
                      <span className="stm-muted" title="No standard time configured">
                        not set
                      </span>
                    ) : (
                      minutes(row.average_standard_minutes)
                    )}
                  </td>
                  <td className="stm-num">{variance(row.variance_minutes)}</td>
                </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="stm-footnote">
        To see how one changeover was split, or to record an interruption
        on it, open that changeover from Setup Details — the split is at
        the bottom of its detail panel.
      </p>

    </div>
  );
};

export default OperatorSetupTime;
