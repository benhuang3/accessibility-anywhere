// Each Fakebook scene lives on its own *.localhost host so Ally's per-host warning cooldown stays separate.
// Links name their host in data-host; on any other host they keep working as plain relative links.
if (location.hostname === "localhost" || location.hostname.endsWith(".localhost")) {
  for (const link of document.querySelectorAll("a[data-host]")) {
    const url = new URL(link.href);
    url.hostname = `${link.dataset.host}.localhost`;
    link.href = url.href;
  }
}
