import React from "react";
import { useShift } from "../context/ShiftContext";
import "../css/shift.css";

const pad = (value) => String(value).padStart(2, "0");

const formatCountdown = (seconds) => {
  if (seconds === null || seconds < 0) return "--:--";
  // Hours only when there are any. The warning window is five minutes,
  // so this reads "04:59" in practice -- but mm:ss alone turned a wider
  // window into "419:24", which is not a time anybody reads.
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(rest)}`
    : `${pad(minutes)}:${pad(rest)}`;
};

/**
 * The warning an operator sees before their shift signs them out.
 *
 * Nothing is shown to anyone without a shift -- shift incharges, managers
 * and the administrator are never signed out automatically, so a countdown
 * would just be noise for them.
 */
const ShiftBanner = () => {
  const { duty, secondsLeft, isEndingSoon, signOut, signingOut } = useShift();

  if (!duty || !isEndingSoon) return null;

  return (
    <div className="sb-banner" role="status" aria-live="polite">
      <svg className="sb-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" strokeWidth={2} />
        <path strokeLinecap="round" strokeWidth={2} d="M12 7v5l3 2" />
      </svg>

      <div className="sb-text">
        <strong>{duty.shift} shift ends in {formatCountdown(secondsLeft)}.</strong>
        <span>
          You will be signed out automatically. Save any reason you are
          part-way through typing.
        </span>
      </div>

      <button
        type="button"
        className="sb-signout"
        onClick={() => signOut("manual")}
        disabled={signingOut}
      >
        {signingOut ? "Signing out..." : "Sign out now"}
      </button>
    </div>
  );
};

export default ShiftBanner;
