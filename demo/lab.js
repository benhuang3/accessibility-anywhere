// Ally groups repeated warnings by host. Separate loopback hosts keep the demo beats distinct.
if (location.hostname === "localhost" || location.hostname.endsWith(".localhost")) {
  const scenarios = new Set(["virus", "bank", "trap", "support", "shop", "news"]);
  for (const link of document.querySelectorAll("a[href]")) {
    const url = new URL(link.href);
    const name = url.pathname.split("/").pop().replace(/\.html$/, "");
    if (url.origin === location.origin && scenarios.has(name)) {
      url.hostname = `${name}.localhost`;
      link.href = url.href;
    }
  }
}
