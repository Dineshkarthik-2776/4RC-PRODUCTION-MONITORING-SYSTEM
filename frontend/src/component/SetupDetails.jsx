import React, { useState } from "react";
import { useApiData } from "../context/ApiContext";
import { useNotification } from "../context/NotificationContext";
import axios from "axios";
import "../css/SetupDetails.css";

const SetupDetails = ({ viewType = "details" }) => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const { changeoverData, loading, error, refreshData, selectedRecipe, overshootOptions } = useApiData();
  const { showNotification } = useNotification();
  const [openRow, setOpenRow] = useState(null);
  const [openDetail, setOpenDetail] = useState(null);
  const [reasons, setReasons] = useState({});
  const [categories, setCategories] = useState({});
  const [saving, setSaving] = useState({});

  const toggleRow = (id) => {
    setOpenRow(openRow === id ? null : id);
  };

  const handleReasonChange = (detailId, value) => {
    setReasons((prev) => ({
      ...prev,
      [detailId]: value,
    }));
  };

  const handleCategoryChange = (detailId, value) => {
    setCategories((prev) => ({
      ...prev,
      [detailId]: value,
    }));
  };

  const handleReasonSubmit = async (detailId, currentOvershootId) => {
    const key = detailId;
    
    // The current state might be holding the reason ID or the reason object
    const selectedOvershootId = reasons[detailId] || currentOvershootId;
    const selectedCategory = categories[detailId];

    // Validation
    if (!selectedOvershootId) {
      showNotification('Please select both a category and a reason.', 'error');
      return;
    }

    setSaving((prev) => ({ ...prev, [key]: true }));

    try {
      const token = localStorage.getItem('authToken');
      
      if (!token) {
        showNotification('Authentication token not found. Please login again.', 'error');
        setSaving((prev) => ({ ...prev, [key]: false }));
        return;
      }

      const payload = {
        overshoot: selectedOvershootId
      };

      console.log('Sending PATCH request:', {
        url: `${API_BASE_URL}/api/changeover/update/${detailId}/`,
        payload,
        headers: {
          'Authorization': `Token ${token}`,
          'Content-Type': 'application/json'
        }
      });

      const response = await axios.patch(
        `${API_BASE_URL}/api/changeover/update/${detailId}/`,
        payload,
        {
          headers: {
            'Authorization': `Token ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );

      console.log('Update successful:', response.data);
      
      // Check if it was a correction request (status 202) or direct update (status 200)
      if (response.status === 202) {
        showNotification(`✓ Update request sent to manager for approval (Recipe ID: ${detailId})`, 'success');
      } else {
        showNotification(`✓ Reason saved successfully for Recipe ID: ${detailId}`, 'success');
      }
      
      // Refresh data to get updated values
      refreshData();
      
      // Clear local state after successful save
      setReasons((prev) => {
        const newReasons = { ...prev };
        delete newReasons[detailId];
        return newReasons;
      });
      setCategories((prev) => {
        const newCategories = { ...prev };
        delete newCategories[detailId];
        return newCategories;
      });

    } catch (err) {
      console.error('Error updating reason:', err);
      console.error('Error response:', err.response);

      console.error('Failed to save reason:', err);
      const msg = err.response?.data?.error || err.response?.data?.message || err.message || 'Failed to save reason. Please try again.';
      showNotification(msg, 'error');
    } finally {
      setSaving((prev) => ({ ...prev, [key]: false }));
    }
  };

  // Show loading state
  if (loading && !changeoverData) {
    return (
      <div className="sd-loading-container">
        <div className="sd-spinner"></div>
        <p className="sd-loading-text">Loading setup details...</p>
      </div>
    );
  }

  // Show error state
  if (error) {
    return (
      <div className="sd-error-container">
        <svg className="sd-error-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="sd-error-text">{error}</p>
      </div>
    );
  }

  // Get table data from API and filter by recipe
  let tableData = changeoverData?.table_data || [];
  
  // Filter by selected recipe and recalculate counts
  if (selectedRecipe) {
    tableData = tableData.map(row => {
      const filteredDetails = row.details?.filter(detail => 
        detail.current_recipe && detail.current_recipe.includes(selectedRecipe)
      ) || [];
      
      return {
        ...row,
        details: filteredDetails,
        count: filteredDetails.length // Update count based on filtered details
      };
    }).filter(row => row.details.length > 0);
  }

  // Show empty state
  if (tableData.length === 0) {
    return (
      <div className="sd-empty-container">
        <svg className="sd-empty-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <p className="sd-empty-text">No setup data available for the selected date range.</p>
      </div>
    );
  }

  const parseLocalDateTime = (dateValue) => {
    if (!dateValue) return null;
    const normalized = dateValue.replace("T", " ").replace("Z", "").trim();
    const parts = normalized.split(" ");
    if (parts.length < 2) return null;
    const [datePart, timePart] = parts;
    const [year, month, day] = datePart.split("-").map(Number);
    const [hour, minute, second] = timePart.split(":").map(Number);
    if ([year, month, day, hour, minute, second].some((val) => Number.isNaN(val))) {
      return null;
    }
    return new Date(year, month - 1, day, hour, minute, second).getTime();
  };

  // Find the currently open row data
  const openRowData = tableData.find(row => row.id === openRow);
  const flattenedTableData = tableData
    .flatMap((row) =>
      (row.details || []).map((detail) => ({
        ...detail,
        type: row.type,
      }))
    )
    .sort((a, b) => {
      const aTime = parseLocalDateTime(a.start_time) || 0;
      const bTime = parseLocalDateTime(b.start_time) || 0;
      return bTime - aTime;
    });

  const sortedOpenRowDetails = (openRowData?.details || []).slice().sort((a, b) => {
    const aTime = parseLocalDateTime(a.start_time) || 0;
    const bTime = parseLocalDateTime(b.start_time) || 0;
    return bTime - aTime;
  });

  const formatDateTime = (dateValue) => {
    if (!dateValue) return "N/A";
    return dateValue.replace("T", " ").replace("Z", "").substring(0, 19);
  };

  const formatMetric = (value) => {
    if (value === null || value === undefined || value === "NA" || value === "N/A" || value === "") {
      return "N/A";
    }

    const numericValue = Number(value);
    if (Number.isNaN(numericValue)) {
      return "N/A";
    }

    const isNegative = numericValue < 0;
    const absoluteValue = Math.abs(numericValue);
    let minutes = Math.floor(absoluteValue);
    let seconds = Math.round((absoluteValue - minutes) * 60);

    // Handle round-up edge case like 2.999 -> 3:00
    if (seconds === 60) {
      minutes += 1;
      seconds = 0;
    }

    const sign = isNegative ? "-" : "";
    return `${sign}${minutes}:${String(seconds).padStart(2, "0")}`;
  };

  const renderNA = () => <span className="na-text">N/A</span>;

  const renderValueOrNA = (value) => {
    if (value === null || value === undefined || value === "" || value === "NA" || value === "N/A") {
      return renderNA();
    }
    return value;
  };

  const renderFormattedMetric = (value) => {
    const formattedValue = formatMetric(value);
    return formattedValue === "N/A" ? renderNA() : formattedValue;
  };

  const renderFormattedDateTime = (value) => {
    const formattedValue = formatDateTime(value);
    return formattedValue === "N/A" ? renderNA() : formattedValue;
  };

  const formatDateRange = (startValue, endValue) => {
    const startText = formatDateTime(startValue);
    const endText = formatDateTime(endValue);
    if (startText === "N/A" || endText === "N/A") {
      return "N/A";
    }
    return `${startText} - ${endText}`;
  };

  const renderDateRange = (startValue, endValue) => {
    const rangeText = formatDateRange(startValue, endValue);
    return rangeText === "N/A" ? renderNA() : rangeText;
  };

  const selectedDetailData =
    openDetail !== null
      ? flattenedTableData.find((detail) => detail.id === openDetail)
      : null;

  return (
    <>
      <div className="sd-container">
        
        <div className="sd-table-wrapper">
          {viewType === "summary" ? (
            <table className="sd-table">
              <thead className="sd-thead">
                <tr>
                  <th className="sd-th">Type of Style</th>
                  <th className="sd-th">Std Time (min)</th>
                  <th className="sd-th">Act Time (min)</th>
                  <th className="sd-th">Static S/U (min)</th>
                  <th className="sd-th">Ramp Up (min)</th>
                  <th className="sd-th">Setup Count</th>
                  <th className="sd-th">Over Shoot (min)</th>
                  <th className="sd-th">Action</th>
                </tr>
              </thead>
              <tbody>
                {tableData.map((row, index) => (
                  <tr key={row.id} className={index % 2 === 0 ? "sd-tr sd-even" : "sd-tr sd-odd"}>
                    <td className="sd-td sd-td-bold">{row.type}</td>
                    <td className="sd-td">{renderFormattedMetric(row.Std)}</td>
                    <td className="sd-td">{renderFormattedMetric(row.act)}</td>
                    <td className="sd-td">{renderFormattedMetric(row.static)}</td>
                    <td className="sd-td">{renderFormattedMetric(row.ramp)}</td>
                    <td className="sd-td sd-count">{row.count || 0}</td>
                    <td className={`sd-td ${(row.shoot || 0) > 0 ? 'sd-overshoot' : 'sd-normal'}`}>
                      {renderFormattedMetric(row.shoot)}
                    </td>
                    <td className="sd-td">
                      <button
                        className="sd-btn-action"
                        onClick={() => toggleRow(row.id)}
                      >
                        {openRow === row.id ? "Hide Details" : "Show Details"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="sd-table">
              <thead className="sd-thead">
                <tr>
                  <th className="sd-th sd-th-left">Previous Recipe</th>
                  <th className="sd-th sd-th-left">Current Recipe</th>
                  <th className="sd-th sd-th-left">Type of Style</th>
                  <th className="sd-th sd-th-left">Production Date</th>
                  <th className="sd-th">Shift</th>
                  <th className="sd-th">Std Time (min)</th>
                  <th className="sd-th">Act Time (min)</th>
                  <th className="sd-th">Static S/U (min)</th>
                  <th className="sd-th">Ramp Up (min)</th>
                  <th className="sd-th">Over Shoot (min)</th>
                  <th className="sd-th sd-th-left">Start Time</th>
                </tr>
              </thead>
              <tbody>
                {flattenedTableData.map((detail, index) => (
                  <tr
                    key={detail.id}
                    className={`${index % 2 === 0 ? "sd-tr sd-even" : "sd-tr sd-odd"} sd-row-clickable`}
                    onClick={() => setOpenDetail(detail.id)}
                  >
                    <td className="sd-td sd-td-left">{renderValueOrNA(detail.from_recipe)}</td>
                    <td className="sd-td sd-td-bold sd-td-left">{renderValueOrNA(detail.current_recipe)}</td>
                    <td className="sd-td sd-td-left">{renderValueOrNA(detail.type)}</td>
                    <td className="sd-td sd-td-left">{renderValueOrNA(detail.production_date)}</td>
                    <td className="sd-td">{renderValueOrNA(detail.shift)}</td>
                    <td className="sd-td">{renderFormattedMetric(detail.Std)}</td>
                    <td className="sd-td">{renderFormattedMetric(detail.act)}</td>
                    <td className="sd-td">{renderFormattedMetric(detail.static)}</td>
                    <td className="sd-td">{renderFormattedMetric(detail.ramp)}</td>
                    <td className={`sd-td ${(Number(detail.shoot) || 0) > 0 ? 'sd-overshoot' : 'sd-normal'}`}>
                      {renderFormattedMetric(detail.shoot)}
                    </td>
                    <td className="sd-td sd-td-left">{renderFormattedDateTime(detail.start_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Modal Popup - OUTSIDE the table */}
      {viewType === "summary" && openRow && openRowData && (
        <>
          {/* Background Overlay */}
          <div 
            className="sd-overlay"
            onClick={() => setOpenRow(null)}
          ></div>

          {/* Modal Content */}
          <div className="sd-modal">
            <div className="sd-modal-header">
              <h3 className="sd-modal-title">
                Details for {openRowData.type}
              </h3>
              <button
                className="sd-modal-close"
                onClick={() => setOpenRow(null)}
              >
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="sd-modal-body">
              <table className="sd-detail-table">
                <thead className="sd-detail-thead">
                  <tr>
                    <th className="sd-detail-th">Previous Recipe</th>
                    <th className="sd-detail-th">Current Recipe</th>
                    <th className="sd-detail-th">Production Date</th>
                    <th className="sd-detail-th">Shift</th>
                    <th className="sd-detail-th">Std Time</th>
                    <th className="sd-detail-th">Act Time</th>
                    <th className="sd-detail-th">Static S/U</th>
                    <th className="sd-detail-th">Ramp Up</th>
                    <th className="sd-detail-th">Over Shoot</th>
                    <th className="sd-detail-th">Start Time</th>
                    <th className="sd-detail-th">Category</th>
                    <th className="sd-detail-th">Reason</th>
                    <th className="sd-detail-th">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedOpenRowDetails.map((detail, idx) => {
                    const hasReason = detail.overshoot_reason && detail.overshoot_reason.trim() !== '';
                    const displayReason = reasons[detail.id] !== undefined 
                      ? reasons[detail.id] 
                      : (detail.overshoot_reason || "");
                    const displayCategory = categories[detail.id] !== undefined
                      ? categories[detail.id]
                      : (detail.overshoot_category || "None");

                    return (
                      <tr key={detail.id} className={idx % 2 === 0 ? "sd-detail-tr sd-detail-even" : "sd-detail-tr sd-detail-odd"}>
                        <td className="sd-detail-td">{renderValueOrNA(detail.from_recipe)}</td>
                        <td className="sd-detail-td sd-material">{renderValueOrNA(detail.current_recipe)}</td>
                        <td className="sd-detail-td">{renderValueOrNA(detail.production_date)}</td>
                        <td className="sd-detail-td">{renderValueOrNA(detail.shift)}</td>
                        <td className="sd-detail-td">{renderFormattedMetric(detail.Std)}</td>
                        <td className="sd-detail-td">{renderFormattedMetric(detail.act)}</td>
                        <td className="sd-detail-td">{renderFormattedMetric(detail.static)}</td>
                        <td className="sd-detail-td">{renderFormattedMetric(detail.ramp)}</td>
                        <td className={`sd-detail-td ${(Number(detail.shoot) || 0) > 0 ? 'sd-detail-overshoot' : 'sd-detail-normal'}`}>
                          {renderFormattedMetric(detail.shoot)}
                        </td>
                        <td className="sd-detail-td sd-timestamp">
                          {renderFormattedDateTime(detail.start_time)}
                        </td>
                        <td className="sd-detail-td">
                          <select
                            className={`sd-select ${hasReason ? 'sd-select-filled' : ''}`}
                            value={displayCategory}
                            onChange={(e) => {
                              handleCategoryChange(detail.id, e.target.value);
                              handleReasonChange(detail.id, ""); // Reset reason when category changes
                            }}
                          >
                            <option value="">Select Category</option>
                            {[...new Set(overshootOptions.map(opt => opt.category))].map(cat => (
                              <option key={cat} value={cat}>{cat}</option>
                            ))}
                          </select>
                        </td>
                        <td className="sd-detail-td">
                          <select
                            className={`sd-select ${hasReason ? 'sd-select-filled' : ''}`}
                            value={reasons[detail.id] !== undefined ? reasons[detail.id] : (detail.overshoot || "")}
                            onChange={(e) => handleReasonChange(detail.id, e.target.value)}
                            disabled={!displayCategory}
                          >
                            <option value="">Select Reason</option>
                            {overshootOptions
                              .filter(opt => opt.category === displayCategory)
                              .map(opt => (
                                <option key={opt.id} value={opt.id}>{opt.reason}</option>
                              ))
                            }
                          </select>
                        </td>
                        <td className="sd-detail-td">
                          <button
                            onClick={() => handleReasonSubmit(
                              detail.id, 
                              detail.overshoot
                            )}
                            className="sd-btn-save"
                            disabled={saving[detail.id]}
                          >
                            {saving[detail.id] ? (
                              <span className="sd-btn-loader"></span>
                            ) : (
                              hasReason ? 'Update Result' : 'Save'
                            )}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="sd-modal-footer">
              <button
                className="sd-btn-close"
                onClick={() => setOpenRow(null)}
              >
                Close
              </button>
            </div>
          </div>
        </>
      )}

      {viewType !== "summary" && openDetail && selectedDetailData && (
        <>
          <div
            className="sd-overlay"
            onClick={() => setOpenDetail(null)}
          ></div>

          <div className="sd-modal sd-modal-single">
            <div className="sd-modal-header">
              <h3 className="sd-modal-title">
                Setup Reason - {renderValueOrNA(selectedDetailData.current_recipe)}
              </h3>
              <button
                className="sd-modal-close"
                onClick={() => setOpenDetail(null)}
              >
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="sd-modal-body">
              {(() => {
                const detail = selectedDetailData;
                const hasReason = detail.overshoot_reason && detail.overshoot_reason.trim() !== "";
                const displayReason = reasons[detail.id] !== undefined
                  ? reasons[detail.id]
                  : (detail.overshoot_reason || "");
                const displayCategory = categories[detail.id] !== undefined
                  ? categories[detail.id]
                  : (detail.overshoot_category || "None");

                return (
                  <div className="sd-single-layout">
                    <div className="sd-single-grid">
                      <div className="sd-single-item"><span className="sd-single-label">Previous Recipe</span><span className="sd-single-value">{renderValueOrNA(detail.from_recipe)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Current Recipe</span><span className="sd-single-value">{renderValueOrNA(detail.current_recipe)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Type of Style</span><span className="sd-single-value">{renderValueOrNA(detail.type)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Production Date</span><span className="sd-single-value">{renderValueOrNA(detail.production_date)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Shift</span><span className="sd-single-value">{renderValueOrNA(detail.shift)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Std Time</span><span className="sd-single-value">{renderFormattedMetric(detail.Std)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Act Time</span><span className="sd-single-value">{renderFormattedMetric(detail.act)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Static S/U</span><span className="sd-single-value">{renderFormattedMetric(detail.static)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Static S/U Window</span><span className="sd-single-value">{renderDateRange(detail.setup_start_time, detail.ramp_up_time)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Ramp Up</span><span className="sd-single-value">{renderFormattedMetric(detail.ramp)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Ramp Up Window</span><span className="sd-single-value">{renderDateRange(detail.ramp_up_time, detail.setup_complete_time)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Over Shoot</span><span className={`sd-single-value ${(Number(detail.shoot) || 0) > 0 ? "sd-detail-overshoot" : "sd-detail-normal"}`}>{renderFormattedMetric(detail.shoot)}</span></div>
                      <div className="sd-single-item"><span className="sd-single-label">Start Time</span><span className="sd-single-value">{renderFormattedDateTime(detail.start_time)}</span></div>
                      <div className="sd-single-item sd-single-item-full"><span className="sd-single-label">Remarks</span><span className="sd-single-value sd-single-wrap">{renderValueOrNA(detail.remarks)}</span></div>
                    </div>

                    <div className="sd-single-form">
                      <div className="sd-single-inline-row">
                        <div className="sd-single-inline-item sd-inline-category">
                        <label className="sd-single-label">Category</label>
                        <select
                          className={`sd-select sd-single-select ${hasReason ? "sd-select-filled" : ""}`}
                          value={displayCategory}
                          onChange={(e) => {
                            handleCategoryChange(detail.id, e.target.value);
                            handleReasonChange(detail.id, ""); // Reset reason
                          }}
                        >
                          <option value="">Select Category</option>
                          {[...new Set(overshootOptions.map(opt => opt.category))].map(cat => (
                            <option key={cat} value={cat}>{cat}</option>
                          ))}
                        </select>
                        </div>

                        <div className="sd-single-inline-item sd-inline-reason">
                        <label className="sd-single-label">Reason</label>
                        <select
                          className={`sd-select sd-single-select ${hasReason ? "sd-select-filled" : ""}`}
                          value={reasons[detail.id] !== undefined ? reasons[detail.id] : (detail.overshoot || "")}
                          onChange={(e) => handleReasonChange(detail.id, e.target.value)}
                          disabled={!displayCategory}
                        >
                          <option value="">Select Reason</option>
                          {overshootOptions
                            .filter(opt => opt.category === displayCategory)
                            .map(opt => (
                              <option key={opt.id} value={opt.id}>{opt.reason}</option>
                            ))
                          }
                        </select>
                        </div>

                        <div className="sd-single-inline-item sd-inline-action">
                        <button
                          type="button"
                          onClick={() => handleReasonSubmit(
                            detail.id,
                            detail.overshoot
                          )}
                          className="sd-btn-save sd-btn-save-primary"
                          disabled={saving[detail.id]}
                        >
                          {saving[detail.id] ? (
                            <span className="sd-btn-loader"></span>
                          ) : (
                            hasReason ? "Save Changes" : "Save Changes"
                          )}
                        </button>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>

            <div className="sd-modal-footer">
              <button
                className="sd-btn-close"
                onClick={() => setOpenDetail(null)}
              >
                Close
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
};

export default SetupDetails;
