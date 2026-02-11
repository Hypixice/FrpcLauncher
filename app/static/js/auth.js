const tabLogin = document.querySelector('#tabLogin');
const tabRegister = document.querySelector('#tabRegister');
const form = document.querySelector('#authForm');
const submitBtn = document.querySelector('#submitBtn');
const msg = document.querySelector('#authMsg');
let mode = 'login';

function swapMode(next) {
  mode = next;
  tabLogin.classList.toggle('active', mode === 'login');
  tabRegister.classList.toggle('active', mode === 'register');
  submitBtn.textContent = mode === 'login' ? '登录' : '注册';
  msg.textContent = '';
}

async function submitAuth(e) {
  e.preventDefault();
  const username = document.querySelector('#username').value.trim();
  const password = document.querySelector('#password').value;
  const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/register';
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json();
  msg.textContent = data.message || data.error || '未知错误';
  msg.style.color = res.ok ? '#3ed598' : '#ff6b8b';
  if (res.ok && mode === 'login') {
    setTimeout(() => (window.location.href = '/dashboard'), 400);
  }
}

tabLogin.addEventListener('click', () => swapMode('login'));
tabRegister.addEventListener('click', () => swapMode('register'));
form.addEventListener('submit', submitAuth);
