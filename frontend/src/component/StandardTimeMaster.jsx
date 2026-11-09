import React, { useState, useEffect } from "react";
import axios from "axios";
import "../css/RecipeMaster.css"; // Reusing styles for consistency

const StandardTimeMaster = ({ isOpen, onClose, embedded = false }) => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

  const [standardTimes, setStandardTimes] = useState([]);
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Edit State
  const [editingItem, setEditingItem] = useState(null);
  const [editingStandardTime, setEditingStandardTime] = useState("");
  const [rules, setRules] = useState([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [newRule, setNewRule] = useState({ from_recipe: "", to_recipe: "" });

  // Add Custom State
  const [showAddCustom, setShowAddCustom] = useState(false);
  const [newCustom, setNewCustom] = useState({ changeover_key: "", standard_time: "" });

  useEffect(() => {
    if (isOpen) {
      fetchStandardTimes();
      fetchRecipes();
    }
  }, [isOpen]);

  const fetchRecipes = async () => {
    try {
      const token = localStorage.getItem("authToken");
      const response = await axios.get(`${API_BASE_URL}/api/recipe-master/`, {
        headers: { Authorization: `Token ${token}` },
      });
      setRecipes(response.data);
    } catch (err) {
      console.error("Failed to fetch recipes:", err);
    }
  };

  const fetchStandardTimes = async () => {
    setLoading(true);
    setError("");
    try {
      const token = localStorage.getItem("authToken");
      const response = await axios.get(`${API_BASE_URL}/api/standard-time/`, {
        headers: { Authorization: `Token ${token}` },
      });
      setStandardTimes(response.data);
    } catch (err) {
      console.error("Failed to fetch standard times:", err);
      setError("Failed to load standard times.");
    } finally {
      setLoading(false);
    }
  };

  const fetchRules = async (id) => {
    setRulesLoading(true);
    try {
      const token = localStorage.getItem("authToken");
      const response = await axios.get(`${API_BASE_URL}/api/standard-time/${id}/rules/`, {
        headers: { Authorization: `Token ${token}` },
      });
      setRules(response.data);
    } catch (err) {
      console.error("Failed to fetch rules:", err);
    } finally {
      setRulesLoading(false);
    }
  };

  const requestClose = () => {
    onClose();
  };

  const showMessage = (msg, isError = false) => {
    if (isError) {
      setError(msg);
      setTimeout(() => setError(""), 4000);
    } else {
      setSuccess(msg);
      setTimeout(() => setSuccess(""), 4000);
    }
  };

  // --- Main Table Actions ---

  const handleDeleteMaster = async (id) => {
    if (!window.confirm("Are you sure you want to delete this custom standard time and all its rules?")) return;
    try {
      const token = localStorage.getItem("authToken");
      await axios.delete(`${API_BASE_URL}/api/standard-time/${id}/`, {
        headers: { Authorization: `Token ${token}` },
      });
      showMessage("Custom changeover deleted successfully.");
      fetchStandardTimes();
    } catch (err) {
      showMessage(err.response?.data?.error || "Failed to delete.", true);
    }
  };

  const handleEditClick = (item) => {
    setEditingItem(item);
    setEditingStandardTime(item.standard_time);
    fetchRules(item.id);
  };

  // --- Edit Modal Actions ---

  const handleSaveTime = async () => {
    if (!editingStandardTime || Number(editingStandardTime) < 1) {
      showMessage("Standard time must be 1 or greater.", true);
      return;
    }
    try {
      const token = localStorage.getItem("authToken");
      await axios.post(
        `${API_BASE_URL}/api/standard-time/`,
        [{ changeover_key: editingItem.changeover_key, standard_time: parseFloat(editingStandardTime) }],
        { headers: { Authorization: `Token ${token}`, "Content-Type": "application/json" } }
      );
      showMessage("Standard time updated.");
      fetchStandardTimes(); // Refresh background list
      // Update local state for editing item to reflect new time
      setEditingItem({ ...editingItem, standard_time: editingStandardTime });
    } catch (err) {
      showMessage("Failed to update standard time.", true);
    }
  };

  const handleAddRule = async () => {
    if (!newRule.from_recipe || !newRule.to_recipe) {
      showMessage("Both From and To recipes are required.", true);
      return;
    }
    try {
      const token = localStorage.getItem("authToken");
      await axios.post(
        `${API_BASE_URL}/api/standard-time/${editingItem.id}/rules/`,
        newRule,
        { headers: { Authorization: `Token ${token}` } }
      );
      showMessage("Rule added successfully.");
      setNewRule({ from_recipe: "", to_recipe: "" });
      fetchRules(editingItem.id);
    } catch (err) {
      showMessage("Failed to add rule (it may already exist).", true);
    }
  };

  const handleDeleteRule = async (ruleId) => {
    try {
      const token = localStorage.getItem("authToken");
      await axios.delete(`${API_BASE_URL}/api/standard-time/${editingItem.id}/rules/${ruleId}/`, {
        headers: { Authorization: `Token ${token}` },
      });
      showMessage("Rule removed.");
      fetchRules(editingItem.id);
    } catch (err) {
      showMessage("Failed to delete rule.", true);
    }
  };

  // --- Add Custom Actions ---

  const handleAddCustomSubmit = async () => {
    if (!newCustom.changeover_key || !newCustom.standard_time || Number(newCustom.standard_time) < 1) {
      showMessage("Valid key and time >= 1 required.", true);
      return;
    }
    try {
      const token = localStorage.getItem("authToken");
      await axios.post(
        `${API_BASE_URL}/api/standard-time/`,
        { changeover_key: newCustom.changeover_key, standard_time: parseFloat(newCustom.standard_time) },
        { headers: { Authorization: `Token ${token}`, "Content-Type": "application/json" } }
      );
      showMessage("Custom standard time created.");
      setShowAddCustom(false);
      setNewCustom({ changeover_key: "", standard_time: "" });
      fetchStandardTimes();
    } catch (err) {
      showMessage(err.response?.data?.error || "Failed to create custom type.", true);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      {!embedded && <div className="rm-overlay" onClick={requestClose} />}

      <div
        className={embedded ? "rm-embedded" : "rm-modal"}
        style={embedded ? undefined : { maxWidth: "800px", width: "90vw" }}
      >
        <div className="rm-header">
          <h2 className="rm-title">Standard Time Master</h2>
          <button className="rm-close-btn" onClick={requestClose}>
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="rm-body">
          {success && (
            <div className="rm-alert rm-alert-success">
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>{success}</span>
            </div>
          )}

          {error && (
            <div className="rm-alert rm-alert-error">
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          <div className="rm-section">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "15px" }}>
              <h3 className="rm-section-title" style={{ margin: 0 }}>Manage Standard Times</h3>
              <button className="rm-btn rm-btn-submit" onClick={() => setShowAddCustom(true)}>
                + Add Custom Changeover
              </button>
            </div>

            <p className="rm-info-box" style={{ marginBottom: "15px", padding: "10px", fontSize: "0.9em" }}>
              These values are used to calculate "Shoot" (Overshoot) in the dashboard.
            </p>

            <div className="rm-table-container">
              <table className="rm-table">
                <thead>
                  <tr>
                    <th>Changeover Type (Key)</th>
                    <th>Standard Time (min)</th>
                    <th>Custom?</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {standardTimes.map((item) => (
                    <tr key={item.id}>
                      <td style={{ padding: "12px", fontWeight: "500" }}>{item.changeover_key}</td>
                      <td style={{ padding: "12px" }}>{item.standard_time} min</td>
                      <td style={{ padding: "12px" }}>
                        {item.is_custom ? (
                          <span style={{ background: "#e0f2fe", color: "#0369a1", padding: "2px 8px", borderRadius: "12px", fontSize: "0.8em" }}>Custom</span>
                        ) : (
                          <span style={{ background: "#f3f4f6", color: "#4b5563", padding: "2px 8px", borderRadius: "12px", fontSize: "0.8em" }}>Base</span>
                        )}
                      </td>
                      <td style={{ padding: "12px", textAlign: "right" }}>
                        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
                          <button className="rm-btn rm-btn-submit" style={{ padding: "4px 12px", fontSize: "0.85em" }} onClick={() => handleEditClick(item)}>
                            Edit
                          </button>
                          {item.is_custom && (
                            <button className="rm-btn" style={{ padding: "4px 12px", fontSize: "0.85em", backgroundColor: "#ef4444", color: "white" }} onClick={() => handleDeleteMaster(item.id)}>
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {standardTimes.length === 0 && !loading && (
                    <tr>
                      <td colSpan="4" style={{ textAlign: "center", padding: "20px", color: "#666" }}>
                        No standard times configured.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* --- Edit Modal --- */}
      {editingItem && (
        <>
          <div className="rm-overlay" style={{ zIndex: 3001 }} onClick={() => setEditingItem(null)} />
          <div className="rm-modal" style={{ zIndex: 3002, maxWidth: "600px", width: "90vw" }}>
            <div className="rm-header">
              <h2 className="rm-title">Edit: {editingItem.changeover_key}</h2>
              <button className="rm-close-btn" onClick={() => setEditingItem(null)}>
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="rm-body">
              <div className="rm-section" style={{ marginBottom: "20px" }}>
                <h4 style={{ margin: "0 0 10px 0", color: "#374151" }}>Standard Time (min)</h4>
                <div style={{ display: "flex", gap: "10px" }}>
                  <input
                    type="number"
                    className="rm-input"
                    value={editingStandardTime}
                    onChange={(e) => setEditingStandardTime(e.target.value)}
                    min="1"
                  />
                  <button className="rm-btn rm-btn-submit" onClick={handleSaveTime}>
                    Save Time
                  </button>
                </div>
              </div>

              <div className="rm-section">
                <h4 style={{ margin: "0 0 10px 0", color: "#374151" }}>Changeover Rules</h4>
                <p style={{ fontSize: "0.85em", color: "#6b7280", marginBottom: "15px", marginTop: 0 }}>
                  If a batch matches these specific previous and current recipes, it will use this standard time.
                </p>

                <div style={{ display: "flex", gap: "10px", marginBottom: "15px" }}>
                  <select
                    className="rm-input"
                    value={newRule.from_recipe}
                    onChange={(e) => setNewRule({ ...newRule, from_recipe: e.target.value })}
                    style={{ padding: "8px", borderRadius: "8px", border: "1px solid #d1d5db", flex: 1 }}
                  >
                    <option value="" disabled>Select From Recipe</option>
                    <option value="ANY">ANY (Generic wildcard)</option>
                    {recipes.map(r => (
                      <option key={r.id} value={r.recipe_code}>{r.recipe_code}</option>
                    ))}
                  </select>
                  <select
                    className="rm-input"
                    value={newRule.to_recipe}
                    onChange={(e) => setNewRule({ ...newRule, to_recipe: e.target.value })}
                    style={{ padding: "8px", borderRadius: "8px", border: "1px solid #d1d5db", flex: 1 }}
                  >
                    <option value="" disabled>Select To Recipe</option>
                    <option value="ANY">ANY (Generic wildcard)</option>
                    {recipes.map(r => (
                      <option key={r.id} value={r.recipe_code}>{r.recipe_code}</option>
                    ))}
                  </select>
                  <button className="rm-btn rm-btn-add" onClick={handleAddRule}>
                    Add
                  </button>
                </div>

                <div className="rm-table-container" style={{ maxHeight: "200px" }}>
                  <table className="rm-table">
                    <thead>
                      <tr>
                        <th>From Recipe</th>
                        <th>To Recipe</th>
                        <th style={{ textAlign: "right" }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rulesLoading ? (
                        <tr><td colSpan="3" style={{ textAlign: "center", padding: "10px" }}>Loading...</td></tr>
                      ) : rules.length > 0 ? (
                        rules.map((r) => (
                          <tr key={r.id}>
                            <td style={{ padding: "8px" }}>{r.from_recipe}</td>
                            <td style={{ padding: "8px" }}>{r.to_recipe}</td>
                            <td style={{ padding: "8px", textAlign: "right" }}>
                              <button
                                className="rm-btn"
                                style={{ padding: "4px 12px", fontSize: "0.8em", backgroundColor: "#ef4444", color: "white" }}
                                onClick={() => handleDeleteRule(r.id)}
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan="3" style={{ textAlign: "center", padding: "10px", color: "#666" }}>
                            No specific rules defined.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "20px" }}>
                <button className="rm-btn rm-btn-add" onClick={() => setEditingItem(null)}>
                  Done
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* --- Add Custom Modal --- */}
      {showAddCustom && (
        <>
          <div className="rm-overlay" style={{ zIndex: 3001 }} onClick={() => setShowAddCustom(false)} />
          <div className="rm-modal" style={{ zIndex: 3002, maxWidth: "500px", width: "90vw" }}>
            <div className="rm-header">
              <h2 className="rm-title">Add Custom Changeover</h2>
            </div>
            <div className="rm-body" style={{ paddingTop: "20px" }}>
              <div style={{ marginBottom: "15px" }}>
                <label style={{ display: "block", marginBottom: "5px", color: "#374151", fontWeight: "500" }}>
                  Changeover Key / Name
                </label>
                <input
                  type="text"
                  className="rm-input"
                  placeholder="e.g. COMPOUND ONLY"
                  value={newCustom.changeover_key}
                  onChange={(e) => setNewCustom({ ...newCustom, changeover_key: e.target.value })}
                />
              </div>
              <div style={{ marginBottom: "20px" }}>
                <label style={{ display: "block", marginBottom: "5px", color: "#374151", fontWeight: "500" }}>
                  Standard Time (min)
                </label>
                <input
                  type="number"
                  className="rm-input"
                  placeholder="e.g. 25"
                  value={newCustom.standard_time}
                  onChange={(e) => setNewCustom({ ...newCustom, standard_time: e.target.value })}
                  min="1"
                />
              </div>
              <div className="rm-button-group" style={{ justifyContent: "flex-end" }}>
                <button className="rm-btn" style={{ backgroundColor: "#9ca3af", color: "white" }} onClick={() => setShowAddCustom(false)}>
                  Cancel
                </button>
                <button className="rm-btn rm-btn-submit" onClick={handleAddCustomSubmit}>
                  Create
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
};

export default StandardTimeMaster;
