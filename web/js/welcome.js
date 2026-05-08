// Welcome / splash page controller.
//
// Responsibilities:
//   1) Build the dual-row marquee from the backend sample images so the
//      page always has a "portfolio" feel even on a fresh install.
//   2) Persist a "seen welcome" flag so we don't show this every launch.
//   3) Hand off to the wizard when the user taps the big CTA.

const SEEN_KEY = "aphc.welcomeSeen";
const PARAM_FORCE = new URLSearchParams(location.search).get("force") === "1";

// ----------------------------------------------------------------------------
// Marquee data — the 8 environment azimuths plus 3 reference moodboards.
// We reuse the dev-mode endpoint that already renders these (deterministic),
// so the welcome screen works without bundling any photo assets.
// ----------------------------------------------------------------------------

const ENV_FRAMES = [
  { src: "/dev/sample-frame/0.jpg", tag: "Sunset", azimuth: 0   },
  { src: "/dev/sample-frame/1.jpg", tag: "Bench",  azimuth: 45  },
  { src: "/dev/sample-frame/2.jpg", tag: "Block",  azimuth: 90  },
  { src: "/dev/sample-frame/3.jpg", tag: "Trees",  azimuth: 135 },
  { src: "/dev/sample-frame/4.jpg", tag: "Fount.", azimuth: 180 },
  { src: "/dev/sample-frame/5.jpg", tag: "Statue", azimuth: 225 },
  { src: "/dev/sample-frame/6.jpg", tag: "Skyline",azimuth: 270 },
  { src: "/dev/sample-frame/7.jpg", tag: "Mixed",  azimuth: 315 },
];

const REF_POSTERS = [
  { src: "/dev/sample-reference/0.jpg", tag: "Moody",  size: "wide" },
  { src: "/dev/sample-reference/1.jpg", tag: "Bright", size: "wide" },
  { src: "/dev/sample-reference/2.jpg", tag: "Film",   size: "wide" },
];

function makePosterCard(item) {
  const card = document.createElement("div");
  card.className = "poster-card" + (item.size === "wide" ? " size-wide" : "");

  const img = document.createElement("img");
  img.src = item.src;
  img.alt = item.tag || "poster";
  img.loading = "eager";
  img.decoding = "async";
  card.appendChild(img);

  const tag = document.createElement("div");
  tag.className = "poster-tag";
  tag.textContent = item.tag;
  card.appendChild(tag);

  if (Number.isFinite(item.azimuth)) {
    const az = document.createElement("div");
    az.className = "poster-azimuth";
    az.textContent = `${String(item.azimuth).padStart(3, "0")}°`;
    card.appendChild(az);
  }

  return card;
}

function fillTrack(track, items) {
  if (!track) return;
  // Render each item twice so the @keyframes 0% -> -50% loop stays seamless.
  const sequence = [...items, ...items];
  for (const item of sequence) {
    track.appendChild(makePosterCard(item));
  }
}

// Stagger the two rows for a film-strip parallax feel.
fillTrack(document.querySelector(".marquee-track-a"), ENV_FRAMES);
fillTrack(
  document.querySelector(".marquee-track-b"),
  // Mix references in with a few env frames so row B has personality
  [...REF_POSTERS, ...ENV_FRAMES.slice(2, 7)],
);

// ----------------------------------------------------------------------------
// CTA handoff
// ----------------------------------------------------------------------------

const startBtn = document.getElementById("welcome-start");
if (startBtn) {
  startBtn.addEventListener("click", () => {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch {}
    location.href = "/web/";
  });
}

// If the user reloaded welcome with ?force=1 (e.g. from a future "About"
// link), don't auto-redirect even if they've seen it before.
if (!PARAM_FORCE) {
  // No automatic redirect — the welcome page should ALWAYS render even for
  // returning users if they explicitly land here. We only redirect from
  // /web/ -> welcome on first visit; that logic lives in index.js.
}
