import React, { useState } from 'react';
import Summary from './SetupDetails';
import Charts from './Charts';
import '../css/ToggleBtn.css';

const ToggleBtn = () => {
  const [selected, setSelected] = useState("summary"); // default = Setup Details

  return (
    <div className="tb-dashboard">
      {/* Toggle Buttons */}
      <div className="tb-button-group">
        <button
          className={`tb-btn ${selected === "summary" ? "tb-active" : ""}`}
          onClick={() => setSelected("summary")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span>Setup Details</span>
        </button>

        <button
          className={`tb-btn ${selected === "charts" ? "tb-active" : ""}`}
          onClick={() => setSelected("charts")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          <span>Charts</span>
        </button>

        <button
          className={`tb-btn ${selected === "both" ? "tb-active" : ""}`}
          onClick={() => setSelected("both")}
        >
          <svg className="tb-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
          </svg>
          <span>Setup Summary</span>
        </button>
      </div>

      {/* Render Components Based on Selection */}
      <div className="tb-content">
        {selected === "summary" && <Summary viewType="details" />}
        {selected === "charts" && <Charts />}
        {selected === "both" && (
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
