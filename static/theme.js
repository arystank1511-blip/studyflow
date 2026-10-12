// Runs before first paint so a saved theme never flashes; the CSP forbids inline scripts.
(function () {
  try {
    var theme = localStorage.getItem('studyflow-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  } catch (error) { /* Storage can be blocked; the system theme still applies. */ }
})();
