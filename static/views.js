// Top-bar navigation between the three views. The view lives in the URL hash
// (#home, #panel, #map) so reload and back/forward keep the place.
(() => {
  const VIEWS = ["home", "panel", "map"];

  function current() {
    const h = location.hash.slice(1);
    if (VIEWS.includes(h)) return h;
    // An old ?job= link means a finished check: open the panel, not the home page.
    return new URLSearchParams(location.search).has("job") ? "panel" : "home";
  }

  function show(view) {
    document.body.dataset.view = view;
    document.querySelectorAll("[data-view-section]").forEach((el) => { el.hidden = el.dataset.viewSection !== view; });
    document.querySelectorAll("[data-only]").forEach((el) => { el.hidden = el.dataset.only !== view; });
    document.querySelectorAll(".tab").forEach((el) => {
      const on = el.dataset.view === view;
      el.classList.toggle("active", on);
      if (on) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
    });
    document.dispatchEvent(new CustomEvent("viewchange", { detail: view }));
  }

  window.addEventListener("hashchange", () => { show(current()); window.scrollTo(0, 0); });
  show(current());
})();
