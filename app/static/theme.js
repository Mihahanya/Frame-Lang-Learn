(() => {
  const select = document.getElementById('theme');
  const saved = localStorage.getItem('rll-theme') || 'default';
  document.body.dataset.theme = saved;
  if (select) {
    select.value = saved;
    select.onchange = () => {
      document.body.dataset.theme = select.value;
      localStorage.setItem('rll-theme', select.value);
    };
  }
})();
