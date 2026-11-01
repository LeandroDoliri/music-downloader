const PREFIX = 'music_downloader_';

// Los valores tienen que coincidir con los que entiende main.py (build_*_command)
const FORMAT_OPTIONS = {
    mp3: [['mp3', 'MP3'], ['m4a', 'M4A (AAC)'], ['opus', 'OPUS (original)'], ['flac', 'FLAC'], ['wav', 'WAV']],
    mp4: [['mp4', 'MP4 (compatible)'], ['mkv', 'MKV (máx. calidad)'], ['webm', 'WEBM']],
};
const AUDIO_QUALITY = [['best', 'Máxima'], ['320k', '320 kbps'], ['256k', '256 kbps'], ['192k', '192 kbps'], ['128k', '128 kbps']];
const VIDEO_QUALITY = [['best', 'Máxima'], ['2160', '4K (2160p)'], ['1440', '1440p'], ['1080', '1080p'], ['720', '720p'], ['480', '480p'], ['360', '360p']];
// YouTube no ofrece H.264 por encima de 1080p, así que MP4 no llega a 1440p/4K
const MP4_MAX = ['best', '1080', '720', '480', '360'];
const LOSSLESS = ['flac', 'wav'];

// Preferencias del usuario. `format` y `metadata` guardan lo que eligió el usuario,
// aunque la UI muestre otra cosa temporalmente (p. ej. con enlaces de Spotify).
const state = {
    folder: '',
    format: 'mp3',
    metadata: true,
    downloading: false,
    env: null,        // resultado de check_environment()
    spotdlTotal: 0,   // canciones de la lista de Spotify ("Found N songs")
    spotdlDone: 0,
    skipped: 0,       // elementos omitidos porque ya los tenía
    newFiles: 0,      // elementos descargados de verdad en esta ejecución
};

// Elementos UI
const $ = (id) => document.getElementById(id);
const urlInput = $('url');
const sourceBadge = $('source-badge');
const formatGroup = $('format');
const formatButtons = [...formatGroup.querySelectorAll('button')];
const formatHint = $('format-hint');
const btnFolder = $('btn-folder');
const advFormatSelect = $('adv-format');
const advQualitySelect = $('adv-quality');
const advHint = $('adv-hint');
const chkPlaylist = $('chk-playlist');
const lblPlaylist = $('lbl-playlist');
const chkMetadata = $('chk-metadata');
const metadataNote = $('metadata-note');
const chkSubs = $('chk-subs');
const lblSubs = $('lbl-subs');
const chkSponsor = $('chk-sponsor');
const lblSponsor = $('lbl-sponsor');
const chkSkip = $('chk-skip');
const lblSkip = $('lbl-skip');
const btnAdvanced = $('btn-advanced');
const advancedPanel = $('advanced-panel');
const btnDownload = $('btn-download');
const btnCancel = $('btn-cancel');
const statusBox = $('status');
const statusText = $('status-text');
const statusPercent = $('status-percent');
const progressBar = $('progress-bar');
const consoleDiv = $('console');
const logDetails = document.querySelector('.log');
const envBanner = $('env-banner');
const btnUpdate = $('btn-update');
const btnCheck = $('btn-check');
const accountState = $('account-state');
const accountBrowser = $('account-browser');
const btnAccount = $('btn-account');
const chkAccount = $('chk-account');
const lblAccount = $('lbl-account');
const accountHelp = $('account-help');

function load(key) {
    return localStorage.getItem(PREFIX + key);
}

function save(key, value) {
    localStorage.setItem(PREFIX + key, value);
}

// --- Origen del enlace ---

function getSource(url) {
    if (/spotify\.com|^spotify:/i.test(url)) return 'spotify';
    if (/youtube\.com|youtu\.be/i.test(url)) return 'youtube';
    return url.trim() ? 'other' : null;
}

function effectiveFormat() {
    return getSource(urlInput.value) === 'spotify' ? 'mp3' : state.format;
}

// --- Actualización de la UI ---

function fillSelect(select, options) {
    select.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
}

function restoreSelect(select, value) {
    if (value && [...select.options].some(opt => opt.value === value)) {
        select.value = value;
    }
}

// Regenera los <select> avanzados para el tipo actual. Formato y calidad se guardan
// por tipo (audio/video) para no perder la elección al cambiar entre ambos.
function updateAdvancedOptions() {
    const mode = effectiveFormat();
    fillSelect(advFormatSelect, FORMAT_OPTIONS[mode]);
    restoreSelect(advFormatSelect, load(`advFormat_${mode}`) ?? load('advFormat'));
    updateQualityOptions(load(`advQuality_${mode}`) ?? load('advQuality'));
}

// Las calidades posibles dependen del formato (MP4 llega hasta 1080p)
function updateQualityOptions(preferred = advQualitySelect.value) {
    const mode = effectiveFormat();
    const options = mode === 'mp3'
        ? AUDIO_QUALITY
        : VIDEO_QUALITY.filter(([v]) => advFormatSelect.value !== 'mp4' || MP4_MAX.includes(v));
    fillSelect(advQualitySelect, options);
    restoreSelect(advQualitySelect, preferred);
    // En formatos sin pérdida el bitrate no aplica
    advQualitySelect.disabled = state.downloading || LOSSLESS.includes(advFormatSelect.value);
    updateHints();
}

function updateHints() {
    const fmt = advFormatSelect.value;
    const quality = advQualitySelect.value;
    let hint = '';
    if (LOSSLESS.includes(fmt)) {
        hint = 'El audio de origen ya viene comprimido: FLAC/WAV no mejoran el sonido, solo ocupan más. Útil para editar.';
        if (fmt === 'wav') hint += ' WAV no admite portada.';
    } else if (effectiveFormat() === 'mp3' && quality === 'best' && ['m4a', 'opus'].includes(fmt)) {
        hint = 'Se guarda el audio original, sin recodificar.';
    } else if (fmt === 'mp4') {
        hint = 'MP4 usa H.264 para que abra en cualquier reproductor (hasta 1080p). Para 1440p o 4K elige MKV.';
    } else if (fmt === 'webm') {
        hint = 'WEBM no admite portada incrustada.';
    }
    advHint.textContent = hint;
    advHint.hidden = !hint;
}

function updateSourceUI() {
    const source = getSource(urlInput.value);
    const isSpotify = source === 'spotify';

    sourceBadge.className = 'source-badge';
    if (source) {
        sourceBadge.classList.add('visible', source);
        sourceBadge.textContent = { spotify: 'Spotify', youtube: 'YouTube', other: 'Otro sitio' }[source];
    }

    // spotDL no descarga video
    const shownFormat = effectiveFormat();
    formatButtons.forEach(btn => {
        btn.setAttribute('aria-checked', String(btn.dataset.value === shownFormat));
        btn.disabled = state.downloading || (isSpotify && btn.dataset.value === 'mp4');
    });
    formatHint.hidden = !(isSpotify && state.format === 'mp4');

    // spotDL siempre incrusta metadatos
    chkMetadata.disabled = isSpotify;
    chkMetadata.checked = isSpotify || state.metadata;
    metadataNote.hidden = !isSpotify;

    // spotDL siempre baja la lista entera; la casilla no aplica
    lblPlaylist.hidden = isSpotify;
    // Subtítulos solo tienen sentido en video de YouTube
    lblSubs.hidden = !(source === 'youtube' && shownFormat === 'mp4');
    // SponsorBlock solo tiene datos de videos de YouTube (spotDL también baja de YouTube)
    lblSponsor.hidden = !(source === 'youtube' || isSpotify);
    // spotDL ya omite por su cuenta las canciones que existen en la carpeta destino
    lblSkip.hidden = isSpotify;

    if (advFormatSelect.dataset.mode !== shownFormat) {
        advFormatSelect.dataset.mode = shownFormat;
        updateAdvancedOptions();
    }
}

function renderFolder() {
    const path = $('folder-path');
    btnFolder.classList.toggle('selected', !!state.folder);
    btnFolder.title = state.folder;
    // El span usa direction: rtl para recortar por la izquierda; el marcador LRM
    // evita que caracteres como "\" o ":" se reordenen al final.
    path.textContent = state.folder ? '‎' + state.folder + '‎' : 'Ninguna carpeta seleccionada';
    $('folder-action').textContent = state.folder ? 'Cambiar' : 'Elegir';
}

// --- Preferencias ---

function loadPreferences() {
    state.folder = load('folder') || '';
    state.format = load('format') === 'mp4' ? 'mp4' : 'mp3';
    state.metadata = load('metadata') !== 'false'; // por defecto true
    chkPlaylist.checked = load('playlist') === 'true';
    chkSubs.checked = load('subs') === 'true';
    chkSponsor.checked = load('sponsorblock') === 'true';
    chkSkip.checked = load('skipExisting') !== 'false'; // por defecto true
    chkAccount.checked = load('useAccount') !== 'false'; // por defecto true al conectar
    renderFolder();
    updateSourceUI();
}

function savePreferences() {
    const mode = effectiveFormat();
    save('folder', state.folder);
    save('format', state.format);
    save(`advFormat_${mode}`, advFormatSelect.value);
    save(`advQuality_${mode}`, advQualitySelect.value);
    save('playlist', chkPlaylist.checked);
    save('metadata', state.metadata);
    save('subs', chkSubs.checked);
    save('sponsorblock', chkSponsor.checked);
    save('skipExisting', chkSkip.checked);
    save('useAccount', chkAccount.checked);
}

// --- Dependencias y actualizaciones ---

async function checkEnvironment() {
    if (!window.pywebview?.api) return;
    btnCheck.disabled = true;
    $('versions').textContent = 'Comprobando versiones…';
    try {
        state.env = await window.pywebview.api.check_environment();
        renderEnvironment();
    } catch (err) {
        $('versions').textContent = 'No se pudieron comprobar las versiones';
        console.error(err);
    } finally {
        btnCheck.disabled = false;
    }
}

function renderEnvironment() {
    const env = state.env;
    const names = { 'yt-dlp': 'yt-dlp', 'spotdl': 'spotDL' };
    $('versions').textContent = env.packages
        .map(p => `${names[p.name]} ${p.installed ?? 'no instalado'}`)
        .join(' · ');

    const missing = env.packages.filter(p => !p.installed);
    const outdated = env.packages.filter(p => p.outdated);
    let title = '', detail = '', kind = 'warning', canUpdate = false;

    if (!env.ffmpeg) {
        title = 'Falta ffmpeg';
        detail = 'Sin ffmpeg no se puede convertir ni unir audio y video. Instálalo con: winget install ffmpeg';
        kind = 'error';
    } else if (missing.length) {
        title = `Falta instalar ${missing.map(p => names[p.name]).join(' y ')}`;
        detail = 'Pulsa Instalar o ejecuta: pip install -r requirements.txt';
        kind = 'error';
        canUpdate = true;
    } else if (outdated.length) {
        title = 'Hay actualizaciones disponibles';
        detail = outdated.map(p => `${names[p.name]} ${p.installed} → ${p.latest}`).join(', ')
            + '. YouTube y Spotify cambian seguido: con versiones viejas las descargas pueden fallar.';
        canUpdate = true;
    } else if (!env.deno) {
        title = 'Falta deno';
        detail = 'yt-dlp lo necesita para ver todos los formatos de YouTube. Instálalo con: winget install DenoLand.Deno';
    } else if (env.packages.some(p => p.blocked)) {
        const p = env.packages.find(p => p.blocked);
        title = `${names[p.name]} ${p.latest} disponible`;
        detail = 'spotDL todavía no es compatible con esa versión; se actualizará cuando spotDL lo permita.';
        kind = 'info';
    }

    envBanner.hidden = !title;
    envBanner.className = `banner ${kind}`;
    $('env-title').textContent = title;
    $('env-detail').textContent = detail;
    btnUpdate.hidden = !canUpdate;
    btnUpdate.querySelector('span').textContent = missing.length ? 'Instalar' : 'Actualizar';
}

function hasPendingUpdates() {
    return state.env?.packages.some(p => p.outdated || !p.installed);
}

btnUpdate.addEventListener('click', async () => {
    if (state.downloading || !window.pywebview?.api) return;
    setDownloading(true, "Actualizando herramientas…");
    btnCancel.hidden = true;
    btnDownload.hidden = false;
    btnDownload.disabled = true;
    btnUpdate.disabled = true;
    btnUpdate.querySelector('.spinner').hidden = false;
    logDetails.open = true;

    const ok = await window.pywebview.api.update_tools();

    btnUpdate.disabled = false;
    btnUpdate.querySelector('.spinner').hidden = true;
    btnDownload.disabled = false;
    setDownloading(false);
    finishStatus(ok ? 'success' : 'error',
        ok ? "Herramientas actualizadas" : "No se pudo actualizar");
    await checkEnvironment();
});

btnCheck.addEventListener('click', checkEnvironment);

// --- Eventos ---

btnAdvanced.addEventListener('click', () => {
    const expanded = advancedPanel.classList.toggle('expanded');
    btnAdvanced.setAttribute('aria-expanded', String(expanded));
});

formatButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        state.format = btn.dataset.value;
        updateSourceUI();
        savePreferences();
    });
});

urlInput.addEventListener('input', updateSourceUI);

urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !state.downloading) btnDownload.click();
});

advFormatSelect.addEventListener('change', () => {
    updateQualityOptions();
    savePreferences();
});
advQualitySelect.addEventListener('change', () => {
    updateHints();
    savePreferences();
});
chkPlaylist.addEventListener('change', savePreferences);
chkSubs.addEventListener('change', savePreferences);
chkSponsor.addEventListener('change', savePreferences);
chkSkip.addEventListener('change', savePreferences);
chkAccount.addEventListener('change', savePreferences);

// --- Cuenta de YouTube ---

let accountConnected = false;

async function loadAccount() {
    if (!window.pywebview?.api) return;
    renderAccount(await window.pywebview.api.account_status());
}

function renderAccount(status, error = '') {
    accountConnected = status.connected;
    accountState.classList.toggle('error', !!error);
    if (error) {
        accountState.textContent = error;
    } else if (status.connected) {
        accountState.textContent = `Conectada desde ${status.browser ?? 'el navegador'}${status.date ? ` (${status.date})` : ''}`;
    } else {
        accountState.textContent = status.browsers.length ? 'No conectada' : 'No se encontró ningún navegador compatible';
    }

    if (!status.connected) {
        const previous = accountBrowser.value || load('accountBrowser');
        fillSelect(accountBrowser, status.browsers);
        restoreSelect(accountBrowser, previous);
    }
    accountBrowser.hidden = status.connected || !status.browsers.length;
    btnAccount.hidden = !status.connected && !status.browsers.length;
    btnAccount.textContent = status.connected ? 'Desconectar' : 'Conectar';
    btnAccount.classList.toggle('danger', status.connected);
    lblAccount.hidden = !status.connected;
    accountHelp.hidden = status.connected && !error;
}

btnAccount.addEventListener('click', async () => {
    const api = window.pywebview?.api;
    if (!api || state.downloading) return;
    btnAccount.disabled = true;
    try {
        if (accountConnected) {
            await api.disconnect_account();
            renderAccount(await api.account_status());
        } else {
            save('accountBrowser', accountBrowser.value);
            btnAccount.textContent = 'Conectando…';
            const result = await api.connect_account(accountBrowser.value);
            if (result.ok) {
                chkAccount.checked = true;
                savePreferences();
            }
            renderAccount(await api.account_status(), result.ok ? '' : result.error);
        }
    } finally {
        btnAccount.disabled = false;
    }
});
chkMetadata.addEventListener('change', () => {
    if (!chkMetadata.disabled) state.metadata = chkMetadata.checked;
    savePreferences();
});

btnFolder.addEventListener('click', () => {
    if (!window.pywebview?.api) return;
    btnFolder.disabled = true;
    window.pywebview.api.choose_directory().then(folder => {
        if (folder) {
            state.folder = folder;
            renderFolder();
            savePreferences();
        }
    }).catch(err => {
        console.error("Error al seleccionar carpeta:", err);
    }).finally(() => {
        btnFolder.disabled = false;
    });
});

btnDownload.addEventListener('click', () => {
    const url = urlInput.value.trim();

    if (!url) {
        urlInput.focus();
        alert("Por favor ingresa un enlace válido.");
        return;
    }
    if (!state.folder) {
        alert("Por favor selecciona una carpeta de destino.");
        return;
    }
    if (!window.pywebview?.api) return;

    savePreferences();

    const tool = getSource(url) === 'spotify' ? 'spotdl' : 'yt-dlp';
    const options = {
        url: url,
        folder: state.folder,
        mainFormat: effectiveFormat(),
        advFormat: advFormatSelect.value,
        quality: advQualitySelect.value,
        playlist: !lblPlaylist.hidden && chkPlaylist.checked,
        metadata: chkMetadata.checked,
        subs: !lblSubs.hidden && chkSubs.checked,
        sponsorblock: !lblSponsor.hidden && chkSponsor.checked,
        skipExisting: !lblSkip.hidden && chkSkip.checked,
        useAccount: accountConnected && chkAccount.checked,
    };

    setDownloading(true);
    window.pywebview.api.download(tool, options);
});

btnCancel.addEventListener('click', () => {
    btnCancel.disabled = true;
    btnCancel.querySelector('span').textContent = "Cancelando…";
    window.pywebview?.api.cancel();
});

// --- Estado de descarga ---

function setDownloading(active, initialStatus = "Preparando…") {
    state.downloading = active;
    btnDownload.hidden = active;
    btnCancel.hidden = !active;
    btnCancel.disabled = false;
    btnCancel.querySelector('span').textContent = "Cancelar";
    [urlInput, btnFolder, advFormatSelect, chkPlaylist, chkSubs, chkSponsor, chkSkip, btnUpdate, btnCheck,
        chkAccount, btnAccount, accountBrowser].forEach(el => el.disabled = active);
    advQualitySelect.disabled = active || LOSSLESS.includes(advFormatSelect.value);
    chkMetadata.disabled = active || getSource(urlInput.value) === 'spotify';
    formatButtons.forEach(btn => btn.disabled = active);

    if (active) {
        state.spotdlTotal = 0;
        state.spotdlDone = 0;
        state.skipped = 0;
        state.newFiles = 0;
        statusBox.hidden = false;
        statusBox.className = 'status';
        consoleDiv.innerHTML = '';
        const explain = document.getElementById('status-explain');
        if (explain) explain.hidden = true;
        setStatus(initialStatus);
        setProgress(null);
    } else {
        updateSourceUI();
    }
}

function setStatus(text) {
    statusText.textContent = text;
    statusText.title = text;
}

// null = progreso indeterminado
function setProgress(percent) {
    progressBar.classList.toggle('indeterminate', percent === null);
    progressBar.style.width = percent === null ? '' : percent + '%';
    statusPercent.textContent = percent === null ? '' : Math.round(percent) + '%';
}

// Traduce líneas conocidas de yt-dlp / spotDL a un texto de estado legible
function updateStatusFromLog(message) {
    let m;
    if ((m = message.match(/Downloading (?:item|video) (\d+) of (\d+)/i))) {
        setStatus(`Descargando elemento ${m[1]} de ${m[2]}…`);
    } else if ((m = message.match(/\[download\] Destination: (.+)$/))) {
        state.newFiles++;
        setStatus(`Descargando: ${m[1].split(/[\\/]/).pop()}`);
    } else if ((m = message.match(/^Ya lo tienes: (.+?)(?: → .*)?$/))) {
        // main.py lo escribe cuando omite algo que está en el historial o que ya existe en la carpeta
        state.skipped++;
        setStatus(`Ya lo tenías: ${m[1]}`);
    } else if (/^Reintentando /.test(message)) {
        // Lo que explicaba el fallo del primer intento ya no aplica
        const explain = document.getElementById('status-explain');
        if (explain) explain.hidden = true;
        setStatus(message);
    } else if (/^\[(ExtractAudio|FFmpegExtractAudio)\]/.test(message)) {
        setStatus("Convirtiendo audio…");
    } else if (/^\[Merger\]/.test(message)) {
        setStatus("Uniendo audio y video…");
    } else if (/^\[SponsorBlock\]|^\[ModifyChapters\]/.test(message)) {
        setStatus("Quitando partes sin música…");
    } else if (/^\[(EmbedThumbnail|Metadata|EmbedSubtitle|ThumbnailsConvertor)\]/.test(message)) {
        setStatus("Incrustando metadatos…");
    } else if ((m = message.match(/^Found (\d+) songs? in (.+)$/))) {
        state.spotdlTotal = parseInt(m[1], 10);
        setStatus(`${m[1]} canciones en ${m[2]}`);
    } else if ((m = message.match(/^(?:Downloaded|Skipping) "(.+?)"/))) {
        // spotDL no informa porcentajes: el progreso es canciones hechas / total
        state.spotdlDone++;
        const skipped = message.startsWith('Skipping');
        if (skipped) state.skipped++; else state.newFiles++;
        const prefix = skipped ? 'Ya existía' : 'Descargado';
        if (state.spotdlTotal > 1) {
            setStatus(`${prefix} (${state.spotdlDone}/${state.spotdlTotal}): ${m[1]}`);
            setProgress(Math.min(100, state.spotdlDone / state.spotdlTotal * 100));
        } else {
            setStatus(`${prefix}: ${m[1]}`);
        }
    } else if (/^Processing query/i.test(message)) {
        setStatus("Buscando en Spotify…");
    }

    if ((m = message.match(/^\[download\]\s+(\d{1,3}(?:\.\d+)?)%/))) {
        setProgress(Math.min(100, parseFloat(m[1])));
    }
}

// Muestra bajo la barra de estado la causa probable del error, fuera del registro técnico
function showExplanation(text) {
    let box = document.getElementById('status-explain');
    if (!box) {
        box = document.createElement('p');
        box.id = 'status-explain';
        box.className = 'status-explain';
        progressBar.parentElement.after(box);
    }
    // En playlists puede haber varias causas distintas: se listan todas
    if (!box.hidden && box.textContent && !box.textContent.includes(text)) {
        box.textContent += ' ' + text;
    } else {
        box.textContent = text;
    }
    box.hidden = false;
}

// Llamado desde Python
function appendLog(message, isError = false, isSuccess = false) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    if (isError || /^(error\b|\w+Error\b)/i.test(message)) entry.classList.add('error');
    else if (/^(warning|advertencia)\b/i.test(message)) entry.classList.add('warning');
    // Explicaciones en español que main.py agrega tras errores conocidos (KNOWN_ERRORS)
    // Explicaciones de errores (KNOWN_ERRORS) y avisos de main.py: se destacan bajo la barra
    const notice = message.match(/^(?:Explicación|Aviso): (.+)$/);
    if (notice) {
        entry.classList.add('explain');
        showExplanation(notice[1]);
    }
    if (isSuccess) entry.classList.add('success');
    entry.textContent = message;

    const atBottom = consoleDiv.scrollHeight - consoleDiv.scrollTop - consoleDiv.clientHeight < 20;
    consoleDiv.appendChild(entry);
    if (atBottom) consoleDiv.scrollTop = consoleDiv.scrollHeight;

    updateStatusFromLog(message);
}

function finishStatus(status, text) {
    statusBox.classList.add(status);
    setStatus(text);
    setProgress(status === 'cancelled' ? 0 : 100);
}

// Llamado desde Python. status: 'success' | 'partial' | 'error' | 'cancelled'
function onDownloadComplete(status) {
    setDownloading(false);

    if (status === 'success') {
        if (state.skipped && !state.newFiles) {
            finishStatus(status, state.skipped === 1 ? "Ya lo tenías: no se descargó nada" : "Ya tenías todo: no se descargó nada nuevo");
        } else if (state.skipped) {
            finishStatus(status, `¡Listo! Se omitieron ${state.skipped} que ya tenías`);
        } else {
            finishStatus(status, "¡Descarga completada!");
        }
        appendLog("¡Descarga completada con éxito!", false, true);
    } else if (status === 'partial') {
        finishStatus(status, "Completado, pero algunos elementos fallaron");
        appendLog("Algunos elementos no se pudieron descargar; revisa los errores arriba.", true);
        logDetails.open = true;
    } else if (status === 'cancelled') {
        finishStatus(status, "Descarga cancelada");
    } else {
        finishStatus(status, "Hubo un error en la descarga");
        appendLog("Hubo un error en la descarga.", true, false);
        if (hasPendingUpdates()) {
            appendLog("Hay una actualización pendiente de las herramientas: actualizar suele resolver estos errores.", true);
        }
        logDetails.open = true;
    }
}

// Iniciar app
loadPreferences();
// La API de Python se inyecta de forma asíncrona
function onApiReady() {
    checkEnvironment();
    loadAccount();
}
if (window.pywebview?.api) onApiReady();
else window.addEventListener('pywebviewready', onApiReady);
