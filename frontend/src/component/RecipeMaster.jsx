
import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";
import "../css/RecipeMaster.css";
// fixed error for main

const RecipeMaster = ({ isOpen, onClose, missingWarning, missingRecipes }) => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const ALLOWED_RECIPE_TYPES = ["Fabric", "Steel"];

  const [recipes, setRecipes] = useState([]);
  const [initialRecipes, setInitialRecipes] = useState([]);
  const [newRecipe, setNewRecipe] = useState({
    recipe_code: "",
    sap_code: "",
    recipe_type: "",
    target_speed: "",
  });
  const [loading, setLoading] = useState(false);
  const [updatingRecipeCode, setUpdatingRecipeCode] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [resultPopup, setResultPopup] = useState({
    isOpen: false,
    type: "success",
    message: "",
  });

  useEffect(() => {
    if (isOpen) {
      fetchRecipes();
    }
  }, [isOpen]);

  const normalizeRecipeType = (value) => {
    const type = String(value || "").trim().toLowerCase();
    if (type === "fabric") return "Fabric";
    if (type === "steel") return "Steel";
    return "";
  };

  const normalizeRecipe = (recipe) => ({
    recipe_code: recipe.recipe_code || "",
    sap_code: recipe.sap_code || "",
    recipe_type: normalizeRecipeType(recipe.recipe_type),
    target_speed: recipe.target_speed ?? "",
  });

  const fetchRecipes = async () => {
    setLoading(true);
    setError("");

    try {
      const token = localStorage.getItem("authToken");
      const response = await axios.get(`${API_BASE_URL}/api/recipe-master/`, {
        headers: { Authorization: `Token ${token}` },
      });

      const normalized = (response.data || []).map(normalizeRecipe);
      setRecipes(normalized);
      setInitialRecipes(normalized);
    } catch (err) {
      console.error("Failed to load Recipe Master:", err);
      setError("Failed to load Recipe Master data.");
    } finally {
      setLoading(false);
    }
  };

  const changedRecipes = useMemo(
    () =>
      recipes.filter((recipe) => {
        const original = initialRecipes.find((item) => item.recipe_code === recipe.recipe_code);
        if (!original) return false;

        const originalSpeed = original.target_speed === "" ? "" : Number(original.target_speed);
        const currentSpeed = recipe.target_speed === "" ? "" : Number(recipe.target_speed);

        return original.recipe_type !== recipe.recipe_type || originalSpeed !== currentSpeed;
      }),
    [recipes, initialRecipes]
  );

  const handleCellChange = (recipeCode, field, value) => {
    const nextValue = field === "recipe_type" ? normalizeRecipeType(value) : value;
    setRecipes((prev) =>
      prev.map((recipe) =>
        recipe.recipe_code === recipeCode ? { ...recipe, [field]: nextValue } : recipe
      )
    );
  };

  const openResultPopup = (type, message) => {
    setResultPopup({ isOpen: true, type, message });
  };

  const handleUpdateSingleRecipe = async (recipeCode) => {
    setError("");
    setSuccess("");

    const recipe = recipes.find((item) => item.recipe_code === recipeCode);
    const original = initialRecipes.find((item) => item.recipe_code === recipeCode);

    if (!recipe || !original) {
      openResultPopup("error", "Recipe row not found.");
      return;
    }

    const originalSpeed = original.target_speed === "" ? "" : Number(original.target_speed);
    const currentSpeed = recipe.target_speed === "" ? "" : Number(recipe.target_speed);
    const hasChanged = original.recipe_type !== recipe.recipe_type || originalSpeed !== currentSpeed;

    if (!hasChanged) {
      openResultPopup("error", "No changes found for this recipe row.");
      return;
    }

    if (!ALLOWED_RECIPE_TYPES.includes(recipe.recipe_type) || recipe.target_speed === "" || Number.isNaN(Number(recipe.target_speed)) || Number(recipe.target_speed) < 1) {
      openResultPopup("error", "Recipe type is required and target speed must be 1 or greater.");
      return;
    }

    setUpdatingRecipeCode(recipeCode);
    try {
      const token = localStorage.getItem("authToken");
      await axios.patch(
        `${API_BASE_URL}/api/recipe-master/${encodeURIComponent(recipe.recipe_code)}/`,
        {
          target_speed: Number(recipe.target_speed),
          recipe_type: recipe.recipe_type,
        },
        {
          headers: {
            Authorization: `Token ${token}`,
            "Content-Type": "application/json",
          },
        }
      );

      setSuccess(`Recipe ${recipeCode} updated successfully.`);
      openResultPopup("success", `Recipe ${recipeCode} updated successfully.`);
      await fetchRecipes();
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      console.error("Row update error:", err);
      const errorMessage = err.response?.data?.error || err.response?.data?.message || "Failed to update recipe.";
      setError(errorMessage);
      openResultPopup("error", errorMessage);
    } finally {
      setUpdatingRecipeCode("");
    }
  };

  const handleSubmitUpdates = async () => {
    setError("");
    setSuccess("");

    if (changedRecipes.length === 0) {
      setError("No changes detected to update.");
      openResultPopup("error", "No changes detected to update.");
      return;
    }

    const invalidRows = changedRecipes.filter(
      (recipe) =>
        recipe.target_speed === "" ||
        Number.isNaN(Number(recipe.target_speed)) ||
        Number(recipe.target_speed) < 1 ||
        !ALLOWED_RECIPE_TYPES.includes(recipe.recipe_type)
    );

    if (invalidRows.length > 0) {
      setError("Please provide recipe type and target speed of at least 1 for all changed rows.");
      openResultPopup("error", "Please provide recipe type and target speed of at least 1 for all changed rows.");
      return;
    }

    setLoading(true);

    try {
      const token = localStorage.getItem("authToken");

      await Promise.all(
        changedRecipes.map((recipe) =>
          axios.patch(
            `${API_BASE_URL}/api/recipe-master/${encodeURIComponent(recipe.recipe_code)}/`,
            {
              target_speed: Number(recipe.target_speed),
              recipe_type: recipe.recipe_type,
            },
            {
              headers: {
                Authorization: `Token ${token}`,
                "Content-Type": "application/json",
              },
            }
          )
        )
      );

      setSuccess(`Updated ${changedRecipes.length} recipe(s) successfully.`);
      openResultPopup("success", `Updated ${changedRecipes.length} recipe(s) successfully.`);
      await fetchRecipes();
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      console.error("Update error:", err);
      const errorMessage = err.response?.data?.error || err.response?.data?.message || "Failed to update Recipe Master.";
      setError(errorMessage);
      openResultPopup("error", errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleNewRecipeFieldChange = (field, value) => {
    setNewRecipe((prev) => ({ ...prev, [field]: value }));
  };

  const handleAddSingleRecipe = async () => {
    setError("");
    setSuccess("");

    if (!newRecipe.recipe_code.trim()) {
      setError("Recipe code is required for adding a new recipe.");
      openResultPopup("error", "Recipe code is required for adding a new recipe.");
      return;
    }

    if (!newRecipe.recipe_type) {
      setError("Recipe type is required for adding a new recipe.");
      openResultPopup("error", "Recipe type is required for adding a new recipe.");
      return;
    }

    if (
      newRecipe.target_speed === "" ||
      Number.isNaN(Number(newRecipe.target_speed)) ||
      Number(newRecipe.target_speed) < 1
    ) {
      setError("Target speed must be 1 or greater for new recipe.");
      openResultPopup("error", "Target speed must be 1 or greater for new recipe.");
      return;
    }

    setLoading(true);
    try {
      const token = localStorage.getItem("authToken");
      await axios.post(
        `${API_BASE_URL}/api/recipe-master/`,
        {
          recipe_code: newRecipe.recipe_code.trim(),
          sap_code: newRecipe.sap_code.trim() || null,
          recipe_type: newRecipe.recipe_type,
          target_speed: Number(newRecipe.target_speed),
        },
        {
          headers: {
            Authorization: `Token ${token}`,
            "Content-Type": "application/json",
          },
        }
      );

      setSuccess("New recipe added successfully.");
      openResultPopup("success", "New recipe added successfully.");
      setNewRecipe({ recipe_code: "", sap_code: "", recipe_type: "", target_speed: "" });
      await fetchRecipes();
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      console.error("Create recipe error:", err);
      const errorMessage = err.response?.data?.error || err.response?.data?.message || "Failed to add new recipe.";
      setError(errorMessage);
      openResultPopup("error", errorMessage);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="rm-overlay" onClick={onClose} />

      <div className="rm-modal">
        <div className="rm-header">
          <h2 className="rm-title">Recipe Master</h2>
          <button className="rm-close-btn" onClick={onClose}>
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

          {missingWarning && (
            <div className="rm-alert rm-alert-error" style={{ backgroundColor: "#fff3cd", color: "#856404", borderColor: "#ffeeba" }}>
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <div>
                <strong style={{ display: "block", marginBottom: "4px" }}>Missing Targets Detected!</strong>
                <span>The following recipes have run on the machine but are missing target speeds:</span>
                <div style={{ marginTop: "5px", fontWeight: "bold" }}>{missingRecipes.join(", ")}</div>
                <span style={{ display: "block", marginTop: "5px", fontSize: "0.9em" }}>
                  Data processing is paused for these intervals. Please update them to resume data flow.
                </span>
              </div>
            </div>
          )}

          <div className="rm-section">
            <h3 className="rm-section-title">Edit Existing Recipes</h3>

            <div className="rm-table-container">
              <table className="rm-table">
                <thead>
                  <tr>
                    <th>Recipe Code</th>
                    <th>SAP Code</th>
                    <th>Recipe Type</th>
                    <th>Target Speed (m/min)</th>
                    <th className="rm-th-action">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {recipes.map((recipe) => {
                    const isSpeedMissing = recipe.target_speed === "" || recipe.target_speed === null;

                    return (
                      <tr key={recipe.recipe_code}>
                        <td>
                          <input type="text" className="rm-input" value={recipe.recipe_code} disabled readOnly />
                        </td>
                        <td>
                          <input
                            type="text"
                            className={`rm-input ${recipe.sap_code ? "" : "rm-input-na"}`.trim()}
                            value={recipe.sap_code || "N/A"}
                            disabled
                            readOnly
                          />
                        </td>
                        <td>
                          <select
                            className="rm-select"
                            value={recipe.recipe_type}
                            onChange={(e) => handleCellChange(recipe.recipe_code, "recipe_type", e.target.value)}
                          >
                            <option value="">Select Type</option>
                            <option value="Fabric">Fabric</option>
                            <option value="Steel">Steel</option>
                          </select>
                        </td>
                        <td>
                          <div className="rm-target-wrap">
                            <input
                              type="number"
                              className="rm-input"
                              placeholder="Enter speed"
                              value={recipe.target_speed}
                              onChange={(e) => handleCellChange(recipe.recipe_code, "target_speed", e.target.value)}
                              min="1"
                              step="0.1"
                            />
                            {isSpeedMissing && <span className="rm-missing-target">!</span>}
                          </div>
                        </td>
                        <td className="rm-td-action">
                          <button
                            className="rm-btn rm-btn-submit rm-btn-row-update"
                            onClick={() => handleUpdateSingleRecipe(recipe.recipe_code)}
                            disabled={loading || updatingRecipeCode === recipe.recipe_code}
                          >
                            {updatingRecipeCode === recipe.recipe_code ? "Updating..." : "Update"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {recipes.length === 0 && !loading && (
                    <tr>
                      <td colSpan="5" style={{ textAlign: "center", padding: "20px", color: "#666" }}>
                        No recipes available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="rm-button-group">
              <button className="rm-btn rm-btn-submit" onClick={handleSubmitUpdates} disabled={loading}>
                {loading ? (
                  <span className="rm-spinner"></span>
                ) : (
                  <>
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    Update All
                  </>
                )}
              </button>
            </div>
          </div>

          <div className="rm-divider">
            <span>OR</span>
          </div>

          <div className="rm-section">
            <h3 className="rm-section-title">Add Single Recipe</h3>

            <div className="rm-table-container">
              <table className="rm-table">
                <thead>
                  <tr>
                    <th>Recipe Code</th>
                    <th>SAP Code</th>
                    <th>Recipe Type</th>
                    <th>Target Speed (m/min)</th>
                    <th className="rm-th-action">Action</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <input
                        type="text"
                        className="rm-input"
                        placeholder="e.g., RC001"
                        value={newRecipe.recipe_code}
                        onChange={(e) => handleNewRecipeFieldChange("recipe_code", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        className="rm-input"
                        placeholder="Optional SAP code"
                        value={newRecipe.sap_code}
                        onChange={(e) => handleNewRecipeFieldChange("sap_code", e.target.value)}
                      />
                    </td>
                    <td>
                      <select
                        className="rm-select"
                        value={newRecipe.recipe_type}
                        onChange={(e) => handleNewRecipeFieldChange("recipe_type", e.target.value)}
                      >
                        <option value="">Select Type</option>
                        <option value="Fabric">Fabric</option>
                        <option value="Steel">Steel</option>
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        className="rm-input"
                        placeholder="Enter speed"
                        value={newRecipe.target_speed}
                        onChange={(e) => handleNewRecipeFieldChange("target_speed", e.target.value)}
                        min="1"
                        step="0.1"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="rm-button-group">
              <button className="rm-btn rm-btn-submit" onClick={handleAddSingleRecipe} disabled={loading}>
                {loading ? (
                  <span className="rm-spinner"></span>
                ) : (
                  <>
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Add Recipe
                  </>
                )}
              </button>
            </div>
          </div>

        </div>
      </div>

      {resultPopup.isOpen && (
        <>
          <div className="rm-overlay" onClick={() => setResultPopup((prev) => ({ ...prev, isOpen: false }))} />
          <div className="rm-modal" style={{ maxWidth: "520px", width: "90vw", maxHeight: "unset" }}>
            <div className="rm-header">
              <h2 className="rm-title" style={{ fontSize: "20px" }}>
                {resultPopup.type === "success" ? "Success" : "Error"}
              </h2>
            </div>
            <div className="rm-body" style={{ paddingTop: "20px" }}>
              <div className={`rm-alert ${resultPopup.type === "success" ? "rm-alert-success" : "rm-alert-error"}`} style={{ marginBottom: "20px" }}>
                <span>{resultPopup.message}</span>
              </div>
              <div className="rm-button-group" style={{ justifyContent: "flex-end" }}>
                <button className="rm-btn rm-btn-submit" onClick={() => setResultPopup((prev) => ({ ...prev, isOpen: false }))}>
                  OK
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
};

export default RecipeMaster;

