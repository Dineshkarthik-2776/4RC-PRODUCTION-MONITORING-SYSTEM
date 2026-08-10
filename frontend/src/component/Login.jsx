import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import "../css/login.css";

const Login = () => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const navigate = useNavigate();
  const [formData, setFormData] = useState({
    email: "",
    password: "",
    name: ""
  });
  const [isSignup, setIsSignup] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
    setError("");
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const endpoint = isSignup ? `${API_BASE_URL}/api/auth/signup/` : `${API_BASE_URL}/api/auth/login/`;
      const payload = isSignup 
        ? { email: formData.email, password: formData.password, name: formData.name }
        : { email: formData.email, password: formData.password };

      const response = await axios.post(endpoint, payload);

      if (response.data.token) {
        localStorage.setItem("authToken", response.data.token);
        localStorage.setItem("user", JSON.stringify(response.data.user));
        
        // Fetch recipe names after successful login
        try {
          const recipeResponse = await axios.get(`${API_BASE_URL}/api/recipe-export-master/`, {
            params: { format: 'json' },
            headers: { 'Authorization': `Token ${response.data.token}` }
          });
          
          const recipeNames = Object.keys(recipeResponse.data);
          localStorage.setItem('recipeNames', JSON.stringify(recipeNames));
          console.log('Recipe names fetched on login:', recipeNames);
        } catch (recipeErr) {
          console.error('Failed to fetch recipe names on login:', recipeErr);
          // Don't block login if recipe fetch fails
        }
        
        navigate("/");
      }
    } catch (err) {
      if (err.response?.data) {
        const errorData = err.response.data;
        if (errorData.email) {
          setError(errorData.email[0]);
        } else if (errorData.message) {
          setError(errorData.message);
        } else {
          setError("Authentication failed. Please try again.");
        }
      } else {
        setError("Network error. Please check your connection.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-wrapper">
      {/* Left Panel - Minimal Professional Branding */}
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

        {/* Minimal Tyre Animation
        <div className="visual-element">
          <div className="tyre-circle">
            <div className="tyre-ring"></div>
            <div className="tyre-center">
              <span className="tyre-label">4RC</span>
            </div>
          </div>
        </div> */}

        <div className="footer-text">
          <p>© 2025 JK Tyre & Industries Ltd.</p>
        </div>
      </div>

      {/* Right Panel - Login Form */}
      <div className="right-panel">
        <div className="login-card">
          <div className="card-header">
            <h2 className="login-title">
              {isSignup ? "Create Account" : "Welcome Back"}
            </h2>
            <p className="login-subtitle">
              {isSignup 
                ? "Sign up to access the dashboard" 
                : "Login to access Production Monitoring Dashboard"}
            </p>
          </div>

          {/* Error Message - Amazon/Instagram Style ONLY */}
          {error && (
            <div className="error-banner">
              <svg className="error-icon" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
              <span className="error-text">{error}</span>
            </div>
          )}

          {/* Login/Signup Form - ORIGINAL DESIGN */}
          <form onSubmit={handleSubmit} className="login-form">
            {isSignup && (
              <div className="form-group">
                <label className="form-label">Full Name</label>
                <input
                  type="text"
                  name="name"
                  placeholder="Enter your full name"
                  className="form-input"
                  value={formData.name}
                  onChange={handleChange}
                  required
                />
              </div>
            )}

            <div className="form-group">
              <label className="form-label">Email Address</label>
              <input
                type="email"
                name="email"
                placeholder="Enter your email"
                className="form-input"
                value={formData.email}
                onChange={handleChange}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                type="password"
                name="password"
                placeholder="Enter your password"
                className="form-input"
                value={formData.password}
                onChange={handleChange}
                required
              />
            </div>

            {!isSignup && (
              <div className="form-options">
                <label className="remember-me">
                  <input 
                    type="checkbox" 
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                  />
                  <span>Remember Me</span>
                </label>
                <a href="#" className="forgot-password">
                  Forgot Password?
                </a>
              </div>
            )}

            <button
              type="submit"
              className="submit-btn"
              disabled={loading}
            >
              {loading ? (
                <span className="loading-spinner"></span>
              ) : (
                isSignup ? "Sign Up" : "Login"
              )}
            </button>
          </form>

          <div className="toggle-auth">
            <p>
              {isSignup ? "Already have an account?" : "Don't have an account?"}{" "}
              <button 
                type="button"
                onClick={() => {
                  setIsSignup(!isSignup);
                  setError("");
                  setFormData({ email: "", password: "", name: "" });
                }}
                className="toggle-btn"
              >
                {isSignup ? "Login" : "Sign Up"}
              </button>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
