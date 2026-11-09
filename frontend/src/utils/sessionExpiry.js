import axios from "axios";

/**
 * Sends the app back to the sign-in screen when the stored token stops
 * working.
 *
 * Every component calls axios directly with an Authorization header read
 * from localStorage, so a token the server no longer recognises -- revoked
 * by a password change, cleared by an administrator reset, or wiped when the
 * database was reseeded -- surfaced as whatever banner that component
 * happened to render. "Invalid token." next to a password field reads as if
 * the password were wrong, which it never is: the request is rejected before
 * any view sees it.
 *
 * Installed once on the default axios instance, so it covers every request
 * in the app without each caller having to remember.
 */
const LOGIN_PATH = "/login";

axios.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("authToken");
      localStorage.removeItem("user");
      if (window.location.pathname !== LOGIN_PATH) {
        // A full navigation rather than a router redirect: this runs outside
        // React, and it also clears any state built on the dead session.
        window.location.replace(
          `${LOGIN_PATH}?expired=1`
        );
      }
    }
    return Promise.reject(error);
  }
);
