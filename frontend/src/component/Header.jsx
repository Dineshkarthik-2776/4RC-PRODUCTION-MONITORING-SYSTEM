import React, { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import "../css/header.css";
import User from "../assets/user.png";
import RecipeMaster from "./RecipeMaster";
import StandardTimeMaster from "./StandardTimeMaster";
import { useNotification } from "../context/NotificationContext";

const Header = () => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isRecipeModalOpen, setIsRecipeModalOpen] = useState(false);
  const [isStandardTimeModalOpen, setIsStandardTimeModalOpen] = useState(false);
  const [isCorrectionModalOpen, setIsCorrectionModalOpen] = useState(false);
  const [correctionRequests, setCorrectionRequests] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [processingRequest, setProcessingRequest] = useState(null);
  const [missingWarning, setMissingWarning] = useState(false);
  const [missingRecipes, setMissingRecipes] = useState([]);
  const dropdownRef = useRef(null);
  const pollingRef = useRef(null);
  const navigate = useNavigate();
  const { showNotification } = useNotification();

  // Get user data from localStorage
  const user = JSON.parse(localStorage.getItem("user") || '{}');
  const isManager = user.role === 'manager' || user.role === 'admin';

  // Fetch correction requests (Manager only)
  const fetchCorrectionRequests = async () => {
    if (!isManager) return;

    try {
      const token = localStorage.getItem('authToken');
      if (!token) return;

      const response = await axios.get(`${API_BASE_URL}/api/correction-requests/`, {
        headers: { 'Authorization': `Token ${token}` }
      });

      setCorrectionRequests(response.data);
      setPendingCount(response.data.length);
    } catch (err) {
      console.error('Failed to fetch correction requests:', err);
    }
  };

  // Fetch missing recipe warnings
  const fetchMissingWarnings = async () => {
    if (!isManager) return;
    try {
      const token = localStorage.getItem('authToken');
      if (!token) return;
      const response = await axios.get(`${API_BASE_URL}/api/missing-recipes-warning/`, {
        headers: { 'Authorization': `Token ${token}` }
      });
      setMissingWarning(response.data.warning);
      setMissingRecipes(response.data.missing_recipe_codes || []);
    } catch (err) {
      console.error('Failed to fetch missing recipe warnings:', err);
    }
  };

  // Handle approve/reject action
  const handleCorrectionAction = async (requestId, action) => {
    setProcessingRequest(requestId);
    
    try {
      const token = localStorage.getItem('authToken');
      if (!token) {
        showNotification('Authentication required', 'error');
        return;
      }

      await axios.post(
        `${API_BASE_URL}/api/correction-requests/${requestId}/action/`,
        { action }, // "approve" or "reject"
        {
          headers: {
            'Authorization': `Token ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );

      showNotification(`Request ${action}d successfully!`, 'success');
      
      // Refresh the list
      await fetchCorrectionRequests();
      
    } catch (err) {
      console.error('Failed to process request:', err);
      showNotification(`Failed to ${action} request: ${err.response?.data?.error || err.message}`, 'error');
    } finally {
      setProcessingRequest(null);
    }
  };

  // Start polling for correction requests (every 2 minutes for managers)
  useEffect(() => {
    if (isManager) {
      fetchCorrectionRequests(); // Initial fetch
      fetchMissingWarnings();
      
      pollingRef.current = setInterval(() => {
        fetchCorrectionRequests();
        fetchMissingWarnings();
      }, 2 * 60 * 1000); // 2 minutes

      return () => {
        if (pollingRef.current) {
          clearInterval(pollingRef.current);
        }
      };
    }
  }, [isManager]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsDropdownOpen(false);
      }
    };

    if (isDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isDropdownOpen]);

  // Handle logout
  const handleLogout = () => {
    localStorage.removeItem("authToken");
    localStorage.removeItem("user");
    navigate("/login");
  };

  // Toggle dropdown
  const toggleDropdown = () => {
    setIsDropdownOpen(!isDropdownOpen);
  };

  const openDocumentation = () => {
    const docUrl = `${import.meta.env.BASE_URL}${encodeURIComponent('4RC Documentation.pdf')}`;
    window.open(docUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <>
      <header className="h-header">
        {/* Logo - Left Side */}
        <div className="h-logo-container">
          <img 
            src="https://vectorseek.com/wp-content/uploads/2023/08/JK-Tyre-Logo-Vector.svg-.png" 
            alt="JK Tyre Logo" 
            className="h-logo" 
          />
        </div>

        {/* Right Side - Recipe Master Icon + Bell Icon + User Dropdown */}
        <div className="h-right-section">

          <button
            type="button"
            className="h-doc-button"
            onClick={openDocumentation}
            title="Open 4RC Documentation"
          >
            <svg
              className="h-doc-icon"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M7 21h10a2 2 0 002-2V7l-5-5H7a2 2 0 00-2 2v15a2 2 0 002 2z"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 13h6M9 17h6M9 9h2"
              />
            </svg>
            <span>Documentation</span>
          </button>
          
          {/* Recipe Master Icon - SAME AS NOTIFICATION */}
          <div 
            className="h-notification-icon"
            onClick={() => setIsRecipeModalOpen(true)}
            title="Recipe Master"
          >
            <svg 
              className="h-bell-icon" 
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
              <path 
                strokeLinecap="round" 
                strokeLinejoin="round" 
                strokeWidth={2} 
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" 
              />
            </svg>
            {isManager && missingWarning && (
              <span className="h-notification-badge" style={{backgroundColor: '#e74c3c'}}>!</span>
            )}
          </div>

          {/* Standard Time Master Icon - ONLY MANAGERS */}
          {isManager && (
            <div 
              className="h-notification-icon"
              onClick={() => setIsStandardTimeModalOpen(true)}
              title="Standard Time Master"
            >
              <svg 
                className="h-bell-icon" 
                fill="none" 
                stroke="currentColor" 
                viewBox="0 0 24 24"
              >
                <path 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  strokeWidth={2} 
                  d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" 
                />
              </svg>
            </div>
          )}

          {/* Notification Bell Icon - Only for Managers */}
          {isManager && (
            <div 
              className="h-notification-icon"
              onClick={() => setIsCorrectionModalOpen(true)}
              title="Correction Requests"
            >
              <svg 
                className="h-bell-icon" 
                fill="none" 
                stroke="currentColor" 
                viewBox="0 0 24 24"
              >
                <path 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  strokeWidth={2} 
                  d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" 
                />
              </svg>
              {pendingCount > 0 && (
                <span className="h-notification-badge">{pendingCount}</span>
              )}
            </div>
          )}

          {/* User Dropdown */}
          <div className="h-user-dropdown" ref={dropdownRef}>
            <button 
              className="h-user-button" 
              onClick={toggleDropdown}
              aria-expanded={isDropdownOpen}
            >
              <img 
                src={User} 
                alt="User Icon" 
                className="h-user-avatar"
              />
              <span className="h-user-name">{user.name || "User"}</span>
              <svg 
                className={`h-chevron-icon ${isDropdownOpen ? 'h-chevron-open' : ''}`}
                fill="none" 
                stroke="currentColor" 
                viewBox="0 0 24 24"
              >
                <path 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  strokeWidth={2} 
                  d="M19 9l-7 7-7-7" 
                />
              </svg>
            </button>

            {/* Dropdown Menu */}
            {isDropdownOpen && (
              <div className="h-dropdown-menu">
                <div className="h-dropdown-header">
                  <p className="h-dropdown-user-name">{user.name || "User"}</p>
                  <p className="h-dropdown-user-email">{user.email || "user@jktyre.com"}</p>
                </div>
                <div className="h-dropdown-divider"></div>
                <button 
                  className="h-dropdown-item h-logout-button" 
                  onClick={handleLogout}
                >
                  <svg 
                    className="h-logout-icon" 
                    fill="none" 
                    stroke="currentColor" 
                    viewBox="0 0 24 24"
                  >
                    <path 
                      strokeLinecap="round" 
                      strokeLinejoin="round" 
                      strokeWidth={2} 
                      d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" 
                    />
                  </svg>
                  Logout
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Recipe Master Modal */}
      <RecipeMaster 
        isOpen={isRecipeModalOpen} 
        onClose={() => {
          setIsRecipeModalOpen(false);
          if (isManager) fetchMissingWarnings(); // Refresh when closed
        }}
        missingWarning={missingWarning}
        missingRecipes={missingRecipes}
      />

      {/* Correction Requests Modal */}
      {isCorrectionModalOpen && isManager && (
        <>
          <div 
            className="h-modal-overlay" 
            onClick={() => setIsCorrectionModalOpen(false)}
          />
          <div className="h-correction-modal">
            <div className="h-modal-header">
              <h2 className="h-modal-title">Pending Correction Requests</h2>
              <button
                className="h-modal-close"
                onClick={() => setIsCorrectionModalOpen(false)}
              >
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="h-modal-body">
              {correctionRequests.length === 0 ? (
                <div className="h-empty-state">
                  <svg className="h-empty-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p>No pending correction requests</p>
                </div>
              ) : (
                <div className="h-requests-list">
                  {correctionRequests.map((request) => (
                    <div key={request.id} className="h-request-card">
                      <div className="h-request-header">
                        <span className="h-request-id">Request #{request.id}</span>
                        <span className="h-request-changeover">Changeover ID: {request.changeover_id}</span>
                      </div>
                      
                      <div className="h-request-info">
                        <p className="h-request-user">
                          <strong>Recipe:</strong> {request.current_recipe || <span className="na-text">N/A</span>}
                        </p>
                        <p className="h-request-user">
                          <strong>Requested by:</strong> {request.requested_by_email}
                        </p>
                      </div>

                      <div className="h-request-changes">
                        <div className="h-change-section">
                          <h4>Previous Values:</h4>
                          <p><strong>Category:</strong> {request.old_category || 'None'}</p>
                          <p><strong>Reason:</strong> {request.old_reason || 'None'}</p>
                        </div>
                        <div className="h-arrow">→</div>
                        <div className="h-change-section">
                          <h4>Requested Changes:</h4>
                          <p><strong>Category:</strong> {request.new_category}</p>
                          <p><strong>Reason:</strong> {request.new_reason}</p>
                        </div>
                      </div>

                      <div className="h-request-actions">
                        <button
                          className="h-btn-approve"
                          onClick={() => handleCorrectionAction(request.id, 'approve')}
                          disabled={processingRequest === request.id}
                        >
                          {processingRequest === request.id ? 'Processing...' : 'Approve'}
                        </button>
                        <button
                          className="h-btn-reject"
                          onClick={() => handleCorrectionAction(request.id, 'reject')}
                          disabled={processingRequest === request.id}
                        >
                          {processingRequest === request.id ? 'Processing...' : 'Reject'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
      {/* Standard Time Master Modal */}
      <StandardTimeMaster 
        isOpen={isStandardTimeModalOpen} 
        onClose={() => setIsStandardTimeModalOpen(false)}
      />
    </>
  );
};

export default Header;
