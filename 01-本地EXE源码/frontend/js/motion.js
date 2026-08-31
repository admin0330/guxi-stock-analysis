(function () {
  "use strict";

  const interactiveSelector = "button, .btn, a.button, a.primary-button, a.secondary-button, a.danger-button, .interactive-item, input[type='button'], input[type='submit']";
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const dataTimers = new WeakMap();
  let activeTransition = null;
  let transitionTicket = 0;
  let sceneTicket = 0;
  let navigating = false;

  const elementOf = (target) => target instanceof Element ? target : target?.parentElement;
  const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function rippleTone(element) {
    const channels = getComputedStyle(element).backgroundColor.match(/[\d.]+/g)?.slice(0, 3).map(Number) || [255, 255, 255];
    return channels[0] * 0.299 + channels[1] * 0.587 + channels[2] * 0.114 > 170 ? "28, 25, 23" : "255, 255, 255";
  }

  function createRipple(host, clientX, clientY) {
    if (!(host instanceof Element) || reducedMotion.matches || host.matches(":disabled, [aria-disabled='true']")) return;
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const size = Math.hypot(rect.width, rect.height);
    const x = Number.isFinite(clientX) && clientX > 0 ? clientX - rect.left : rect.width / 2;
    const y = Number.isFinite(clientY) && clientY > 0 ? clientY - rect.top : rect.height / 2;
    const ripple = document.createElement("span");
    ripple.className = "ripple-effect";
    ripple.setAttribute("aria-hidden", "true");
    ripple.style.width = ripple.style.height = `${size}px`;
    ripple.style.left = `${Math.min(Math.max(x, 0), rect.width) - size / 2}px`;
    ripple.style.top = `${Math.min(Math.max(y, 0), rect.height) - size / 2}px`;
    ripple.style.setProperty("--ripple-rgb", rippleTone(host));
    host.classList.add("motion-ripple-host");
    if (getComputedStyle(host).position === "static") host.classList.add("motion-ripple-static");
    host.appendChild(ripple);

    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      ripple.remove();
      if (!host.querySelector(".ripple-effect")) host.classList.remove("motion-ripple-host", "motion-ripple-static");
    };
    ripple.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 620);
  }

  document.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    const origin = elementOf(event.target);
    const formControl = origin?.closest("input, textarea, select, [contenteditable='true']");
    if (formControl && !formControl.matches("input[type='button'], input[type='submit']")) return;
    const host = origin?.closest(interactiveSelector);
    if (host) createRipple(host, event.clientX, event.clientY);
  });

  document.addEventListener("keydown", (event) => {
    if (event.repeat || !["Enter", " "].includes(event.key)) return;
    const host = document.activeElement?.closest?.(interactiveSelector);
    if (!host || host.matches(":disabled, [aria-disabled='true']")) return;
    host.classList.add("motion-key-press");
    createRipple(host);
  });

  const clearKeyPress = () => document.querySelectorAll(".motion-key-press").forEach((element) => element.classList.remove("motion-key-press"));
  document.addEventListener("keyup", clearKeyPress);
  window.addEventListener("blur", clearKeyPress);

  function replayStagger(container) {
    if (!(container instanceof Element)) return;
    container.classList.remove("stagger-in");
    void container.offsetWidth;
    container.classList.add("stagger-in");
  }

  async function playClassAnimation(element, className, fallbackDuration) {
    if (!(element instanceof Element) || reducedMotion.matches) return;
    element.classList.remove(className);
    void element.offsetWidth;
    element.classList.add(className);
    try {
      await new Promise((resolve) => {
        let timer;
        const finish = (event) => {
          if (event && event.target !== element) return;
          clearTimeout(timer);
          element.removeEventListener("animationend", finish);
          resolve();
        };
        element.addEventListener("animationend", finish);
        timer = setTimeout(finish, fallbackDuration);
      });
    } finally {
      element.classList.remove(className);
    }
  }

  async function fallbackSceneTransition(current, next, update, ticket) {
    if (current instanceof Element && current !== next && getComputedStyle(current).display !== "none") {
      await playClassAnimation(current, "motion-scene-leave", 240);
    }
    if (ticket !== sceneTicket) return;
    const result = await update();
    if (ticket !== sceneTicket) return result;
    replayStagger(next);
    if (next instanceof Element && getComputedStyle(next).display !== "none") {
      await playClassAnimation(next, "motion-scene-enter", 340);
    }
    return result;
  }

  window.transitionViews = async function transitionViews(current, next, updateCallback, options = {}) {
    if (typeof updateCallback !== "function") return;
    const scene = ++sceneTicket;
    document.querySelectorAll(".motion-scene-leave, .motion-scene-enter").forEach((element) => element.classList.remove("motion-scene-leave", "motion-scene-enter"));
    if (reducedMotion.matches) {
      return updateCallback();
    }

    const useNative = options.native !== false && typeof document.startViewTransition === "function" && !activeTransition;
    if (!useNative) return fallbackSceneTransition(current, next, updateCallback, scene);

    const ticket = ++transitionTicket;
    document.documentElement.classList.add("motion-local-transition");
    let result;
    const transition = document.startViewTransition(async () => {
      if (scene !== sceneTicket) return;
      result = await updateCallback();
    });
    activeTransition = transition;
    try {
      await transition.finished;
    } catch (error) {
      if (error?.name !== "AbortError") throw error;
    } finally {
      if (ticket === transitionTicket) document.documentElement.classList.remove("motion-local-transition");
      if (activeTransition === transition) activeTransition = null;
    }
    return result;
  };

  window.smoothRender = async function smoothRender(container, updateCallback) {
    if (!(container instanceof Element) || typeof updateCallback !== "function") return updateCallback?.();
    const result = await updateCallback();
    replayStagger(container);
    if (reducedMotion.matches) return result;
    clearTimeout(dataTimers.get(container));
    container.classList.remove("motion-data-update");
    void container.offsetWidth;
    container.classList.add("motion-data-update");
    dataTimers.set(container, setTimeout(() => container.classList.remove("motion-data-update"), 360));
    return result;
  };

  window.renderWithTransition = window.smoothRender;

  window.highlightNode = function highlightNode(element) {
    if (!(element instanceof Element) || reducedMotion.matches) return;
    element.classList.remove("smooth-ws-update", "ws-flash");
    void element.offsetWidth;
    element.classList.add("smooth-ws-update");
    const cleanup = () => element.classList.remove("smooth-ws-update", "ws-flash");
    element.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 1100);
  };

  window.smoothInsertWSItem = function smoothInsertWSItem(parentContainer, newElement) {
    if (!(parentContainer instanceof Element) || !(newElement instanceof Element)) return;
    newElement.classList.add("smooth-ws-update");
    parentContainer.prepend(newElement);
    newElement.addEventListener("animationend", () => newElement.classList.remove("smooth-ws-update"), { once: true });
  };

  window.motionNavigate = async function motionNavigate(url, options = {}) {
    if (navigating) return;
    navigating = true;
    const target = new URL(url, location.href);
    if (!reducedMotion.matches && document.body) {
      document.body.classList.add("motion-leaving");
      try {
        await document.body.animate(
          [{ opacity: 1, transform: "translateY(0)" }, { opacity: 0, transform: "translateY(-6px)" }],
          { duration: 180, easing: "cubic-bezier(0.32, 0, 0.67, 0)", fill: "both" }
        ).finished;
      } catch (_) { /* 导航继续。 */ }
    }
    if (options.replace) location.replace(target.href);
    else location.assign(target.href);
  };

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = elementOf(event.target)?.closest("a[href]");
    if (!anchor || anchor.target || anchor.download || anchor.dataset.noMotion !== undefined) return;
    const target = new URL(anchor.href, location.href);
    if (target.origin !== location.origin || (target.pathname === location.pathname && target.search === location.search && target.hash)) return;
    event.preventDefault();
    window.motionNavigate(target.href);
  });

  function boot() {
    document.querySelectorAll("[data-auto-animate]").forEach((container) => container.classList.add("stagger-in"));
    requestAnimationFrame(() => document.body?.classList.add("motion-ready"));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
