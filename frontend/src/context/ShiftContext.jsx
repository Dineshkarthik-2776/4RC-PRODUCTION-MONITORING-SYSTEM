import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

// How long before the boundary the warning appears. Long enough to finish
// typing a reason and hand over, short enough not to nag for an hour.
const WARN_SECONDS = 5 * 60;

const ShiftContext = createContext(null);

export const useShift = () => {
  const context = useContext(ShiftContext);
  if (!context) {
    throw new Error("useShift must be used within ShiftProvider");
  }
  return context;
};

/**
 * Keeps an operator's session inside their shift.
 *
 * An operator's sign-in is the record of who was on the line, so leaving
 * it open past the boundary would credit the next shift's setup time to
 * whoever forgot to sign out. The server closes the session and revokes
 * the token from a Celery task either way; this provider makes that
 * graceful rather than a surprise 401 -- it counts down, warns, and signs
 * out cleanly at the boundary.
 *
 * Only operators have a shift attached. `duty` comes back null for shift
 * incharges, managers and the administrator, and this provider then does
 * nothing at all -- they stay signed in across boundaries.
 */
export const ShiftProvider = ({ children }) => {
  const [duty, setDuty] = useState(null);
  const [secondsLeft, setSecondsLeft] = useState(null);
  const [signingOut, setSigningOut] = useState(false);
  const endedRef = useRef(false);

  const authHeaders = () => ({
    headers: { Authorization: `Token ${localStorage.getItem("authToken")}` },
  });

  const clearSession = () => {
    localStorage.removeItem("authToken");
    localStorage.removeItem("user");
  };

  const signOut = useCallback(async (reason) => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      // Tell the server first: this is what closes the duty session and
      // destroys the token. Dropping the token locally on its own would
      // leave the operator showing as still on the line.
      await axios.post(`${API_BASE_URL}/api/auth/logout/`, {}, authHeaders());
    } catch {
      // Already expired, or the network is down. Either way the local
      // session has to go, and the Celery task closes the duty record.
    } finally {
      clearSession();
      const query = reason === "shift_ended" ? "?shift_ended=1" : "";
      window.location.replace(`/login${query}`);
    }
  }, [signingOut]);

  // Pull the duty state from the server rather than trusting whatever the
  // login response left in localStorage -- a tab left open overnight would
  // otherwise count down to a boundary that has long since passed.
  const refresh = useCallback(async () => {
    if (!localStorage.getItem("authToken")) return;
    try {
      const response = await axios.get(`${API_BASE_URL}/api/auth/me/`, authHeaders());
      setDuty(response.data.duty || null);
    } catch {
      // A 401 is handled globally by the axios interceptor in
      // utils/sessionExpiry.js, which sends the browser to /login.
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // One timer, ticking against the server's boundary rather than counting
  // down a number we were handed: a laptop that slept through the shift
  // change wakes up already past zero, and should sign out immediately.
  useEffect(() => {
    if (!duty?.shift_ends_at) {
      setSecondsLeft(null);
      return undefined;
    }

    const endsAt = new Date(duty.shift_ends_at).getTime();

    const tick = () => {
      const remaining = Math.round((endsAt - Date.now()) / 1000);
      setSecondsLeft(remaining);
      if (remaining <= 0 && !endedRef.current) {
        endedRef.current = true;
        signOut("shift_ended");
      }
    };

    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [duty, signOut]);

  const value = {
    duty,
    secondsLeft,
    // Null, not false, when this session has no shift: the banner uses it
    // to tell "not an operator" apart from "plenty of time left".
    isEndingSoon:
      secondsLeft === null ? null : secondsLeft <= WARN_SECONDS && secondsLeft > 0,
    signingOut,
    signOut,
    refreshDuty: refresh,
  };

  return <ShiftContext.Provider value={value}>{children}</ShiftContext.Provider>;
};

export default ShiftContext;
