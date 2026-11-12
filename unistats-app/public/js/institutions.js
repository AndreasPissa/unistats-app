/* Only known institution identifiers become website links. */
export function institutionFor(metadata, value) {
  if (!value) return null;
  const institution = metadata?.institutions?.[value];
  if (institution) return institution;
  const department = metadata?.departments.find((d) =>
    d.programs.some((p) => p.uri === value),
  );
  // #BSc, #MSc and #PhD are RDF resources, not verified website anchors.
  return department ? (metadata.institutions?.[department.uri] ?? null) : null;
}

export function institutionText(metadata, value, text) {
  const institution = institutionFor(metadata, value);
  let url;
  try {
    url = institution ? new URL(institution.url) : null;
  } catch {
    url = null;
  }
  const isLink =
    url &&
    ["http:", "https:"].includes(url.protocol) &&
    !url.username &&
    !url.password;
  const node = document.createElement(isLink ? "a" : "span");
  node.textContent = text;
  if (isLink) {
    node.href = url.href;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    node.className = "institution-link";
    node.title = `Visit ${institution.label} website (opens in a new tab)`;
  }
  return node;
}
