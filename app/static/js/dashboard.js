const toast = document.querySelector('#toast');
const binarySelect = document.querySelector('#binarySelect');
const templateSelect = document.querySelector('#templateSelect');
const linksEl = document.querySelector('#links');
let templates = [];

const notify = (text, ok = true) => {
  toast.textContent = text;
  toast.style.border = `1px solid ${ok ? '#3ed598' : '#ff6b8b'}`;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2200);
};

async function api(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || '请求失败');
  return data;
}

async function refreshBinaries() {
  const data = await api('/api/frpc/binaries');
  binarySelect.innerHTML = '';
  data.binaries.forEach((b) => {
    const opt = document.createElement('option');
    opt.value = b;
    opt.textContent = b;
    binarySelect.appendChild(opt);
  });
}

async function refreshTemplates() {
  const data = await api('/api/templates');
  templates = data.templates || [];
  templateSelect.innerHTML = '<option value="">选择模板</option>';
  templates.forEach((t) => {
    const opt = document.createElement('option');
    opt.value = t.name;
    opt.textContent = `${t.name} (${t.type})`;
    templateSelect.appendChild(opt);
  });
}

async function refreshLinks() {
  const data = await api('/api/links');
  linksEl.innerHTML = '';
  data.links.forEach((l) => {
    const item = document.createElement('div');
    item.className = 'link-item';
    item.innerHTML = `<div><b>${l.name}</b><div class="muted">${l.config_type} | ${l.frpc_binary}</div></div><button data-id="${l.id}">启动</button>`;
    item.querySelector('button').addEventListener('click', async () => {
      try {
        const r = await api('/api/launch', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({link_id: l.id}),
        });
        notify(r.message);
      } catch (e) {
        notify(e.message, false);
      }
    });
    linksEl.appendChild(item);
  });
}

function setupEvents() {
  document.querySelector('#themeToggle').addEventListener('click', () => {
    document.body.classList.toggle('theme-light');
  });

  document.querySelector('#logoutBtn').addEventListener('click', async () => {
    await api('/api/auth/logout', {method: 'POST'});
    location.href = '/login';
  });

  document.querySelector('#importBtn').addEventListener('click', async () => {
    try {
      const path = document.querySelector('#importPath').value.trim();
      const data = await api('/api/frpc/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path}),
      });
      notify(data.message);
      refreshBinaries();
    } catch (e) { notify(e.message, false); }
  });

  document.querySelector('#uploadBtn').addEventListener('click', async () => {
    try {
      const file = document.querySelector('#uploadFile').files[0];
      if (!file) throw new Error('请先选择文件');
      const buf = await file.arrayBuffer();
      const res = await fetch(`/api/frpc/upload?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST', body: buf,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      notify(data.message);
      refreshBinaries();
    } catch (e) { notify(e.message, false); }
  });

  document.querySelector('#downloadBtn').addEventListener('click', async () => {
    try {
      const body = {
        version: document.querySelector('#version').value.trim(),
        platform: document.querySelector('#platform').value,
        arch: document.querySelector('#arch').value,
      };
      const data = await api('/api/frpc/download', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      });
      notify(data.message);
      refreshBinaries();
    } catch (e) { notify(e.message, false); }
  });

  document.querySelector('#loadTemplateBtn').addEventListener('click', () => {
    const name = templateSelect.value;
    const t = templates.find((v) => v.name === name);
    if (!t) return;
    document.querySelector(`#configBody`).value = t.body;
    document.querySelector(`input[name=cfgType][value=${t.type}]`).checked = true;
  });

  document.querySelector('#createLinkBtn').addEventListener('click', async () => {
    try {
      const payload = {
        name: document.querySelector('#linkName').value.trim(),
        template_name: templateSelect.value || null,
        config_type: document.querySelector('input[name=cfgType]:checked').value,
        config_body: document.querySelector('#configBody').value,
        frpc_binary: binarySelect.value,
      };
      const data = await api('/api/links', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      notify(data.message);
      refreshLinks();
    } catch (e) { notify(e.message, false); }
  });
}

(async function init() {
  try {
    setupEvents();
    await Promise.all([refreshBinaries(), refreshTemplates(), refreshLinks()]);
  } catch (e) {
    notify(e.message, false);
    if (e.message.includes('登录')) location.href = '/login';
  }
})();
