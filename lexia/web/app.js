'use strict';

// Lexia Read Mode preview. Vanilla JS, no dependencies.
// All state lives in this object: the current session and its chunks.
var state = {
  sessionId: null,
  chunks: [],
  currentChunkIndex: 0
};

var apiBaseInput = document.getElementById('apiBase');
var imageFileInput = document.getElementById('imageFile');
var simplifyInput = document.getElementById('simplify');
var readButton = document.getElementById('readButton');
var statusArea = document.getElementById('status');
var resultEngine = document.getElementById('resultEngine');
var resultConfidence = document.getElementById('resultConfidence');
var resultSession = document.getElementById('resultSession');
var fullText = document.getElementById('fullText');
var chunkList = document.getElementById('chunkList');
var readerChunk = document.getElementById('readerChunk');
var readerPosition = document.getElementById('readerPosition');
var prevButton = document.getElementById('prevButton');
var nextButton = document.getElementById('nextButton');
var speakButton = document.getElementById('speakButton');
var speakInfo = document.getElementById('speakInfo');
var audioPlayer = document.getElementById('audioPlayer');

function getBaseUrl() {
  var value = apiBaseInput.value.trim();
  while (value.endsWith('/')) {
    value = value.slice(0, -1);
  }
  return value;
}

function setStatus(message) {
  statusArea.textContent = message;
}

function formatConfidence(value) {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  if (typeof value === 'number' && isFinite(value)) {
    return String(value);
  }
  return String(value);
}

// Read the chosen file as base64 text, with the data: prefix stripped.
function readFileAsBase64(file) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () {
      var result = String(reader.result || '');
      var commaIndex = result.indexOf(',');
      if (commaIndex !== -1) {
        result = result.slice(commaIndex + 1);
      }
      resolve(result);
    };
    reader.onerror = function () {
      reject(new Error('Could not read the selected file.'));
    };
    reader.readAsDataURL(file);
  });
}

// Turn a failed fetch response into a plain error message.
function responseError(response, bodyText) {
  var message = 'Request failed with status ' + response.status + '.';
  if (bodyText) {
    try {
      var parsed = JSON.parse(bodyText);
      if (parsed && parsed.detail) {
        message = message + ' ' + String(parsed.detail);
      } else {
        message = message + ' ' + bodyText;
      }
    } catch (err) {
      message = message + ' ' + bodyText;
    }
  }
  return new Error(message);
}

function updateReader() {
  if (!state.chunks || state.chunks.length === 0) {
    readerChunk.textContent = 'No chunk selected yet.';
    readerPosition.textContent = '';
    prevButton.disabled = true;
    nextButton.disabled = true;
    return;
  }
  if (state.currentChunkIndex < 0) {
    state.currentChunkIndex = 0;
  }
  if (state.currentChunkIndex >= state.chunks.length) {
    state.currentChunkIndex = state.chunks.length - 1;
  }
  var chunk = state.chunks[state.currentChunkIndex];
  readerChunk.textContent = chunk.text || '';
  readerPosition.textContent =
    'Chunk ' + (state.currentChunkIndex + 1) + ' of ' + state.chunks.length +
    ' (index ' + chunk.index + ')';
  prevButton.disabled = state.currentChunkIndex === 0;
  nextButton.disabled = state.currentChunkIndex === state.chunks.length - 1;
}

function renderResults(data) {
  state.sessionId = data.session_id || null;
  state.chunks = Array.isArray(data.chunks) ? data.chunks : [];
  state.currentChunkIndex = 0;

  resultEngine.textContent = data.engine || '';
  resultConfidence.textContent = formatConfidence(data.mean_confidence);
  resultSession.textContent = state.sessionId || '';
  fullText.textContent = data.text || '';

  chunkList.textContent = '';
  state.chunks.forEach(function (chunk) {
    var item = document.createElement('li');
    var paragraph = document.createElement('p');
    var label = 'Chunk ' + chunk.index + ': ';
    if (typeof chunk.word_count === 'number') {
      label = label + '(' + chunk.word_count + ' words) ';
    }
    paragraph.textContent = label + (chunk.text || '');
    item.appendChild(paragraph);
    chunkList.appendChild(item);
  });

  speakButton.disabled = !state.sessionId;
  speakInfo.textContent = '';
  updateReader();
}

async function handleRead() {
  var base = getBaseUrl();
  if (!base) {
    setStatus('Error: enter an API base URL first.');
    return;
  }
  var files = imageFileInput.files;
  if (!files || files.length === 0) {
    setStatus('Error: choose an image file first.');
    return;
  }
  readButton.disabled = true;
  setStatus('Reading image...');
  try {
    var imageBase64 = await readFileAsBase64(files[0]);
    var response = await fetch(base + '/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_base64: imageBase64,
        simplify: Boolean(simplifyInput.checked)
      })
    });
    var bodyText = await response.text();
    if (!response.ok) {
      throw responseError(response, bodyText);
    }
    var data = JSON.parse(bodyText);
    renderResults(data);
    setStatus('Done. Loaded ' + state.chunks.length + ' chunk(s).');
  } catch (err) {
    setStatus('Error: ' + (err && err.message ? err.message : String(err)));
  } finally {
    readButton.disabled = false;
  }
}

async function handleSpeak() {
  var base = getBaseUrl();
  if (!base) {
    setStatus('Error: enter an API base URL first.');
    return;
  }
  if (!state.sessionId) {
    setStatus('Error: run Read first, there is no session to speak yet.');
    return;
  }
  speakButton.disabled = true;
  setStatus('Preparing audio...');
  try {
    var url = base + '/read/' + encodeURIComponent(state.sessionId) + '/speak';
    var response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    var bodyText = await response.text();
    if (!response.ok) {
      throw responseError(response, bodyText);
    }
    var data = JSON.parse(bodyText);
    if (!data.audio_url) {
      throw new Error('Speak response did not include an audio_url.');
    }
    var audioUrl = data.audio_url;
    if (!audioUrl.startsWith('http://') && !audioUrl.startsWith('https://')) {
      if (!audioUrl.startsWith('/')) {
        audioUrl = '/' + audioUrl;
      }
      audioUrl = base + audioUrl;
    }
    audioPlayer.src = audioUrl;
    audioPlayer.load();
    var infoParts = [];
    if (data.engine) {
      infoParts.push('Engine: ' + data.engine);
    }
    if (data.duration_seconds !== null && data.duration_seconds !== undefined) {
      infoParts.push('Duration: ' + data.duration_seconds + ' seconds');
    }
    speakInfo.textContent = infoParts.join('. ');
    setStatus('Audio ready.');
  } catch (err) {
    setStatus('Error: ' + (err && err.message ? err.message : String(err)));
  } finally {
    speakButton.disabled = !state.sessionId;
  }
}

function handlePrev() {
  if (state.currentChunkIndex > 0) {
    state.currentChunkIndex = state.currentChunkIndex - 1;
    updateReader();
  }
}

function handleNext() {
  if (state.currentChunkIndex < state.chunks.length - 1) {
    state.currentChunkIndex = state.currentChunkIndex + 1;
    updateReader();
  }
}

readButton.addEventListener('click', handleRead);
speakButton.addEventListener('click', handleSpeak);
prevButton.addEventListener('click', handlePrev);
nextButton.addEventListener('click', handleNext);

updateReader();
