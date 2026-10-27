'use strict';
const menuButton = document.querySelector('.menu-toggle');
const nav = document.querySelector('#site-nav');
menuButton?.addEventListener('click', () => {
  const expanded = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(expanded));
  nav?.classList.toggle('is-open', expanded);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') {
    menuButton.setAttribute('aria-expanded', 'false');
    nav?.classList.remove('is-open'); menuButton.focus();
  }
});
const search = document.querySelector('#publication-search');
const year = document.querySelector('#publication-year');
const kind = document.querySelector('#publication-kind');
function filterPublications() {
  const words = (search?.value || '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  let count = 0;
  document.querySelectorAll('[data-paper]').forEach((paper) => {
    const match = words.every(word => paper.dataset.search.includes(word)) &&
      (!year?.value || paper.dataset.year === year.value) && (!kind?.value || paper.dataset.kind === kind.value);
    paper.hidden = !match; if (match) count += 1;
  });
  document.querySelectorAll('[data-year-group]').forEach(group => {
    const visible = group.querySelectorAll('[data-paper]:not([hidden])').length;
    group.hidden = visible === 0;
    const yearCount = group.querySelector('.year-heading span');
    if (yearCount) yearCount.textContent = String(visible).padStart(2, '0');
  });
  const countLabel = document.querySelector('#publication-count');
  if (countLabel) countLabel.textContent = `${count} publication${count === 1 ? '' : 's'}`;
  const empty = document.querySelector('#no-publications'); if (empty) empty.hidden = count !== 0;
}
search?.addEventListener('input', filterPublications);
year?.addEventListener('change', filterPublications);
kind?.addEventListener('change', filterPublications);
document.querySelectorAll('.copy-citation').forEach(button => {
  button.addEventListener('click', async () => {
    const citation = button.parentElement.querySelector('pre').textContent;
    try {
      await navigator.clipboard.writeText(citation); button.textContent = 'Copied!';
      setTimeout(() => { button.textContent = 'Copy citation'; }, 2000);
    } catch {
      const range = document.createRange(); range.selectNodeContents(button.parentElement.querySelector('pre'));
      window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range);
      button.textContent = 'Selected — press Ctrl/Cmd+C';
    }
  });
});
const dialog = document.querySelector('#photo-dialog');
const photoLinks = [...document.querySelectorAll('[data-photo]')];
let photoIndex = 0, photoOpener;
function showPhoto(index) {
  photoIndex = (index + photoLinks.length) % photoLinks.length;
  const link = photoLinks[photoIndex], image = link.querySelector('img'), fullImage = document.querySelector('#dialog-image');
  fullImage.src = link.href; fullImage.alt = image.alt;
  document.querySelector('#dialog-caption').textContent = image.alt;
  document.querySelector('#photo-position').textContent = `${photoIndex + 1} / ${photoLinks.length}`;
}
photoLinks.forEach((link, index) => link.addEventListener('click', event => {
  if (!dialog?.showModal || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); photoOpener = link; showPhoto(index); dialog.showModal();
}));
document.querySelector('.dialog-close')?.addEventListener('click', () => dialog.close());
document.querySelector('#photo-prev')?.addEventListener('click', () => showPhoto(photoIndex - 1));
document.querySelector('#photo-next')?.addEventListener('click', () => showPhoto(photoIndex + 1));
dialog?.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
dialog?.addEventListener('close', () => photoOpener?.focus());
dialog?.addEventListener('keydown', event => {
  if (event.key === 'ArrowLeft') { event.preventDefault(); showPhoto(photoIndex - 1); }
  if (event.key === 'ArrowRight') { event.preventDefault(); showPhoto(photoIndex + 1); }
});

// The local editor rebuilds saved source files. Keep an open preview current.
// Published sites never request this local-only endpoint.
if (['127.0.0.1', 'localhost', '[::1]'].includes(window.location.hostname)) {
  let previewVersion = null;
  let previewSupported = true;
  let previewRequest = false;
  let previewNotice = null;
  async function refreshLocalPreview() {
    if (!previewSupported || previewRequest || document.hidden) return;
    previewRequest = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/_preview/status', { cache: 'no-store', signal: controller.signal });
      if (response.status === 404) { previewSupported = false; return; }
      if (!response.ok) return;
      const status = await response.json();
      if (typeof status.version !== 'string') return;
      if (status.has_error && !previewNotice) {
        previewNotice = document.createElement('aside');
        previewNotice.className = 'local-preview-notice';
        previewNotice.setAttribute('role', 'status');
        previewNotice.append('Your latest edit could not be applied. The previous version is shown. ');
        const editorLink = document.createElement('a');
        editorLink.href = '/_editor';
        editorLink.target = '_blank';
        editorLink.rel = 'noopener';
        editorLink.textContent = 'Open the editor to see the error.';
        previewNotice.append(editorLink);
        document.body.append(previewNotice);
      } else if (!status.has_error && previewNotice) {
        previewNotice.remove();
        previewNotice = null;
      }
      if (previewVersion !== null && status.version !== previewVersion && !status.has_error) {
        window.location.reload();
        return;
      }
      if (!status.has_error) previewVersion = status.version;
    } catch {
      // The editor may be restarting. Leave the current page usable.
    } finally {
      clearTimeout(timeout);
      previewRequest = false;
    }
  }
  void refreshLocalPreview();
  setInterval(() => { void refreshLocalPreview(); }, 2000);
  document.addEventListener('visibilitychange', () => { void refreshLocalPreview(); });
}
