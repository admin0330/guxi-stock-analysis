"use strict";

let csrfToken = "";
let currentUser = null;
let resetTarget = null;
const rows = document.getElementById("userRows");
const toastBox = document.getElementById("toast");

function toast(message, error = false) {
  toastBox.textContent = message;
  toastBox.className = "toast show" + (error ? " error" : "");
  setTimeout(() => { toastBox.className = "toast"; }, 2600);
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
  if (response.status === 401) {
    const target = `/login?next=${encodeURIComponent(location.pathname)}`;
    if (typeof window.motionNavigate === "function") window.motionNavigate(target, { replace: true });
    else location.replace(target);
    throw new Error("登录已失效");
  }
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data?.detail || "操作失败，请稍后重试");
  return data;
}

const dateText = (value) => value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" }) : "从未登录";

function renderUsers(items, highlightUsername = "") {
  const html = items.length ? items.map((user) => {
    const self = user.id === currentUser.id;
    return `<tr data-user-id="${user.id}" data-username="${escapeHtml(user.username)}">
      <td><span class="user-name"><b>${escapeHtml(user.username)}</b><small>#${user.id}${self ? " · 当前账号" : ""}</small></span></td>
      <td><span class="badge ${user.role === "admin" ? "admin" : ""}">${user.role === "admin" ? "管理员" : "普通用户"}</span></td>
      <td><span class="badge ${user.enabled ? "" : "disabled"}">${user.enabled ? "已启用" : "已禁用"}</span></td>
      <td>${dateText(user.created_at)}</td><td>${dateText(user.last_login_at)}</td>
      <td><div class="row-actions"><button data-action="role" ${self ? "disabled" : ""}>${user.role === "admin" ? "设为普通用户" : "设为管理员"}</button><button data-action="toggle" ${self ? "disabled" : ""}>${user.enabled ? "禁用" : "启用"}</button><button data-action="reset">重置密码</button><button class="delete" data-action="delete" ${self ? "disabled" : ""}>删除</button></div></td>
    </tr>`;
  }).join("") : '<tr><td class="empty-state" colspan="6">暂无用户</td></tr>';
  const update = () => {
    rows.innerHTML = html;
    rows.querySelectorAll("button[data-action]").forEach((button) => button.addEventListener("click", () => handleAction(button)));
    if (highlightUsername) {
      const created = Array.from(rows.querySelectorAll("tr[data-username]")).find((row) => row.dataset.username === highlightUsername);
      window.highlightNode?.(created);
    }
  };
  if (typeof window.smoothRender === "function") window.smoothRender(rows, update);
  else update();
}

function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function loadUsers(highlightUsername = "") {
  const data = await api("/api/admin/users");
  const policy = data.password_policy;
  document.getElementById("passwordPolicy").textContent = `密码至少 ${policy.min_length} 位${policy.require_mixed ? "，并包含至少三类字符" : ""}`;
  renderUsers(data.items || [], highlightUsername);
}

async function handleAction(button) {
  const id = Number(button.closest("tr").dataset.userId);
  const data = await api("/api/admin/users");
  const user = data.items.find((item) => item.id === id);
  if (!user) return loadUsers();
  const action = button.dataset.action;
  try {
    if (action === "role") {
      const role = user.role === "admin" ? "user" : "admin";
      if (!confirm(`确认将“${user.username}”设为${role === "admin" ? "管理员" : "普通用户"}？`)) return;
      await api(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify({ role }) });
      toast("角色已更新");
    } else if (action === "toggle") {
      if (!confirm(`确认${user.enabled ? "禁用" : "启用"}“${user.username}”？${user.enabled ? "禁用后会立即退出其所有会话。" : ""}`)) return;
      await api(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: !user.enabled }) });
      toast(user.enabled ? "用户已禁用" : "用户已启用");
    } else if (action === "delete") {
      if (!confirm(`确认永久删除用户“${user.username}”？此操作不可撤销。`)) return;
      await api(`/api/admin/users/${id}`, { method: "DELETE" });
      toast("用户已删除");
    } else if (action === "reset") {
      resetTarget = user;
      document.getElementById("resetTitle").textContent = `重置“${user.username}”的密码`;
      document.getElementById("resetPassword").value = "";
      document.getElementById("resetDialog").showModal();
      return;
    }
    await loadUsers();
  } catch (error) { toast(error.message, true); }
}

document.getElementById("createUserForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const body = { username: form.username.value.trim(), password: form.password.value, role: form.role.value };
  if (!confirm(`确认创建${body.role === "admin" ? "管理员" : "普通用户"}“${body.username}”？`)) return;
  try { await api("/api/admin/users", { method: "POST", body: JSON.stringify(body) }); form.reset(); toast("用户已创建"); await loadUsers(body.username); }
  catch (error) { toast(error.message, true); }
});

document.getElementById("resetForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!resetTarget) return;
  const password = document.getElementById("resetPassword").value;
  if (!confirm(`确认重置“${resetTarget.username}”的密码并强制其下线？`)) return;
  try { await api(`/api/admin/users/${resetTarget.id}/reset-password`, { method: "POST", body: JSON.stringify({ password }) }); document.getElementById("resetDialog").close(); toast("密码已重置"); await loadUsers(); }
  catch (error) { toast(error.message, true); }
});

document.getElementById("logoutButton").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); }
  finally {
    if (typeof window.motionNavigate === "function") window.motionNavigate("/login", { replace: true });
    else location.replace("/login");
  }
});

(async () => {
  try {
    const me = await api("/api/auth/me");
    csrfToken = me.csrf_token;
    currentUser = me.user;
    if (currentUser.role !== "admin") {
      if (typeof window.motionNavigate === "function") window.motionNavigate("/", { replace: true });
      else location.replace("/");
      return;
    }
    document.getElementById("currentUser").textContent = `${currentUser.username} · 管理员`;
    await loadUsers();
  } catch (error) { toast(error.message, true); }
})();
