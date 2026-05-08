document.getElementById('form').addEventListener('submit', async e => {
  e.preventDefault();
  const pw = document.getElementById('password').value;
  const body = new FormData();
  body.append('password', pw);
  const res = await fetch('/api/auth/login', { method: 'POST', body });
  if (res.ok) {
    window.location.href = '/';
  } else {
    document.getElementById('error').style.display = 'block';
    document.getElementById('password').value = '';
  }
});
