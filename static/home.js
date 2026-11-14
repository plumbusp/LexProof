// Home scroll story.
// - --lit (0..1): how far the night/day "lit" image has faded in, from scroll position.
// - --hero (1..0): hero tagline, cue and glow fade as the wordmark leaves.
// - The LexProof wordmark flies from the centre into the top-bar brand slot.
// Everything is derived from scrollY every frame (no stored animation state), so a theme
// switch at any point, even mid-flight, just re-colours what is already there.
(() => {
  const home = document.getElementById("view-home");
  const logo = document.getElementById("hero-logo");
  const slot = document.getElementById("hero-slot");
  const brand = document.querySelector(".brand-name");
  if (!home || !logo || !slot || !brand) return;

  const clamp = (x) => Math.min(1, Math.max(0, x));
  const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };

  let active = false, raf = 0, m = null;

  function measure() {
    const big = parseFloat(getComputedStyle(logo).fontSize);
    const small = parseFloat(getComputedStyle(brand).fontSize);
    // Start point is where the slot sits at the very top of the page, held fixed on screen,
    // so the wordmark glides on one straight path instead of scrolling away first.
    const a = slot.getBoundingClientRect();
    const w = logo.offsetWidth, h = logo.offsetHeight;
    m = { w, h, ratio: small / big, x0: a.left + (a.width - w) / 2, y0: a.top + scrollY + (a.height - h) / 2 };
  }

  function frame() {
    raf = 0;
    if (!active) return;
    if (!m) measure();
    const vh = innerHeight, y = scrollY;
    const max = Math.max(1, document.documentElement.scrollHeight - vh);
    home.style.setProperty("--lit", smooth(0.06, 0.9, y / max).toFixed(4));

    const raw = clamp(y / (vh * 0.55)), t = ease(raw);
    // the tagline and cue stay put under the wordmark while they fade, instead of scrolling into it
    home.style.setProperty("--hero", clamp(1 - raw * 2.2).toFixed(4));
    home.style.setProperty("--sy", `${Math.min(y, vh).toFixed(1)}px`);

    const b = brand.getBoundingClientRect(), { x0, y0 } = m;
    const x1 = b.left, y1 = b.top + (b.height - m.h * m.ratio) / 2;
    const s = 1 + (m.ratio - 1) * t;
    logo.style.transform = `translate3d(${(x0 + (x1 - x0) * t).toFixed(2)}px, ${(y0 + (y1 - y0) * t).toFixed(2)}px, 0) scale(${s.toFixed(5)})`;

    const landed = t >= 0.999;
    logo.style.visibility = landed ? "hidden" : "visible";
    brand.classList.toggle("brand-hidden", !landed);
  }

  const schedule = () => { if (!raf) raf = requestAnimationFrame(frame); };

  function setActive(on) {
    active = on;
    if (on) { m = null; schedule(); }
    else { brand.classList.remove("brand-hidden"); logo.style.visibility = "hidden"; }
  }

  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", () => { m = null; schedule(); });
  document.fonts?.ready.then(() => { m = null; schedule(); });
  document.addEventListener("viewchange", (e) => setActive(e.detail === "home"));
  setActive(!home.hidden);

  document.getElementById("hero-cue")?.addEventListener("click", () =>
    document.getElementById("home-problem").scrollIntoView({ behavior: "smooth" }));

  // Text rises in as each line enters the screen, and resets once it is below the fold again,
  // so scrolling back up replays it.
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) e.target.classList.add("in");
      else if (e.boundingClientRect.top > 0) e.target.classList.remove("in");
    }
  }, { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
  home.querySelectorAll(".reveal").forEach((el) => io.observe(el));
})();
