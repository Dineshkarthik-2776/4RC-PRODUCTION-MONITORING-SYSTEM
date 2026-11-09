import React, { useState } from 'react';
import Summary from './SetupDetails';
import Charts from './Charts';
import OperatorSetupTime from './OperatorSetupTime';
import '../css/ToggleBtn.css';

const ToggleBtn = () => {
  const [selected, setSelected] = useState("summary"); // default = Setup Details

  // Operator-wise setup time compares one operator's figures against
  // another's. That is a supervisor's view, not something to put in
  // front of the people being compared, so operators do not get the
  // tab. The API is the real boundary; this only stops it being shown.
  const currentUser = JSON.parse(localStorage.getItem("user") || "{}");
  const canSeeOperatorStats =
    ["manager", "shift_incharge"].includes(currentUser.role);

  // Defends against the tab being selected and then the role losing
  // access -- a stale "operators" selection must not keep rendering.
  const view = selected === "operators" && !canSeeOperatorStats
    ? "summary"
    : selected;

  return (
    <div className="tb-dashboard">
      {/* Toggle Buttons */}
      <div className="tb-button-group">
        <button
          className={`tb-btn ${view === "summary" ? "tb-active" : ""}`}
          onClick={() => setSelected("summary")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span>Setup Details</span>
        </button>

        <button
          className={`tb-btn ${view === "charts" ? "tb-active" : ""}`}
          onClick={() => setSelected("charts")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          <span>Charts</span>
        </button>

        <button
          className={`tb-btn ${view === "both" ? "tb-active" : ""}`}
          onClick={() => setSelected("both")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
          </svg>
          <span>Setup Summary</span>
        </button>

        {canSeeOperatorStats && (
        <button
          className={`tb-btn ${view === "operators" ? "tb-active" : ""}`}
          onClick={() => setSelected("operators")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
          <span>Operator Setup Time</span>
        </button>
        )}
      </div>

      {/* Render Components Based on Selection */}
      <div className="tb-content">
        {view === "summary" && <Summary viewType="details" />}
        {view === "charts" && <Charts />}
        {view === "operators" && canSeeOperatorStats && <OperatorSetupTime />}
        {view === "both" && (
          <>
            <Summary viewType="summary" />
            <Charts />
          </>
        )}
      </div>
    </div>
  );
};

export default ToggleBtn;
