// Runs before first paint so a saved theme never flashes; the CSP forbids inline scripts.
(function () {
  try {
    var theme = localStorage.getItem('studyflow-theme');
    if (theme === 'light' || theme === 'dark') {
      document.documentElement.dataset.theme = theme;
      // Match the phone's status bar to a manually chosen theme.
      document.querySelectorAll('meta[name="theme-color"]').forEach(meta => { meta.content = theme === 'dark' ? '#15171d' : '#f6f8fb'; });
    }
    // The opening animations play on the first visit of the day only, not after every tick.
    var day = new Date().toDateString();
    if (localStorage.getItem('studyflow-last-day') !== day) {
      document.documentElement.classList.add('fresh-day');
      localStorage.setItem('studyflow-last-day', day);
    }
  } catch (error) { /* Storage can be blocked; the system theme still applies. */ }
})();
