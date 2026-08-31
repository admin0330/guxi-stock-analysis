"use strict";

const form = document.getElementById("loginForm");
const button = document.getElementById("loginButton");
const errorBox = document.getElementById("loginError");

function targetAfterLogin() {
  const fallback = "/stock";
  const value = new URLSearchParams(location.search).get("next") || fallback;
  return value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login") ? value : fallback;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.textContent = "";
  const username = form.username.value.trim();
  const password = form.password.value;
  if (!username || !password) { errorBox.textContent = "请输入用户名和密码"; return; }
  button.disabled = true;
  button.classList.add("loading");
  button.textContent = "正在登录";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "登录失败，请稍后重试");
    const target = targetAfterLogin();
    if (typeof window.motionNavigate === "function") await window.motionNavigate(target, { replace: true });
    else location.replace(target);
  } catch (error) {
    errorBox.textContent = error.message || "登录失败，请稍后重试";
    form.password.select();
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
    button.textContent = "登录";
  }
});
