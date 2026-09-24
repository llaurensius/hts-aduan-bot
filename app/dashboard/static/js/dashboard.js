/**
 * HTS Ticket Monitor - Dashboard Client-side Scripts
 */

// Toast Notifications System
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  
  let icon = '✓';
  if (type === 'error') icon = '✕';
  else if (type === 'warning') icon = '⚠';
  else if (type === 'info') icon = 'ℹ';

  toast.innerHTML = `
    <span style="font-weight: bold;">${icon}</span>
    <div style="flex: 1;">${message}</div>
  `;

  container.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  // Auto remove
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 300);
  }, 4000);
}

// Modal Helpers
function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('open');
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('open');
  }
}

// Close on overlay click
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('open');
  }
});

// Close on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach((m) => m.classList.remove('open'));
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Ticket Action Functions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send / Resend Telegram notification for a single ticket
 */
async function resendNotification(nomorAduan, btnElement) {
  if (!confirm(`Kirim notifikasi tiket ${nomorAduan} ke Telegram sekarang?`)) {
    return;
  }

  let originalHtml = '';
  if (btnElement) {
    originalHtml = btnElement.innerHTML;
    btnElement.innerHTML = '<span class="spinner"></span>';
    btnElement.disabled = true;
  }

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}/resend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        petugas_nama: getSavedPetugasNama(),
        petugas_role: getSavedPetugasRole(),
      }),
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'Gagal mengirim notifikasi.');
    }

    showToast(data.message || `Notifikasi ${nomorAduan} berhasil dikirim!`, 'success');
    if (data.audit_warning) {
      setTimeout(() => showToast(data.audit_warning, 'warning'), 1200);
    }
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    if (btnElement) {
      btnElement.innerHTML = originalHtml;
      btnElement.disabled = false;
    }
  }
}

/**
 * Open Ticket Detail & Timeline Modal
 */
async function openDetailModal(nomorAduan) {
  const bodyEl = document.getElementById('detail-modal-body');
  const titleEl = document.getElementById('detail-modal-title');
  if (!bodyEl) return;

  titleEl.textContent = `Detail Tiket: ${nomorAduan}`;
  bodyEl.innerHTML = `
    <div style="text-align: center; padding: 2rem;">
      <span class="spinner" style="width: 24px; height: 24px; border-width: 3px;"></span>
      <p style="margin-top: 0.5rem; color: var(--text-secondary);">Memuat detail tiket...</p>
    </div>
  `;
  openModal('detail-modal');

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Gagal memuat detail tiket.');
    }
    const data = await res.json();

    const isCompleted = data.is_completed === 1;
    const statusBadge = isCompleted
      ? `<span class="badge badge-completed">Sudah Ditangani</span>`
      : `<span class="badge badge-pending">Belum Ditangani</span>`;

    let html = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <div>
          <span style="font-size: 0.8rem; color: var(--text-muted);">STATUS SISTEM:</span><br>
          ${statusBadge}
        </div>
        <div style="text-align: right;">
          <span style="font-size: 0.8rem; color: var(--text-muted);">TANGGAL ADUAN:</span><br>
          <strong>${data.tanggal_aduan || '-'}</strong>
        </div>
      </div>

      <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 1rem; margin-bottom: 1.25rem;">
        <div class="form-row" style="margin-bottom: 0.65rem;">
          <div><span style="color: var(--text-muted); font-size: 0.75rem;">INSTANSI / OPD:</span><br><strong>${data.instansi || '-'}</strong></div>
          <div><span style="color: var(--text-muted); font-size: 0.75rem;">OPD INDUK:</span><br><strong>${data.opd_induk || '-'}</strong></div>
        </div>
        <div class="form-row" style="margin-bottom: 0.65rem;">
          <div><span style="color: var(--text-muted); font-size: 0.75rem;">KATEGORI:</span><br><span class="badge badge-category">${data.kategori || '-'}</span> / ${data.sub_kategori || '-'}</div>
          <div><span style="color: var(--text-muted); font-size: 0.75rem;">PIC PELAPOR:</span><br><strong>${data.pic_nama || '-'}</strong> (${data.pic_nomor || '-'})</div>
        </div>
        <div>
          <span style="color: var(--text-muted); font-size: 0.75rem;">KELUHAN:</span>
          <p style="margin-top: 0.25rem; font-size: 0.875rem; white-space: pre-wrap; color: var(--text-primary);">${data.keluhan || '-'}</p>
        </div>
      </div>

      <h4 style="font-size: 0.9rem; font-weight: 600; margin-bottom: 0.75rem;">Riwayat Perubahan & Notifikasi</h4>
    `;

    // Events timeline
    if (!data.events || data.events.length === 0) {
      html += `<p style="font-size: 0.8rem; color: var(--text-muted);">Belum ada riwayat aktivitas yang tercatat untuk tiket ini.</p>`;
    } else {
      html += `<div style="display: flex; flex-direction: column; gap: 0.5rem;">`;
      for (const ev of data.events) {
        let evBadge = 'badge-category';
        if (ev.event_type.includes('RESEND')) evBadge = 'badge-pending';
        else if (ev.event_type.includes('EDIT')) evBadge = 'badge-category';
        else if (ev.event_type.includes('REFETCH')) evBadge = 'badge-completed';

        let diffText = '';
        if (ev.changed_fields) {
          diffText = `<pre style="font-size: 0.75rem; margin-top: 0.35rem; background: rgba(0,0,0,0.3); padding: 0.4rem; border-radius: 4px; overflow-x: auto;">${JSON.stringify(ev.changed_fields, null, 2)}</pre>`;
        }

        html += `
          <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); padding: 0.65rem 0.85rem; border-radius: 6px; font-size: 0.825rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span class="badge ${evBadge}">${ev.event_type}</span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">${ev.event_timestamp || ev.created_at}</span>
            </div>
            ${diffText}
          </div>
        `;
      }
      html += `</div>`;
    }

    bodyEl.innerHTML = html;
  } catch (err) {
    bodyEl.innerHTML = `
      <div style="color: var(--danger); padding: 1rem; text-align: center;">
        ${err.message}
      </div>
    `;
  }
}

/**
 * Open Edit Modal
 */
async function openEditModal(nomorAduan) {
  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}`);
    if (!res.ok) throw new Error('Gagal mengambil data tiket.');
    const data = await res.json();

    document.getElementById('edit-nomor-aduan').value = data.nomor_aduan;
    document.getElementById('edit-kategori').value = data.kategori || '';
    document.getElementById('edit-sub-kategori').value = data.sub_kategori || '';
    document.getElementById('edit-instansi').value = data.instansi || '';
    document.getElementById('edit-opd-induk').value = data.opd_induk || '';
    document.getElementById('edit-pic-nama').value = data.pic_nama || '';
    document.getElementById('edit-pic-nomor').value = data.pic_nomor || '';
    document.getElementById('edit-keluhan').value = data.keluhan || '';
    document.getElementById('edit-status-display').value = data.status_display || '';
    document.getElementById('edit-is-submitted').value = data.is_submitted || 0;

    openModal('edit-modal');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

/**
 * Submit Edit Form
 */
async function submitEditTicket() {
  const nomorAduan = document.getElementById('edit-nomor-aduan').value;
  const btn = document.getElementById('btn-save-edit');
  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span> Menyimpan...';
  btn.disabled = true;

  const payload = {
    kategori: document.getElementById('edit-kategori').value,
    sub_kategori: document.getElementById('edit-sub-kategori').value,
    instansi: document.getElementById('edit-instansi').value,
    opd_induk: document.getElementById('edit-opd-induk').value,
    pic_nama: document.getElementById('edit-pic-nama').value,
    pic_nomor: document.getElementById('edit-pic-nomor').value,
    keluhan: document.getElementById('edit-keluhan').value,
    status_display: document.getElementById('edit-status-display').value,
    is_submitted: parseInt(document.getElementById('edit-is-submitted').value, 10),
  };

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Gagal menyimpan perubahan.');

    showToast(data.message || 'Tiket berhasil diperbarui!', 'success');
    closeModal('edit-modal');
    setTimeout(() => window.location.reload(), 800);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

/**
 * Open Refetch Modal
 */
function openRefetchModal(nomorAduan) {
  document.getElementById('refetch-nomor-aduan').value = nomorAduan;
  document.getElementById('refetch-target-text').textContent = nomorAduan;
  document.getElementById('refetch-send-notif').checked = false;
  openModal('refetch-modal');
}

/**
 * Submit Refetch Ticket
 */
async function submitRefetchTicket() {
  const nomorAduan = document.getElementById('refetch-nomor-aduan').value;
  const sendNotif = document.getElementById('refetch-send-notif').checked;
  const btn = document.getElementById('btn-confirm-refetch');
  const originalHtml = btn.innerHTML;

  btn.innerHTML = '<span class="spinner"></span> Mengambil dari API...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}/refetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ send_notification: sendNotif }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Gagal menarik ulang tiket dari HTS.');

    showToast(data.message || `Tiket ${nomorAduan} berhasil ditarik ulang!`, 'success');
    closeModal('refetch-modal');
    setTimeout(() => window.location.reload(), 1000);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

/**
 * Open Delete Modal
 */
function openDeleteModal(nomorAduan) {
  document.getElementById('delete-nomor-aduan').value = nomorAduan;
  document.getElementById('delete-target-text').textContent = nomorAduan;
  openModal('delete-modal');
}

/**
 * Submit Delete Ticket
 */
async function submitDeleteTicket() {
  const nomorAduan = document.getElementById('delete-nomor-aduan').value;
  const btn = document.getElementById('btn-confirm-delete');
  const originalHtml = btn.innerHTML;

  btn.innerHTML = '<span class="spinner"></span> Menghapus...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/tickets/${encodeURIComponent(nomorAduan)}`, {
      method: 'DELETE',
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Gagal menghapus tiket.');

    showToast(data.message || `Tiket ${nomorAduan} berhasil dihapus.`, 'success');
    closeModal('delete-modal');
    setTimeout(() => window.location.reload(), 800);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

/**
 * Open Bulk Resend Modal
 */
function openBulkResendModal() {
  const progressContainer = document.getElementById('bulk-progress-container');
  const logContainer = document.getElementById('bulk-log-container');
  const selectEl = document.getElementById('bulk-status-select');
  const btn = document.getElementById('btn-confirm-bulk');
  const cancelBtn = document.getElementById('btn-cancel-bulk');

  if (progressContainer) progressContainer.style.display = 'none';
  if (logContainer) logContainer.innerHTML = '';
  if (selectEl) selectEl.disabled = false;
  if (btn) {
    btn.innerHTML = 'Kirim Sekarang';
    btn.disabled = false;
    btn.onclick = submitBulkResend;
  }
  if (cancelBtn) {
    cancelBtn.style.display = 'inline-flex';
    cancelBtn.textContent = 'Batal';
  }

  openModal('bulk-resend-modal');
}

/**
 * Submit Bulk Resend with real-time streaming progress
 */
async function submitBulkResend() {
  const statusFilter = document.getElementById('bulk-status-select').value;
  const btn = document.getElementById('btn-confirm-bulk');
  const cancelBtn = document.getElementById('btn-cancel-bulk');
  const selectEl = document.getElementById('bulk-status-select');
  const progressContainer = document.getElementById('bulk-progress-container');
  const progressBar = document.getElementById('bulk-progress-bar');
  const progressLabel = document.getElementById('bulk-progress-label');
  const progressCounter = document.getElementById('bulk-progress-counter');
  const logContainer = document.getElementById('bulk-log-container');

  // Reset progress UI
  progressContainer.style.display = 'block';
  progressBar.style.width = '0%';
  progressBar.style.background = 'linear-gradient(90deg, #6366f1, #10b981)';
  progressLabel.textContent = 'Menghubungkan antrian...';
  progressCounter.textContent = '0 / 0 (0%)';
  logContainer.innerHTML = '';

  selectEl.disabled = true;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Mengirim...';
  if (cancelBtn) cancelBtn.style.display = 'none';

  try {
    const res = await fetch('/api/tickets/bulk-resend-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: statusFilter,
        petugas_nama: getSavedPetugasNama(),
        petugas_role: getSavedPetugasRole(),
      }),
    });

    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || 'Gagal memulai pengiriman massal.');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const data = JSON.parse(line);

          if (data.type === 'init') {
            const totalTickets = data.total;
            progressLabel.textContent = `Menyiapkan ${totalTickets} tiket...`;
            progressCounter.textContent = `0 / ${totalTickets} (0%)`;
            if (totalTickets === 0) {
              logContainer.innerHTML = `<div style="color: var(--text-muted);">Tidak ada tiket yang cocok untuk dikirim.</div>`;
            }
          } else if (data.type === 'progress') {
            const pct = Math.round((data.current / data.total) * 100);
            progressBar.style.width = `${pct}%`;
            progressLabel.textContent = `Mengirimkan notifikasi ke Telegram...`;
            progressCounter.textContent = `${data.current} / ${data.total} (${pct}%)`;

            const logItem = document.createElement('div');
            if (data.status === 'OK') {
              logItem.style.color = '#34d399';
              logItem.textContent = `✓ [OK] ${data.nomor_aduan}`;
            } else {
              logItem.style.color = '#f87171';
              logItem.textContent = `✕ [FAIL] ${data.nomor_aduan}: ${data.error || 'Error'}`;
            }
            logContainer.appendChild(logItem);
            logContainer.scrollTop = logContainer.scrollHeight;
          } else if (data.type === 'done') {
            progressBar.style.width = '100%';
            progressLabel.textContent = `✓ Pengiriman Selesai!`;
            progressCounter.textContent = `${data.sent} / ${data.total} Berhasil`;

            showToast(data.message || `Selesai! ${data.sent}/${data.total} notifikasi berhasil dikirim.`, 'success');
          }
        } catch (e) {
          console.error('Error parsing stream line:', e, line);
        }
      }
    }

    // Finished state
    btn.innerHTML = 'Tutup';
    btn.disabled = false;
    btn.onclick = () => {
      closeModal('bulk-resend-modal');
      openBulkResendModal();
    };
    if (cancelBtn) {
      cancelBtn.style.display = 'inline-flex';
      cancelBtn.textContent = 'Selesai';
    }

  } catch (err) {
    progressLabel.textContent = 'Gagal mengirim notifikasi.';
    progressBar.style.background = 'var(--danger)';
    showToast(err.message, 'error');

    btn.innerHTML = 'Coba Lagi';
    btn.disabled = false;
    btn.onclick = submitBulkResend;
    selectEl.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'inline-flex';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Signature & Profil Petugas
// ─────────────────────────────────────────────────────────────────────────────

function getSavedPetugasNama() {
  return localStorage.getItem('hts_petugas_nama') || localStorage.getItem('hts_rekap_petugas') || 'Laurensius Liquori';
}

function getSavedPetugasRole() {
  return localStorage.getItem('hts_petugas_role') || 'Helpdesk DC';
}

function updateNavProfileDisplay() {
  const navNameEl = document.getElementById('nav-petugas-name');
  if (navNameEl) {
    const nama = getSavedPetugasNama();
    navNameEl.textContent = nama.split(' ')[0] || nama;
  }
}

function openProfileModal() {
  const namaInput = document.getElementById('profile-nama');
  const roleInput = document.getElementById('profile-role');
  if (namaInput) namaInput.value = getSavedPetugasNama();
  if (roleInput) roleInput.value = getSavedPetugasRole();
  updateProfilePreview();
  openModal('modal-profile');
}

function updateProfilePreview() {
  const nama = (document.getElementById('profile-nama')?.value || '').trim() || getSavedPetugasNama();
  const role = (document.getElementById('profile-role')?.value || '').trim() || getSavedPetugasRole();
  const previewEl = document.getElementById('profile-preview');
  if (previewEl) {
    previewEl.textContent = `Terima kasih atas perhatian dan kerjasamanya.\n${nama}\n${role}`;
  }
}

function saveProfileSettings() {
  const nama = (document.getElementById('profile-nama')?.value || '').trim();
  const role = (document.getElementById('profile-role')?.value || '').trim();
  if (!nama) {
    showToast('Nama Petugas tidak boleh kosong.', 'warning');
    return;
  }
  localStorage.setItem('hts_petugas_nama', nama);
  localStorage.setItem('hts_rekap_petugas', nama);
  if (role) {
    localStorage.setItem('hts_petugas_role', role);
  }
  updateNavProfileDisplay();
  const rekapInput = document.getElementById('rekap-petugas');
  if (rekapInput) rekapInput.value = nama;
  closeModal('modal-profile');
  showToast('Signature petugas berhasil disimpan!', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Rekap Laporan Functions
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  updateNavProfileDisplay();

  const rekapTanggalEl = document.getElementById('rekap-tanggal');
  if (rekapTanggalEl) {
    const today = new Date();
    const dd = String(today.getDate()).padStart(2, '0');
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const yyyy = today.getFullYear();
    rekapTanggalEl.value = `${yyyy}-${mm}-${dd}`;
  }

  const rekapPetugasEl = document.getElementById('rekap-petugas');
  if (rekapPetugasEl) {
    rekapPetugasEl.value = getSavedPetugasNama();
    rekapPetugasEl.addEventListener('change', (e) => {
      const val = e.target.value.trim();
      localStorage.setItem('hts_rekap_petugas', val);
      localStorage.setItem('hts_petugas_nama', val);
      updateNavProfileDisplay();
    });
  }
});

async function generateRekap() {
  let tanggal = document.getElementById('rekap-tanggal').value;
  if (tanggal && tanggal.includes('-')) {
    const [yyyy, mm, dd] = tanggal.split('-');
    tanggal = `${dd}/${mm}/${yyyy}`;
  }
  const shift = document.getElementById('rekap-shift').value;
  const petugas = document.getElementById('rekap-petugas').value.trim();
  const btn = document.getElementById('btn-generate-rekap');
  const resultContainer = document.getElementById('rekap-result-container');
  const output = document.getElementById('rekap-output');

  if (!tanggal || !shift || !petugas) {
    showToast('Harap isi semua field (Tanggal, Shift, Nama Petugas).', 'warning');
    return;
  }

  localStorage.setItem('hts_rekap_petugas', petugas);

  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span> Generate...';
  btn.disabled = true;

  try {
    const res = await fetch('/api/rekap/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tanggal, shift, nama_petugas: petugas }),
    });
    
    const data = await res.json();
    
    if (!res.ok || !data.success) {
      throw new Error(data.message || data.detail || 'Gagal meng-generate rekap.');
    }

    output.value = data.rekap_text;
    resultContainer.style.display = 'block';
    showToast('Rekap berhasil digenerate!', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

function copyRekap() {
  const output = document.getElementById('rekap-output');
  output.select();
  document.execCommand('copy');
  showToast('Teks rekap disalin ke clipboard!', 'success');
}

