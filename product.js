/* Site Control browser utilities; loaded before script.js. Kept as a separate
 * asset to satisfy the live V48 transactional ZIP validator. */
window.SCUtils = Object.freeze({
  escape(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  },
  date(epoch) {
    const d = new Date(Number(epoch) * 1000);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  },
  async copy(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(String(value));
      return true;
    }
    const field = document.createElement('textarea');
    field.value = String(value);
    field.setAttribute('readonly', '');
    field.style.position = 'fixed'; field.style.opacity = '0';
    document.body.append(field); field.select();
    const ok = document.execCommand('copy'); field.remove();
    return ok;
  },
  confirmDanger(message) {
    return window.confirm(message);
  },
  fileSize(num) {
    if (num < 1024) return `${num} Б`;
    return `${Math.round(num/1024)} КБ`;
  },
  loadImage(file, limit = 900000) {
    if (!file || !['image/jpeg','image/png','image/webp'].includes(file.type)) {
      throw Error('Выберите PNG, JPG или WebP');
    }
    if (file.size > limit) {
      throw Error('Изображение должно быть меньше 900 КБ');
    }
    return file;
  }
});
