(function () {
  "use strict";

  const AUTO_ANIMATE_URL = "https://unpkg.com/@formkit/auto-animate@1.0.0-beta.6/index.min.js";
  const interactiveSelector = "button, .btn, a.button, .interactive-item, input[type='button'], input[type='submit']";
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const animatedContainers = new WeakSet();
  let activeTransition = null;

  const elementOf = (target) => target instanceof Element ? target : target?.parentElement;

  function rippleTone(element) {
    const channels = getComputedStyle(element).backgroundColor.match(/[\d.]+/g)?.slice(0, 3).map(Number) || [255, 255, 255];
    return channels[0] * .299 + channels[1] * .587 + channels[2] * .114 > 170 ? "28, 25, 23" : "255, 255, 255";
  }

  document.addEventListener("pointerdown", (event) => {
    if (reducedMotion.matches || event.button !== 0) return;
    const origin = elementOf(event.target);
    const formControl = origin?.closest("input, textarea, select, [contenteditable='true']");
    if (formControl && !formControl.matches("input[type='button'], input[type='submit']")) return;
    const host = origin?.closest(interactiveSelector);
    if (!host || host.matches(":disabled, [aria-disabled='true']")) return;

    const rect = host.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 1.5;
    const ripple = document.createElement("span");
    ripple.className = "ripple-effect";
    ripple.setAttribute("aria-hidden", "true");
    ripple.style.width = ripple.style.height = `${size}px`;
    ripple.style.left = `${Math.min(Math.max(event.clientX - rect.left, 0), rect.width) - size / 2}px`;
    ripple.style.top = `${Math.min(Math.max(event.clientY - rect.top, 0), rect.height) - size / 2}px`;
    ripple.style.setProperty("--ripple-rgb", rippleTone(host));
    host.classList.add("motion-ripple-host");
    host.appendChild(ripple);

    const cleanup = () => {
      ripple.remove();
      if (!host.querySelector(".ripple-effect")) host.classList.remove("motion-ripple-host");
    };
    ripple.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 560);
  });

  function replayStagger(container) {
    container.classList.remove("stagger-in");
    void container.offsetWidth;
    container.classList.add("stagger-in");
  }

  async function localCrossfade(container, update) {
    await container.animate(
      [{ opacity: 1, transform: "translateY(0)" }, { opacity: 0, transform: "translateY(-6px)" }],
      { duration: 140, easing: "cubic-bezier(0.32, 0, 0.67, 0)", fill: "both" }
    ).finished;
    const result = await update();
    await container.animate(
      [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }],
      { duration: 360, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "both" }
    ).finished;
    return result;
  }

  window.smoothRender = async function smoothRender(container, updateCallback) {
    if (!(container instanceof Element) || typeof updateCallback !== "function") return updateCallback?.();
    const update = async () => {
      const result = await updateCallback();
      replayStagger(container);
      return result;
    };
    if (reducedMotion.matches) return updateCallback();

    if (typeof document.startViewTransition !== "function" || activeTransition) return localCrossfade(container, update);

    const transition = document.startViewTransition(update);
    activeTransition = transition;
    try {
      await transition.finished;
    } catch (error) {
      if (error?.name !== "AbortError") throw error;
    } finally {
      if (activeTransition === transition) activeTransition = null;
    }
    return transition;
  };

  window.renderWithTransition = window.smoothRender;

  window.highlightNode = function highlightNode(element) {
    if (!(element instanceof Element) || reducedMotion.matches) return;
    element.classList.remove("smooth-ws-update", "ws-flash");
    void element.offsetWidth;
    element.classList.add("smooth-ws-update");
    const cleanup = () => element.classList.remove("smooth-ws-update", "ws-flash");
    element.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 1500);
  };

  window.smoothInsertWSItem = function smoothInsertWSItem(parentContainer, newElement) {
    if (!(parentContainer instanceof Element) || !(newElement instanceof Element)) return;
    newElement.classList.add("smooth-ws-update");
    parentContainer.prepend(newElement);
    newElement.addEventListener("animationend", () => newElement.classList.remove("smooth-ws-update"), { once: true });
  };

  function mountAutoAnimate(root = document) {
    root.querySelectorAll("[data-auto-animate]").forEach((container) => {
      container.classList.add("stagger-in");
      if (typeof window.autoAnimate !== "function" || animatedContainers.has(container)) return;
      window.autoAnimate(container, { duration: 360, easing: "cubic-bezier(0.16, 1, 0.3, 1)" });
      animatedContainers.add(container);
    });
  }

  function boot() {
    mountAutoAnimate();
    import(AUTO_ANIMATE_URL).then((module) => {
      window.autoAnimate = module.default;
      mountAutoAnimate();
    }).catch(() => { /* 核心动效不依赖 CDN。 */ });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
