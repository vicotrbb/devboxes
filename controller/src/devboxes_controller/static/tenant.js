const tenantSwitcher = document.querySelector("#tenant-switcher");

tenantSwitcher?.addEventListener("change", () => {
  const destination = new URL(window.location.href);
  destination.searchParams.set("tenant", tenantSwitcher.value);
  window.location.assign(destination);
});
