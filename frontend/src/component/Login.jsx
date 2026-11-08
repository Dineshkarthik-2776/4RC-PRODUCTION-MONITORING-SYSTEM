import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import "../css/login.css";

const MODE_LOGIN = "login";
const MODE_SETUP = "setup";

const Login = () => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const navigate = useNavigate();

  const [mode, setMode] = useState(MODE_LOGIN);
  const [formData, setFormData] = useState({
    employee_id: "",
    password: "",
    confirm_password: "",
  });
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // Arriving here with ?expired=1 means a request came back 401 and the
  // session was dropped -- say so, rather than leaving a blank form that
  // looks like an ordinary sign-out.
  const [notice, setNotice] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    // A shift ending is the ordinary case, not a fault -- say which it
    // was, so an operator at 15:00 is not left wondering what broke.
    if (params.has("shift_ended")) {
      return "Your shift has ended, so you were signed out. "
        + "Sign in again if you are staying on.";
    }
    if (params.has("expired")) {
      return "Your session has ended. Please sign in again.";
    }
    return "";
  });

  const isSetup = mode === MODE_SETUP;

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
    setError("");
  };

  const switchMode = (next) => {
    setMode(next);
    setError("");
    setNotice("");
    setFormData({ ...formData, password: "", confirm_password: "" });
  };

  // The API returns field errors, non_field_errors, or a message. Flatten
  // whichever arrived into one line the operator can act on.
  const readError = (data) => {
    if (!data) return "Network error. Please check your connection.";
    if (typeof data === "string") return data;
    if (data.non_field_errors?.length) return data.non_field_errors[0];
    if (data.message) return data.message;
    for (const key of ["employee_id", "password", "confirm_password", "error"]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === "string") return value;
    }
    return "Something went wrong. Please try again.";
  };

  const cacheRecipeNames = async (token) => {
    // Populates the recipe filter without waiting for the dashboard to load.
    try {
      const response = await axios.get(
        `${API_BASE_URL}/api/recipe-export-master/`,
        { headers: { Authorization: `Token ${token}` } }
      );
      localStorage.setItem(
        "recipeNames",
        JSON.stringify(Object.keys(response.data))
      );
    } catch {
      // Not worth blocking sign-in over.
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    if (isSetup && formData.password !== formData.confirm_password) {
      setError("The two passwords do not match.");
      setLoading(false);
      return;
    }

    try {
      const endpoint = isSetup
        ? `${API_BASE_URL}/api/auth/claim/`
        : `${API_BASE_URL}/api/auth/login/`;
      const payload = isSetup
        ? {
            employee_id: formData.employee_id,
            password: formData.password,
            confirm_password: formData.confirm_password,
          }
        : { employee_id: formData.employee_id, password: formData.password };

      const response = await axios.post(endpoint, payload);

      if (response.data.token) {
        localStorage.setItem("authToken", response.data.token);
        localStorage.setItem("user", JSON.stringify(response.data.user));
        await cacheRecipeNames(response.data.token);
        navigate("/");
      }
    } catch (err) {
      const data = err.response?.data;
      const message = readError(data);
      setError(message);

      // An account that has never been claimed, or one an administrator has
      // reset, can only move forward by setting a password -- so offer that
      // instead of leaving the person stuck on a failed sign-in.
      if (!isSetup && message.toLowerCase().includes("not been set up")) {
        setMode(MODE_SETUP);
        setError("");
        setNotice(
          "This account needs a password. Choose one to finish setting it up."
        );
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-wrapper">
      <div className="left-panel">
        <div className="brand-section">
          <img
            src="https://vectorseek.com/wp-content/uploads/2023/08/JK-Tyre-Logo-Vector.svg-.png"
            alt="JK Tyre Logo"
            className="brand-logo"
          />
          <h1 className="brand-title">4RC Production Dashboard</h1>
          <p className="brand-tagline">Advancing Mobility Through Innovation</p>
        </div>

        <div className="footer-text">
          <p>© 2025 JK Tyre &amp; Industries Ltd.</p>
        </div>
      </div>

      <div className="right-panel">
        <div className="login-card">
          <div className="card-header">
            <h2 className="login-title">
              {isSetup ? "Set Your Password" : "Welcome Back"}
            </h2>
            <p className="login-subtitle">
              {isSetup
                ? "First time here, or password reset? Choose a password for your employee ID."
                : "Login to access Production Monitoring Dashboard"}
            </p>
          </div>

          {notice && (
            <div className="error-banner" style={{ background: "#eef5ff", borderColor: "#b6d4fe" }}>
              <span className="error-text" style={{ color: "#084298" }}>{notice}</span>
            </div>
          )}

          {error && (
            <div className="error-banner">
              <svg className="error-icon" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                  clipRule="evenodd"
                />
              </svg>
              <span className="error-text">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="login-form">
            <div className="form-group">
              <label className="form-label">Employee ID</label>
              <input
                type="text"
                name="employee_id"
                placeholder="Enter your employee ID"
                className="form-input"
                value={formData.employee_id}
                onChange={handleChange}
                autoComplete="username"
                autoCapitalize="characters"
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">
                {isSetup ? "New Password" : "Password"}
              </label>
              <input
                type="password"
                name="password"
                placeholder={isSetup ? "At least 8 characters" : "Enter your password"}
                className="form-input"
                value={formData.password}
                onChange={handleChange}
                autoComplete={isSetup ? "new-password" : "current-password"}
                required
              />
            </div>

            {isSetup && (
              <div className="form-group">
                <label className="form-label">Confirm Password</label>
                <input
                  type="password"
                  name="confirm_password"
                  placeholder="Type it again"
                  className="form-input"
                  value={formData.confirm_password}
                  onChange={handleChange}
                  autoComplete="new-password"
                  required
                />
              </div>
            )}

            {!isSetup && (
              <div className="form-options">
                <label className="remember-me">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                  />
                  <span>Remember Me</span>
                </label>
                <button
                  type="button"
                  className="toggle-btn"
                  onClick={() => switchMode(MODE_SETUP)}
                >
                  Forgot password?
                </button>
              </div>
            )}

            <button type="submit" className="submit-btn" disabled={loading}>
              {loading ? (
                <span className="loading-spinner"></span>
              ) : isSetup ? (
                "Set Password & Continue"
              ) : (
                "Login"
              )}
            </button>
          </form>

          <div className="toggle-auth">
            {isSetup ? (
              <p>
                Already have a password?{" "}
                <button
                  type="button"
                  onClick={() => switchMode(MODE_LOGIN)}
                  className="toggle-btn"
                >
                  Back to login
                </button>
              </p>
            ) : (
              <p className="login-subtitle" style={{ margin: 0 }}>
                Accounts are created by your manager. If you have forgotten your
                password, ask the administrator to reset it.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
