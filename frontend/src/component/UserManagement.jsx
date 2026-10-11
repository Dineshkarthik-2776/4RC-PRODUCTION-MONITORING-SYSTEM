import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";

const ROLES = [
  { value: "operator", label: "Operator" },
  { value: "shift_incharge", label: "Shift Incharge" },
  { value: "manager", label: "Manager" },
];

const STATUS_STYLE = {
  active: { background: "#e6f4ea", color: "#137333" },
  pending: { background: "#fef7e0", color: "#8a6d00" },
  retired: { background: "#f1f3f4", color: "#5f6368" },
};

/**
 * People administration, for managers.
 *
 * Managers add operators and shift incharges. Only the administrator can
 * create a manager or reset somebody's password, so those controls are
 * hidden unless the signed-in user is an administrator -- the API enforces
 * the same rules regardless.
 *
 * No password is ever typed here. A new account starts PENDING and the
 * person chooses their own password at first sign-in; a reset simply puts
 * them back in that state.
 */
const UserManagement = () => {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const currentUser = JSON.parse(localStorage.getItem("user") || "{}");

  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [savingId, setSavingId] = useState(null);
  const [confirmReset, setConfirmReset] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(null);

  // Changing the administrator's own password, in two steps: prove the
  // current one, then choose a new one. Nobody can reset the administrator
  // -- there is no higher account -- so this is the only way it changes.
  const [pwStep, setPwStep] = useState(null); // null | "current" | "new"
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [newUser, setNewUser] = useState({
    employee_id: "",
    name: "",
    role: "operator",
  });

  // The API tells us whether this account carries administrator privilege;
  // until the list loads we assume it does not.
  const [isAdmin, setIsAdmin] = useState(false);

  const authHeaders = () => ({
    headers: { Authorization: `Token ${localStorage.getItem("authToken")}` },
  });

  const readError = (err, fallback) => {
    const data = err.response?.data;
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (data.non_field_errors?.length) return data.non_field_errors[0];
    const keys = [
      "employee_id", "name", "role", "error", "detail",
      "password", "current_password", "new_password", "confirm_password",
    ];
    for (const key of keys) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === "string") return value;
    }
    return fallback;
  };

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const response = await axios.get(
        `${API_BASE_URL}/api/auth/users/`,
        authHeaders()
      );
      setUsers(response.data);
      const me = response.data.find(
        (u) => u.employee_id === currentUser.employee_id
      );
      setIsAdmin(Boolean(me?.is_admin));
      setError("");
    } catch (err) {
      setError(readError(err, "Could not load the user list."));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API_BASE_URL]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleAdd = async (e) => {
    e.preventDefault();
    setError("");
    setNotice("");
    try {
      await axios.post(
        `${API_BASE_URL}/api/auth/users/`,
        newUser,
        authHeaders()
      );
      setNotice(
        `${newUser.employee_id.trim().toUpperCase()} added. They set their own ` +
          `password the first time they sign in.`
      );
      setNewUser({ employee_id: "", name: "", role: "operator" });
      fetchUsers();
    } catch (err) {
      setError(readError(err, "Could not add that person."));
    }
  };

  const handleRoleChange = async (user, role) => {
    setSavingId(user.employee_id);
    setError("");
    setNotice("");
    try {
      await axios.patch(
        `${API_BASE_URL}/api/auth/users/${user.employee_id}/`,
        { role },
        authHeaders()
      );
      setNotice(`${user.employee_id} is now a ${role.replace("_", " ")}.`);
      fetchUsers();
    } catch (err) {
      setError(readError(err, "Could not change that role."));
    } finally {
      setSavingId(null);
    }
  };

  const handleReset = async (user) => {
    setSavingId(user.employee_id);
    setError("");
    setNotice("");
    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/auth/users/${user.employee_id}/reset-password/`,
        {},
        authHeaders()
      );
      setNotice(response.data.message);
      setConfirmReset(null);
      fetchUsers();
    } catch (err) {
      setError(readError(err, "Could not reset that password."));
    } finally {
      setSavingId(null);
    }
  };

  const handleRemove = async (user) => {
    setSavingId(user.employee_id);
    setError("");
    setNotice("");
    try {
      const response = await axios.delete(
        `${API_BASE_URL}/api/auth/users/${user.employee_id}/`,
        authHeaders()
      );
      setNotice(response.data.message);
      setConfirmRemove(null);
      fetchUsers();
    } catch (err) {
      setError(readError(err, "Could not remove that person."));
    } finally {
      setSavingId(null);
    }
  };

  const closePasswordFlow = () => {
    setPwStep(null);
    setPwCurrent("");
    setPwNew("");
    setPwConfirm("");
  };

  const verifyCurrentPassword = async (e) => {
    e.preventDefault();
    setPwBusy(true);
    setError("");
    try {
      await axios.post(
        `${API_BASE_URL}/api/auth/me/verify-password/`,
        { password: pwCurrent },
        authHeaders()
      );
      setPwStep("new");
    } catch (err) {
      setError(readError(err, "Could not check that password."));
    } finally {
      setPwBusy(false);
    }
  };

  const submitNewPassword = async (e) => {
    e.preventDefault();
    setPwBusy(true);
    setError("");
    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/auth/me/change-password/`,
        {
          current_password: pwCurrent,
          new_password: pwNew,
          confirm_password: pwConfirm,
        },
        authHeaders()
      );
      // The server revokes every old token and issues a fresh one, so the
      // stored token has to be swapped or the next request is rejected.
      if (response.data.token) {
        localStorage.setItem("authToken", response.data.token);
      }
      setNotice(response.data.message);
      closePasswordFlow();
    } catch (err) {
      setError(readError(err, "Could not change your password."));
    } finally {
      setPwBusy(false);
    }
  };

  const availableRoles = isAdmin
    ? ROLES
    : ROLES.filter((r) => r.value !== "manager");

  return (
    <div className="um-wrapper">
      {error && <div className="um-banner um-banner-error">{error}</div>}
      {notice && <div className="um-banner um-banner-ok">{notice}</div>}

      {pwStep && (
        <form
          className="um-password-panel"
          onSubmit={pwStep === "current" ? verifyCurrentPassword : submitNewPassword}
        >
          <div className="um-password-head">
            <strong>
              {pwStep === "current"
                ? "Confirm your current password"
                : "Choose a new password"}
            </strong>
            <button type="button" className="um-btn" onClick={closePasswordFlow}>
              Cancel
            </button>
          </div>

          {pwStep === "current" ? (
            <div className="um-password-row">
              <input
                className="um-input"
                type="password"
                placeholder="Current password"
                value={pwCurrent}
                onChange={(e) => setPwCurrent(e.target.value)}
                autoComplete="current-password"
                autoFocus
                required
              />
              <button type="submit" className="um-btn um-btn-primary" disabled={pwBusy}>
                Continue
              </button>
            </div>
          ) : (
            <div className="um-password-row">
              <input
                className="um-input"
                type="password"
                placeholder="New password"
                value={pwNew}
                onChange={(e) => setPwNew(e.target.value)}
                autoComplete="new-password"
                autoFocus
                required
              />
              <input
                className="um-input"
                type="password"
                placeholder="Confirm new password"
                value={pwConfirm}
                onChange={(e) => setPwConfirm(e.target.value)}
                autoComplete="new-password"
                required
              />
              <button type="submit" className="um-btn um-btn-primary" disabled={pwBusy}>
                Save
              </button>
            </div>
          )}
        </form>
      )}

      <form className="um-add-form" onSubmit={handleAdd}>
        <input
          className="um-input"
          placeholder="Employee ID"
          value={newUser.employee_id}
          onChange={(e) =>
            setNewUser({ ...newUser, employee_id: e.target.value })
          }
          required
        />
        <input
          className="um-input"
          placeholder="Full name"
          value={newUser.name}
          onChange={(e) => setNewUser({ ...newUser, name: e.target.value })}
          required
        />
        <select
          className="um-input"
          value={newUser.role}
          onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
        >
          {availableRoles.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </select>
        <button type="submit" className="um-btn um-btn-primary">
          Add Person
        </button>
      </form>

      {!isAdmin && (
        <p className="um-hint">
          Manager accounts and password resets are handled by the
          administrator.
        </p>
      )}

      {loading ? (
        <p className="um-hint">Loading…</p>
      ) : (
        <table className="um-table">
          <thead>
            <tr>
              <th>Employee ID</th>
              <th>Name</th>
              <th>Role</th>
              <th>Status</th>
              {isAdmin && <th>Password</th>}
              {isAdmin && <th>Access</th>}
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const isSelf = user.employee_id === currentUser.employee_id;
              const busy = savingId === user.employee_id;
              return (
                <tr key={user.employee_id}>
                  <td>
                    <strong>{user.employee_id}</strong>
                    {user.is_admin && <span className="um-tag">admin</span>}
                  </td>
                  <td>{user.name}</td>
                  <td>
                    <select
                      className="um-input um-input-sm"
                      value={user.role}
                      disabled={busy || isSelf || (user.is_admin && !isSelf)}
                      onChange={(e) => handleRoleChange(user, e.target.value)}
                    >
                      {ROLES.map((role) => (
                        <option
                          key={role.value}
                          value={role.value}
                          disabled={role.value === "manager" && !isAdmin}
                        >
                          {role.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <span
                      className="um-status"
                      style={STATUS_STYLE[user.status] || STATUS_STYLE.retired}
                    >
                      {user.status_display || user.status}
                    </span>
                  </td>
                  {isAdmin && (
                    <td>
                      {isSelf && user.is_admin ? (
                        <button
                          className="um-btn"
                          onClick={() => setPwStep("current")}
                          title="Change your own password"
                        >
                          Change
                        </button>
                      ) : isSelf || user.is_admin ? (
                        <span className="um-hint">—</span>
                      ) : confirmReset === user.employee_id ? (
                        <span className="um-confirm">
                          <button
                            className="um-btn um-btn-danger"
                            disabled={busy}
                            onClick={() => handleReset(user)}
                          >
                            Confirm reset
                          </button>
                          <button
                            className="um-btn"
                            onClick={() => setConfirmReset(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          className="um-btn"
                          disabled={busy || user.status === "pending"}
                          onClick={() => setConfirmReset(user.employee_id)}
                          title={
                            user.status === "pending"
                              ? "Already awaiting a new password"
                              : "Send this account back to awaiting-password"
                          }
                        >
                          Reset
                        </button>
                      )}
                    </td>
                  )}
                  {isAdmin && (
                    <td>
                      {isSelf || user.is_admin ? (
                        <span className="um-hint">—</span>
                      ) : confirmRemove === user.employee_id ? (
                        <span className="um-confirm">
                          <button
                            className="um-btn um-btn-danger"
                            disabled={busy}
                            onClick={() => handleRemove(user)}
                          >
                            Confirm
                          </button>
                          <button
                            className="um-btn"
                            onClick={() => setConfirmRemove(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          className="um-btn um-btn-quiet"
                          disabled={busy}
                          onClick={() => setConfirmRemove(user.employee_id)}
                          title="The employee has left. This is permanent — their record is kept only so past work still shows who did it."
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default UserManagement;
