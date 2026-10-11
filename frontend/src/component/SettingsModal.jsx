import React, { useState } from "react";
import RecipeMaster from "./RecipeMaster";
import StandardTimeMaster from "./StandardTimeMaster";
import UserManagement from "./UserManagement";
import "../css/settings.css";

const TABS = [
  { key: "users", label: "Users" },
  { key: "recipes", label: "Recipe Master" },
  { key: "standard", label: "Standard Time Master" },
];

/**
 * Manager-only settings, gathering the master-data screens that used to be
 * separate header icons. Correction Requests deliberately stays in the
 * header: it carries a live pending count a manager needs to see at a
 * glance, which would be buried in here.
 */
const SettingsModal = ({ isOpen, onClose, missingWarning, missingRecipes }) => {
  const [tab, setTab] = useState("users");

  if (!isOpen) return null;

  return (
    <>
      <div className="h-modal-overlay" onClick={onClose} />
      <div className="st-modal">
        <div className="h-modal-header">
          <h2 className="h-modal-title">Settings</h2>
          <button className="h-modal-close" onClick={onClose}>
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <div className="st-tabs">
          {TABS.map((item) => (
            <button
              key={item.key}
              className={`st-tab ${tab === item.key ? "st-tab-active" : ""}`}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="h-modal-body st-body">
          {tab === "users" && <UserManagement />}
          {tab === "recipes" && (
            <RecipeMaster
              isOpen={true}
              embedded={true}
              onClose={onClose}
              missingWarning={missingWarning}
              missingRecipes={missingRecipes}
            />
          )}
          {tab === "standard" && <StandardTimeMaster isOpen={true} embedded={true} onClose={onClose} />}
        </div>
      </div>
    </>
  );
};

export default SettingsModal;
