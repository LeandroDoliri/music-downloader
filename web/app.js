let selectedFolder = '';

// Elementos UI
const urlInput = document.getElementById('url');
const formatSelect = document.getElementById('format');
const advFormatSelect = document.getElementById('adv-format');
const advQualitySelect = document.getElementById('adv-quality');
const chkPlaylist = document.getElementById('chk-playlist');
const chkMetadata = document.getElementById('chk-metadata');
const chkSubs = document.getElementById('chk-subs');
const lblSubs = document.getElementById('lbl-subs');
const btnAdvanced = document.getElementById('btn-advanced');
const advancedPanel = document.getElementById('advanced-panel');

// Cargar preferencias guardadas
function loadPreferences() {
    selectedFolder = localStorage.getItem('music_downloader_folder') || '';
    if (selectedFolder) {
        const folderPath = document.getElementById('folder-path');
        folderPath.textContent = `Guardar en: ${selectedFolder}`;
        folderPath.style.display = 'block';
    }

    if (localStorage.getItem('music_downloader_advFormat')) {
        advFormatSelect.value = localStorage.getItem('music_downloader_advFormat');
    }
    if (localStorage.getItem('music_downloader_advQuality')) {
        advQualitySelect.value = localStorage.getItem('music_downloader_advQuality');
    }
    
    chkPlaylist.checked = localStorage.getItem('music_downloader_playlist') === 'true';
    chkMetadata.checked = localStorage.getItem('music_downloader_metadata') !== 'false'; // default true
    chkSubs.checked = localStorage.getItem('music_downloader_subs') === 'true';
}

function savePreferences() {
    localStorage.setItem('music_downloader_folder', selectedFolder);
    localStorage.setItem('music_downloader_advFormat', advFormatSelect.value);
    localStorage.setItem('music_downloader_advQuality', advQualitySelect.value);
    localStorage.setItem('music_downloader_playlist', chkPlaylist.checked);
    localStorage.setItem('music_downloader_metadata', chkMetadata.checked);
    localStorage.setItem('music_downloader_subs', chkSubs.checked);
}

// Acordeon
btnAdvanced.addEventListener('click', () => {
    btnAdvanced.classList.toggle('active');
    advancedPanel.classList.toggle('expanded');
});

// Actualizar opciones avanzadas en base al formato principal
function updateAdvancedOptions() {
    const isVideo = formatSelect.value === 'mp4';
    
    // Opciones de formato
    advFormatSelect.innerHTML = '';
    if (isVideo) {
        advFormatSelect.innerHTML = `
            <option value="mp4">MP4</option>
            <option value="mkv">MKV</option>
            <option value="webm">WEBM</option>
        `;
    } else {
        advFormatSelect.innerHTML = `
            <option value="mp3">MP3</option>
            <option value="m4a">M4A</option>
            <option value="flac">FLAC</option>
            <option value="wav">WAV</option>
        `;
    }
    
    // Opciones de calidad
    advQualitySelect.innerHTML = '';
    if (isVideo) {
        advQualitySelect.innerHTML = `
            <option value="best">Mejor calidad</option>
            <option value="1080">1080p</option>
            <option value="720">720p</option>
            <option value="480">480p</option>
        `;
    } else {
        advQualitySelect.innerHTML = `
            <option value="320k">320 kbps</option>
            <option value="256k">256 kbps</option>
            <option value="192k">192 kbps</option>
            <option value="128k">128 kbps</option>
        `;
    }
}

// Actualizar subtitulos si es YT
urlInput.addEventListener('input', () => {
    if (urlInput.value.includes('youtube.com') || urlInput.value.includes('youtu.be')) {
        lblSubs.style.display = 'flex';
    } else {
        lblSubs.style.display = 'none';
        chkSubs.checked = false;
    }
});

formatSelect.addEventListener('change', updateAdvancedOptions);

document.getElementById('btn-folder').addEventListener('click', () => {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.choose_directory().then(folder => {
            if (folder) {
                selectedFolder = folder;
                const folderPath = document.getElementById('folder-path');
                folderPath.textContent = `Guardar en: ${folder}`;
                folderPath.style.display = 'block';
                savePreferences();
            }
        });
    }
});

document.getElementById('btn-download').addEventListener('click', () => {
    const url = urlInput.value;
    const format = formatSelect.value;
    
    if (!url) {
        alert("Por favor ingresa un enlace válido.");
        return;
    }
    if (!selectedFolder) {
        alert("Por favor selecciona una carpeta de destino.");
        return;
    }

    savePreferences();

    const consoleContainer = document.getElementById('console-container');
    const consoleDiv = document.getElementById('console');
    const btn = document.getElementById('btn-download');
    
    consoleContainer.classList.add('active');
    consoleDiv.innerHTML = '';
    btn.disabled = true;
    btn.classList.add('loading');
    btn.querySelector('span').textContent = "Descargando...";
    
    document.getElementById('progress-bar').style.width = '100%';
    document.getElementById('progress-bar').classList.add('indeterminate');

    const tool = url.includes('spotify') ? 'spotdl' : 'yt-dlp';
    
    const options = {
        url: url,
        folder: selectedFolder,
        mainFormat: format,
        advFormat: advFormatSelect.value,
        quality: advQualitySelect.value,
        playlist: chkPlaylist.checked,
        metadata: chkMetadata.checked,
        subs: chkSubs.checked
    };

    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.download(tool, options);
    }
});

function appendLog(message, isError=false, isSuccess=false) {
    const consoleDiv = document.getElementById('console');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    if (isError) entry.classList.add('error');
    if (isSuccess) entry.classList.add('success');
    entry.textContent = message;
    
    consoleDiv.appendChild(entry);
    consoleDiv.scrollTop = consoleDiv.scrollHeight;

    if (message.includes('%')) {
        const match = message.match(/(\d+\.?\d*)%/);
        if (match) {
            document.getElementById('progress-bar').classList.remove('indeterminate');
            document.getElementById('progress-bar').style.width = match[1] + '%';
        }
    }
}

function onDownloadComplete(success) {
    const btn = document.getElementById('btn-download');
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.querySelector('span').textContent = "Descargar";
    document.getElementById('progress-bar').classList.remove('indeterminate');
    
    if (success) {
        document.getElementById('progress-bar').style.width = '100%';
        appendLog("¡Descarga completada con éxito!", false, true);
    } else {
        appendLog("Hubo un error en la descarga.", true, false);
    }
}

// Iniciar app
loadPreferences();
updateAdvancedOptions();
