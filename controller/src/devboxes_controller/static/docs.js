const statusRegion = document.querySelector("#docs-status");
const tocLinks = [...document.querySelectorAll(".docs-toc a[href^='#']")];

function cookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
}

function announce(message) {
  statusRegion.textContent = "";
  window.requestAnimationFrame(() => {
    statusRegion.textContent = message;
  });
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy-target]");
  if (!button) return;

  const target = document.getElementById(button.dataset.copyTarget);
  if (!target) return;

  const originalLabel = button.textContent;
  window.clearTimeout(button.copyResetTimer);
  try {
    await navigator.clipboard.writeText(target.textContent.trim());
    button.textContent = "Copied";
    announce("Commands copied to the clipboard.");
  } catch (_) {
    button.textContent = "Try again";
    announce("Clipboard access was denied. Select and copy the commands manually.");
  }
  button.copyResetTimer = window.setTimeout(() => {
    button.textContent = originalLabel;
  }, 2200);
});

document.querySelector("#logout-button")?.addEventListener("click", async () => {
  const csrf = decodeURIComponent(cookie("devboxes_csrf") || "");
  try {
    await fetch("/auth/logout", {
      method: "POST",
      headers: { "X-Devboxes-CSRF": csrf },
    });
  } finally {
    window.location.assign("/login");
  }
});

function markCurrentSection(id) {
  for (const link of tocLinks) {
    if (link.hash === `#${id}`) {
      link.setAttribute("aria-current", "location");
    } else {
      link.removeAttribute("aria-current");
    }
  }
}

const observedSections = tocLinks
  .map((link) => document.querySelector(link.hash))
  .filter(Boolean);

if (observedSections.length) {
  markCurrentSection(window.location.hash.slice(1) || observedSections[0].id);
  const sectionObserver = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      if (visible[0]) markCurrentSection(visible[0].target.id);
    },
    { rootMargin: "-18% 0px -72% 0px" },
  );
  observedSections.forEach((section) => sectionObserver.observe(section));
}
