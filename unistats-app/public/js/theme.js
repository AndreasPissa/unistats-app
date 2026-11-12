// Run before styles paint. A stored preference is optional; dark is the default.
(() => {
  let theme = "dark";
  try {
    if (localStorage.getItem("unistats-theme") === "light") theme = "light";
  } catch {}
  function apply(value) {
    document.documentElement.dataset.theme = value;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", value === "light" ? "#f3f6f4" : "#101613");
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.textContent = value === "light" ? "Dark mode" : "Light mode";
      button.setAttribute(
        "aria-label",
        `Switch to ${value === "light" ? "dark" : "light"} mode`,
      );
    }
  }
  apply(theme);
  document.addEventListener("DOMContentLoaded", () => {
    apply(theme);
    document.getElementById("theme-toggle").addEventListener("click", () => {
      theme =
        document.documentElement.dataset.theme === "light" ? "dark" : "light";
      try {
        localStorage.setItem("unistats-theme", theme);
      } catch {}
      apply(theme);
      window.dispatchEvent(new Event("unistats:themechange"));
    });
  });
})();
