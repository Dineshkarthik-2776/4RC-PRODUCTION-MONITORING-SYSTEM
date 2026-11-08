import React, { useState, useRef, useEffect } from "react";
import axios from "axios";
import "../css/header.css";
import User from "../assets/user.png";
import SettingsModal from "./SettingsModal";
import { useNotification } from "../context/NotificationContext";
import { useShift } from "../context/ShiftContext";
import LineStatus from "./LineStatus";
import "../css/shift.css";

const Header = () => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isCorrectionModalOpen, setIsCorrectionModalOpen] = useState(false);
  const [correctionRequests, setCorrectionRequests] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [processingRequest, setProcessingRequest] = useState(null);
  const [missingWarning, setMissingWarning] = useState(false);
  const [missingRecipes, setMissingRecipes] = useState([]);
  const dropdownRef = useRef(null);
  const pollingRef = useRef(null);
  const { showNotification } = useNotification();
  const { duty, secondsLeft, isEndingSoon, signOut, signingOut } = useShift();

  // Get user data from localStorage
  const user = JSON.parse(localStorage.getItem("user") || '{}');
  // There is no admin role: an administrator is a manager carrying
  // is_superuser, so this one check covers both.
  const isManager = user.role === 'manager';
  // Approving is a shift decision, not only a management one, so the
  // correction queue is open to shift incharges too. Settings and the
  // master data stay with managers.
  const canApprove = ['manager', 'shift_incharge'].includes(user.role);

  // Fetch correction requests (Manager only)
  const fetchCorrectionRequests = async () => {
    if (!canApprove) return;

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
    if (!canApprove) return undefined;

    fetchCorrectionRequests(); // Initial fetch
    fetchMissingWarnings();    // no-ops for a shift incharge

    pollingRef.current = setInterval(() => {
      fetchCorrectionRequests();
      fetchMissingWarnings();
    }, 2 * 60 * 1000); // 2 minutes

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, [canApprove]);

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

  // Signing out goes through the server now: it closes the operator's
  // duty session and destroys the token. Dropping the token locally on
  // its own left them showing as still on the line.
  const handleLogout = () => signOut("manual");

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

          {/* Reporting a stoppage as it happens. First, because when the
              line is down this is the only thing anyone needs. */}
          <LineStatus />

          {/* Only operators have a shift attached, so only they see this. */}
          {duty && (
            <span
              className={`sb-chip ${isEndingSoon ? "sb-chip-ending" : ""}`}
              title={`${duty.shift} shift ends at ${new Date(duty.shift_ends_at)
                .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
            >
              <span className="sb-chip-dot" />
              {duty.shift} shift
              {secondsLeft !== null && secondsLeft > 0 && (
                <>
                  {" · "}
                  {Math.floor(secondsLeft / 3600)}h{" "}
                  {Math.floor((secondsLeft % 3600) / 60)}m left
                </>
              )}
            </span>
          )}

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
          

          {/* Settings -- masters and user administration, managers only */}
          {isManager && (
            <div
              className="h-notification-icon"
              onClick={() => setIsSettingsOpen(true)}
              title="Settings"
            >
              <svg className="h-bell-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
                />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {missingWarning && (
                <span className="h-notification-badge" style={{ backgroundColor: "#e74c3c" }}>!</span>
              )}
            </div>
          )}

          {/* Correction requests -- shift incharges approve these too */}
          {canApprove && (
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
                  <p className="h-dropdown-user-email">
                    {user.employee_id} · {user.role_display || user.role}
                  </p>
                </div>
                <div className="h-dropdown-divider"></div>
                <button 
                  className="h-dropdown-item h-logout-button" 
                  onClick={handleLogout}
                  disabled={signingOut}
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


      {/* Correction Requests Modal */}
      {isCorrectionModalOpen && canApprove && (
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
                          <strong>Requested by:</strong> {request.requested_by_label}
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
      <SettingsModal
        missingWarning={missingWarning}
        missingRecipes={missingRecipes}
        isOpen={isSettingsOpen}
        onClose={() => {
          setIsSettingsOpen(false);
          fetchMissingWarnings();
        }}
      />
    </>
  );
};

export default Header;
