const tier4Base = 'https://seekreap-tier-4-orchestrator.onrender.com';
document.getElementById('videoForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    creatorId: document.getElementById('creatorId').value,
    videoUrl: document.getElementById('videoUrl').value,
    title: document.getElementById('title').value,
    usesThirdPartyMusic: document.getElementById('usesMusic').checked
  };
  try {
    const res = await fetch(`${tier4Base}/process-video`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    document.getElementById('result').innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>
      <a href="${tier4Base}/seekreap-tier4-db/${data.id}.pdf" target="_blank">Download PDF Report</a>`;
    loadHistory();
  } catch (err) {
    document.getElementById('result').innerHTML = `<span style="color:red;">Error: ${err.message}</span>`;
  }
});
async function loadHistory() {
  try {
    const res = await fetch(`${tier4Base}/videos`);
    const db = await res.json();
    const tbody = document.querySelector('#historyTable tbody');
    tbody.innerHTML = '';
    Object.values(db).forEach(v => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${v.id}</td><td>${v.title}</td><td>${v.creatorId}</td><td>${v.status}</td>
        <td><a href="${tier4Base}/seekreap-tier4-db/${v.id}.pdf" target="_blank">PDF</a></td>`;
      tbody.appendChild(tr);
    });
  } catch (err) { console.error('History load error:', err); }
}
loadHistory();
