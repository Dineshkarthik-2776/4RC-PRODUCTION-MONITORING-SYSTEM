import { useEffect, useRef, useState } from "react";
import { DateRange } from "react-date-range";
import "react-date-range/dist/styles.css"; 
import "react-date-range/dist/theme/default.css"; 
import "../css/DateIP.css";
import { useApiData } from "../context/ApiContext";
import { useNotification } from "../context/NotificationContext";

const DateIP = () => {
  const { 
    updateDateRange, 
    dateRange, 
    loading, 
    error, 
    API_BASE_URL,
    selectedShift,
    selectedRecipe,
    recipeNames,
    updateShiftFilter,
    updateRecipeFilter
  } = useApiData();
  const { showNotification } = useNotification();

  const getLocalDateString = () => {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };
  
  const [range, setRange] = useState([
    {
      startDate: new Date(),
      endDate: new Date(),
      key: "selection"
    }
  ]);
  
  const [showCalendar, setShowCalendar] = useState(false);

  // Kepware logs are a separate concern from the summary exports: they pull
  // raw line data for ONE day, independent of the dashboard date range. The
  // date input used to sit loose in the button row, which read as a third
  // filter and pushed the toolbar onto two lines. It now lives in a popover
  // behind its own button.
  const [showKepwarePicker, setShowKepwarePicker] = useState(false);
  const kepwareRef = useRef(null);
  const calendarRef = useRef(null);

  useEffect(() => {
    if (!showCalendar) return undefined;
    const onPointerDown = (e) => {
      if (calendarRef.current && !calendarRef.current.contains(e.target)) {
        setShowCalendar(false);
      }
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") setShowCalendar(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [showCalendar]);

  useEffect(() => {
    if (!showKepwarePicker) return undefined;
    const onPointerDown = (e) => {
      if (kepwareRef.current && !kepwareRef.current.contains(e.target)) {
        setShowKepwarePicker(false);
      }
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") setShowKepwarePicker(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [showKepwarePicker]);
  const [logDate, setLogDate] = useState(getLocalDateString());

  const handleSubmit = () => {
    // Update context with selected dates
    updateDateRange(range[0].startDate, range[0].endDate);
    setShowCalendar(false);
  };

 // ✅ Updated: Generate & Download Report
const handleGenerateReport = async () => {
  try {
    const startDate = dateRange.from_date;
    const endDate = dateRange.to_date;

    // Log to confirm full API endpoint
    console.log(
      "API URL:",
      `${API_BASE_URL}/api/reports/summary/?start_date=${startDate}&end_date=${endDate}`
    );

    // Fetch report
    const response = await fetch(
      `${API_BASE_URL}/api/reports/summary/?start_date=${startDate}&end_date=${endDate}`,
      {
        method: "GET",
        headers: {
          Authorization: `Token ${localStorage.getItem("authToken")}`,
          Accept: "application/pdf", // Request PDF
        },
      }
    );

    // Handle HTTP errors (non-200)
    if (!response.ok) {
      throw new Error("Failed to fetch report.");
    }

    // Check what content type we actually got back
    const contentType = response.headers.get("Content-Type");

    // 🧩 Case 1: Backend returns JSON with error (e.g. no data found)
    if (contentType && contentType.includes("application/json")) {
      const data = await response.json();
      const errorMessage =
        data.error || "Unknown error occurred while generating report.";
      showNotification(`⚠️ ${errorMessage}`, "warning");
      return; // Stop here (don’t try to download anything)
    }

    // 🧩 Case 2: Backend returns a valid PDF
    if (contentType && contentType.includes("application/pdf")) {
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `4RC_Report_${startDate}_to_${endDate}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      showNotification("✅ Report downloaded successfully!", "success");
      return;
    }

    // 🧩 Case 3: Unknown or invalid file type
    showNotification("❌ Unexpected response format. Please contact support or check backend.", "error");
  } catch (error) {
    console.error("Error downloading report:", error);
    showNotification("❌ Failed to download report. Check console for details.", "error");
  }
};

const handleDownloadExcel = async () => {
  try {
    const startDate = dateRange.from_date;
    const endDate = dateRange.to_date;
    const shift = selectedShift;

    let url = `${API_BASE_URL}/api/changeover-export/?from_date=${startDate}&to_date=${endDate}`;
    if (shift) url += `&shift=${shift}`;

    console.log("Downloading Excel from:", url);

    const response = await fetch(url, {
      method: "GET",
      headers: {
        Authorization: `Token ${localStorage.getItem("authToken")}`,
      },
    });

    if (!response.ok) {
      if (response.status === 404) {
        showNotification("⚠️ No data found for the selected period.", "warning");
      } else {
        throw new Error("Failed to download Excel.");
      }
      return;
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = `Changeover_Report_${startDate}_to_${endDate}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(downloadUrl);

    showNotification("✅ Excel downloaded successfully!", "success");
  } catch (error) {
    console.error("Error downloading Excel:", error);
    showNotification("❌ Failed to download Excel. Check console for details.", "error");
  }
};

const handleDownloadLogs = async () => {
  try {
    if (!logDate) {
      showNotification("Please select a date for Kepware logs.", "warning");
      return;
    }

    const url = `${API_BASE_URL}/api/kepware-logs-export/?date=${logDate}`;
    console.log("Downloading Kepware logs from:", url);

    const response = await fetch(url, {
      method: "GET",
      headers: {
        Authorization: `Token ${localStorage.getItem("authToken")}`,
      },
    });

    if (!response.ok) {
      if (response.status === 404) {
        showNotification("⚠️ No logs found for the selected date.", "warning");
      } else {
        throw new Error("Failed to download Kepware logs.");
      }
      return;
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = `Kepware_Logs_${logDate}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(downloadUrl);

    showNotification("✅ Kepware logs downloaded successfully!", "success");
  } catch (error) {
    console.error("Error downloading Kepware logs:", error);
    showNotification("❌ Failed to download Kepware logs. Check console for details.", "error");
  }
};


  return (
    <>
      <div className="dip-header">
        <h1 className="dip-title">4RC Setup Summary</h1>
      </div>
    
      <div className="dip-container">
        <div className="dip-left-section">
          {/* Shift Filter */}
          <select 
            className="dip-filter-select" 
            value={selectedShift}
            onChange={(e) => updateShiftFilter(e.target.value)}
          >
            <option value="">All Shifts</option>
            <option value="A">Shift A</option>
            <option value="B">Shift B</option>
            <option value="C">Shift C</option>
          </select>

          {/* Recipe Filter */}
          <select 
            className="dip-filter-select" 
            value={selectedRecipe}
            onChange={(e) => updateRecipeFilter(e.target.value)}
          >
            <option value="">All Recipes</option>
            {recipeNames.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>

          {/* Date range. The input and its calendar share a positioned
              wrapper: without one the popup anchored to .dip-left-section
              and opened under the first dropdown instead of under the
              field it belongs to. */}
          <div className="dip-date-field" ref={calendarRef}>
            <input
              type="text"
              readOnly
              value={`${range[0].startDate.toLocaleDateString()} - ${range[0].endDate.toLocaleDateString()}`}
              onClick={() => setShowCalendar(!showCalendar)}
              className="dip-date-input"
              placeholder="Select date range"
              aria-expanded={showCalendar}
              aria-haspopup="dialog"
            />

            {showCalendar && (
              <div className="dip-calendar-popup" role="dialog" aria-label="Select date range">
                <DateRange
                  editableDateInputs={true}
                  onChange={(item) => setRange([item.selection])}
                  moveRangeOnFirstSelection={false}
                  ranges={range}
                  maxDate={new Date()}
                />
              </div>
            )}
          </div>

          {/* GET Button */}
          <button 
            className="dip-submit-btn" 
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? (
              <span className="dip-btn-loader"></span>
            ) : (
              'GET'
            )}
          </button>

          {/* Error Display */}
          {error && (
            <span className="dip-error-text">{error}</span>
          )}
        </div>

        {/* Generate Report Button */}
        <div className="dip-btn-group">
          <button 
            className="dip-report-btn" 
            onClick={handleGenerateReport}
            title="Generate PDF report for the selected date range"
          >
            Report
          </button>
          <button 
            className="dip-excel-btn" 
            onClick={handleDownloadExcel}
            title="Download the summary as an Excel file"
          >
            <svg 
              className="dip-excel-icon" 
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
              style={{width: '16px', height: '16px', marginRight: '6px'}}
            >
              <path 
                strokeLinecap="round" 
                strokeLinejoin="round" 
                strokeWidth={2} 
                d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" 
              />
            </svg>
            Excel
          </button>
          <div
            className={`dip-kepware${showKepwarePicker ? " dip-kepware-open" : ""}`}
            ref={kepwareRef}
          >
            <button
              type="button"
              className="dip-logs-btn"
              onClick={() => setShowKepwarePicker((open) => !open)}
              aria-expanded={showKepwarePicker}
              aria-haspopup="dialog"
              title="Download raw Kepware logs for a single day"
            >
              Kepware Logs
              <svg className="dip-kepware-caret" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M2 4.5L6 8.5L10 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>

            {showKepwarePicker && (
              <div className="dip-kepware-popover" role="dialog" aria-label="Download Kepware logs">
                <label className="dip-field-label" htmlFor="dip-kepware-date">
                  Kepware log date
                </label>
                <p className="dip-kepware-hint">
                  Raw line data for one day, independent of the range above.
                </p>
                <input
                  id="dip-kepware-date"
                  type="date"
                  value={logDate}
                  onChange={(e) => setLogDate(e.target.value)}
                  className="dip-date-input dip-kepware-input"
                  autoFocus
                />
                <div className="dip-kepware-actions">
                  <button
                    type="button"
                    className="dip-kepware-cancel"
                    onClick={() => setShowKepwarePicker(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="dip-logs-btn dip-kepware-confirm"
                    onClick={() => { handleDownloadLogs(); setShowKepwarePicker(false); }}
                  >
                    Download
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
};

export default DateIP;
