/*
 * The phone interface of YouTube Clipster.
 *
 * The PC does the work; this only sends links and shows what came of them.
 *
 * Authentication: the token arrives once in the URL - from the QR code shown in
 * the view window - and the server answers with a cookie. Every later request is
 * a same-origin fetch, which carries that cookie by itself, so the token is
 * never kept in JavaScript and never written to storage.
 */

"use strict";

/** How often the running downloads are polled, in milliseconds. */
const POLL_INTERVAL = 2000;

const elements = {
    form: document.getElementById("submit-form"),
    url: document.getElementById("url"),
    send: document.getElementById("send"),
    message: document.getElementById("message"),
    running: document.getElementById("running"),
    active: document.getElementById("active"),
    downloads: document.getElementById("downloads"),
    empty: document.getElementById("empty"),
    refresh: document.getElementById("refresh"),
    historyClear: document.getElementById("history-clear"),
    connection: document.getElementById("connection"),
    quit: document.getElementById("quit"),
    player: document.getElementById("player"),
    tabDownloads: document.getElementById("tab-downloads"),
    tabStreaming: document.getElementById("tab-streaming"),
    tabSettings: document.getElementById("tab-settings"),
    tabAbout: document.getElementById("tab-about"),
    viewDownloads: document.getElementById("view-downloads"),
    viewStreaming: document.getElementById("view-streaming"),
    viewSettings: document.getElementById("view-settings"),
    viewAbout: document.getElementById("view-about"),
    streamTitle: document.getElementById("stream-title"),
    streamUploader: document.getElementById("stream-uploader"),
    streamTrack: document.getElementById("stream-track"),
    streamFill: document.getElementById("stream-fill"),
    streamTime: document.getElementById("stream-time"),
    streamLevel: document.getElementById("stream-level"),
    streamToggle: document.getElementById("stream-toggle"),
    streamStop: document.getElementById("stream-stop"),
    streamMessage: document.getElementById("stream-message"),
    streamRefresh: document.getElementById("stream-refresh"),
    streamLike: document.getElementById("stream-like"),
    streamDislike: document.getElementById("stream-dislike"),
    queue: document.getElementById("queue"),
    queueEmpty: document.getElementById("queue-empty"),
    queueCard: document.getElementById("queue-card"),
    votes: document.getElementById("votes"),
    votesEmpty: document.getElementById("votes-empty"),
    votesCard: document.getElementById("votes-card"),
    nowPlaying: document.getElementById("now-playing"),
    streamEmptyHint: document.getElementById("stream-empty-hint"),
    search: document.getElementById("search"),
    searchNote: document.getElementById("search-note"),
    results: document.getElementById("results"),
    resultsToggle: document.getElementById("results-toggle"),
    targetCard: document.getElementById("target-card"),
    targetNote: document.getElementById("target-note"),
    targetHostChip: document.getElementById("target-host-chip"),
    targetGuestLabel: document.getElementById("target-guest-label"),
    volumeRow: document.getElementById("volume-row"),
    volume: document.getElementById("volume"),
    volumeValue: document.getElementById("volume-value"),
    settingsForm: document.getElementById("settings-form"),
    settingsMessage: document.getElementById("settings-message"),
    settingsSave: document.getElementById("settings-save"),
    settingsReload: document.getElementById("settings-reload"),
    setLanguage: document.getElementById("set-language"),
    setFormat: document.getElementById("set-format"),
    setDownloadDir: document.getElementById("set-download-dir"),
    setDownloadResolved: document.getElementById("set-download-resolved"),
    setHistory: document.getElementById("set-history"),
    setParallel: document.getElementById("set-parallel"),
    setMaxParallel: document.getElementById("set-max-parallel"),
    setNoPlaylist: document.getElementById("set-no-playlist"),
    setRestrict: document.getElementById("set-restrict"),
    setAskAudio: document.getElementById("set-ask-audio"),
    setSuffix: document.getElementById("set-suffix"),
    setMode: document.getElementById("set-mode"),
    setMaxResults: document.getElementById("set-max-results"),
    setRequireSuffix: document.getElementById("set-require-suffix"),
    setCookiesRisk: document.getElementById("set-cookies-risk"),
    setCookiesBrowser: document.getElementById("set-cookies-browser"),
    setCookiesFile: document.getElementById("set-cookies-file"),
    stage: document.getElementById("stage"),
    streamVideo: document.getElementById("stream-video"),
    streamLibrary: document.getElementById("stream-library"),
    streamShuffle: document.getElementById("stream-shuffle"),
    streamRepeat: document.getElementById("stream-repeat"),
    streamSleep: document.getElementById("stream-sleep"),
    streamScan: document.getElementById("stream-scan"),
    playbackNote: document.getElementById("playback-note"),
    shareDialog: document.getElementById("share-dialog"),
    shareTitle: document.getElementById("share-title"),
    shareCode: document.getElementById("share-code"),
    shareHint: document.getElementById("share-hint"),
    shareLink: document.getElementById("share-link"),
    shareCopy: document.getElementById("share-copy"),
    shareClose: document.getElementById("share-close"),
    scanDialog: document.getElementById("scan-dialog"),
    scanVideo: document.getElementById("scan-video"),
    scanHint: document.getElementById("scan-hint"),
    scanClose: document.getElementById("scan-close"),
    setMobile: document.getElementById("set-mobile"),
    setLocalOnly: document.getElementById("set-local-only"),
    setShuffle: document.getElementById("set-shuffle"),
    setRepeat: document.getElementById("set-repeat"),
    setPlayVideo: document.getElementById("set-play-video"),
    setVisualizer: document.getElementById("set-visualizer"),
    setExtendCount: document.getElementById("set-extend-count"),
    aboutName: document.getElementById("about-name"),
    aboutVersion: document.getElementById("about-version"),
    aboutText: document.getElementById("about-text"),
    aboutLicense: document.getElementById("about-license"),
    aboutAuthor: document.getElementById("about-author"),
    aboutWebsite: document.getElementById("about-website"),
    aboutRepo: document.getElementById("about-repo"),
    aboutPaths: document.getElementById("about-paths"),
    aboutTermsApp: document.getElementById("about-terms-app"),
    aboutTermsStreaming: document.getElementById("about-terms-streaming"),
    updateHeading: document.getElementById("update-heading"),
    updateState: document.getElementById("update-state"),
    updateButton: document.getElementById("update-button"),
    termsDialog: document.getElementById("terms-dialog"),
    termsTitle: document.getElementById("terms-title"),
    termsBody: document.getElementById("terms-body"),
    termsAccept: document.getElementById("terms-accept"),
    termsDecline: document.getElementById("terms-decline"),
};

/** Where the sound comes out: "host" (the PC) or "guest" (this device). */
let target = "host";

/** Pending search timer, so only the last keystroke starts a search. */
let searchTimer = null;

/** Idle time before searching, in ms; the PC's setting wins over this default. */
let searchDelay = 1500;

/** The queue as the device knows it, needed to play the next one locally. */
let queueTracks = [];

/**
 * What the backend says about streaming on this connection.
 *
 * Shape: {connection, metered, local_only, ask, mode}. The default allows
 * everything, so a backend that never reports it behaves exactly as before.
 */
let playbackSource = {connection: "", metered: false, local_only: false, ask: false,
                      mode: "stream"};

/** Track being played on this device, while target is "guest". */
let guestVideoId = "";

/** Its position in the queue, or -1 while the queue has not caught up. */
let guestIndex = -1;
/** Current track vote from the last poll (``up`` / ``down`` / ``""``). */
let lastVote = "";
/** Vote by YouTube id — refreshed from each ``/api/discover`` state. */
let voteById = {};
/** YouTube id whose 👍/👎 buttons are currently shown. */
let ratedVideoId = "";
/** Whether the Streaming queue had tracks on the last render (for auto-play). */
let hadQueueTracks = false;
/** Video ids that failed to play this session — skipped instead of retried. */
let unplayableIds = {};
/** Bumped on every ``playHere`` so a late error from an old src is ignored. */
let playGeneration = 0;
/** Watchdog: skip when the stream never becomes playable. */
let playWatchTimer = null;

/** The track the queue was last centred on, so scrolling only follows changes. */
let centredOn = "";

/** Which view is on screen: "streaming", "downloads", "settings" or "about". */
let view = "streaming";

/** Standalone Android run (owns Quit and plays only on this device). */
let standalone = false;

/** Whether Streaming terms are already accepted on this device / host. */
let streamingTermsOk = true;

/** Signature of the queue as last rendered, so a poll does not rebuild it. */
let lastQueue = "";

/** Length of the track being played, needed to turn a tap into a position. */
let streamDuration = 0;

let pollTimer = null;
let lastSignature = "";
/** Cached download rows for client-side status filters. */
let downloadEntries = [];
/** Active downloads filter: "all", "ok", "failed" or "canceled". */
let downloadFilter = "all";

/**
 * Read a query parameter of the current URL.
 *
 * @param {string} name Parameter to look for.
 * @returns {string} The value, or an empty string.
 */
function queryParam(name) {
    return new URLSearchParams(window.location.search).get(name) || "";
}

/**
 * Remove the token from the address bar once the cookie has been set.
 *
 * A URL that keeps the token would end up in the browser history, and in
 * whatever the user shares next.
 *
 * @returns {void}
 */
function hideToken() {
    if (!window.location.search) {
        return;
    }
    window.history.replaceState({}, "", window.location.pathname + window.location.hash);
}

/**
 * Show a short message below the form.
 *
 * @param {string} text What to say; an empty string hides the line.
 * @param {string} [kind] "good", "bad", or nothing for neutral.
 * @returns {void}
 */
function say(text, kind) {
    elements.message.textContent = text;
    elements.message.className = kind ? "message " + kind : "message";
    elements.message.hidden = !text;
}

/**
 * Mark whether the PC could be reached.
 *
 * @param {boolean} online Whether the last request succeeded.
 * @returns {void}
 */
function setConnection(online) {
    elements.connection.className = online ? "dot online" : "dot offline";
}

/**
 * Describe the connection this device is currently on.
 *
 * Only the device itself can know this - the PC cannot see which network a
 * phone is holding - so it is reported with the status polls. Browsers without
 * the Network Information API (Safari, Firefox) return an empty string, which
 * the backend reads as "no idea" and therefore as "not metered": guessing
 * mobile would stop the music on machines that simply never report anything.
 *
 * @returns {string} For example "cellular" or "wifi"; empty when unknown.
 */
function connectionType() {
    const link = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!link) {
        return "";
    }
    // "type" is the honest answer; effectiveType only describes the speed, but
    // a connection that behaves like 3g is worth treating as metered too.
    return String(link.type || link.effectiveType || "").toLowerCase();
}

/**
 * Return the status path, carrying what this device knows about its connection.
 *
 * @returns {string} "/api/status", with a "net" parameter when there is one.
 */
function statusPath() {
    const link = connectionType();
    return link ? "/api/status?net=" + encodeURIComponent(link) : "/api/status";
}

/**
 * Talk to the API and decode the answer.
 *
 * @param {string} path Path below the origin, e.g. "/api/status".
 * @param {object} [options] Extra fetch options.
 * @returns {Promise<{status: number, body: object}>} Status and decoded body.
 */
async function api(path, options) {
    const response = await fetch(path, Object.assign({cache: "no-store"}, options || {}));
    let body = {};
    try {
        body = await response.json();
    } catch (error) {
        body = {};
    }
    setConnection(true);
    if (response.status === 401) {
        say("This device is not registered any more. Scan the QR code in " +
            "YouTube Clipster again.", "bad");
    }
    return {status: response.status, body: body};
}

/**
 * Turn a byte count into something readable.
 *
 * @param {number} size Size in bytes.
 * @returns {string} For example "4.7 MB", or "-" when unknown.
 */
function formatSize(size) {
    if (!size) {
        return "-";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = size;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
}

/**
 * Turn a number of seconds into mm:ss or h:mm:ss.
 *
 * @param {number} seconds Length of the video.
 * @returns {string} The formatted length, or "-" when unknown.
 */
function formatDuration(seconds) {
    if (!seconds) {
        return "-";
    }
    const total = Math.round(seconds);
    const parts = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
    const shown = parts[0] > 0 ? parts : parts.slice(1);
    return shown
        .map((part, index) => (index === 0 ? String(part) : String(part).padStart(2, "0")))
        .join(":");
}

/**
 * Turn an ISO timestamp into a local date and time.
 *
 * @param {string} stamp ISO 8601 timestamp.
 * @returns {string} The local representation, or "-".
 */
function formatDate(stamp) {
    if (!stamp) {
        return "-";
    }
    const when = new Date(stamp);
    if (Number.isNaN(when.getTime())) {
        return stamp;
    }
    return when.toLocaleString();
}

// ---------------------------------------------------------------- submitting
/**
 * Explain a submission result in one sentence.
 *
 * @param {number} status The HTTP status.
 * @param {object} body The decoded answer.
 * @returns {{text: string, kind: string}} What to show.
 */
function describeSubmission(status, body) {
    const states = {
        started: {text: "Download started.", kind: "good"},
        queued: {text: "Queued - it starts as soon as the current one is done.", kind: "good"},
        exists: {text: "Already downloaded. It is in the list below.", kind: "good"},
        running: {text: "This link is downloading right now.", kind: ""},
        waiting: {text: "This link is already waiting.", kind: ""},
        full: {text: "Too many downloads are waiting. Try again later.", kind: "bad"},
        closing: {text: "YouTube Clipster is shutting down on the PC.", kind: "bad"},
        invalid: {text: "That is not a YouTube link.", kind: "bad"},
        format: {text: "Pick MP3 or MP4.", kind: "bad"},
    };
    if (states[body.state]) {
        return states[body.state];
    }
    if (status === 401) {
        return {text: "Not registered any more.", kind: "bad"};
    }
    return {text: "The PC did not accept the link.", kind: "bad"};
}

/**
 * Send the link in the form to the PC.
 *
 * @param {Event} event The submit event.
 * @returns {Promise<void>}
 */
async function submit(event) {
    event.preventDefault();
    const url = elements.url.value.trim();
    if (!url) {
        say("Paste a YouTube link first.", "bad");
        return;
    }
    const chosen = elements.form.querySelector("input[name=format]:checked");
    elements.send.disabled = true;
    say("Sending...");
    try {
        const answer = await api("/api/submit", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({url: url, format: chosen ? chosen.value : "mp3"}),
        });
        const described = describeSubmission(answer.status, answer.body);
        say(described.text, described.kind);
        if (answer.body.accepted || answer.body.state === "exists") {
            elements.url.value = "";
            await loadDownloads();
        }
    } catch (error) {
        setConnection(false);
        say("The PC cannot be reached. Is YouTube Clipster running?", "bad");
    } finally {
        elements.send.disabled = false;
    }
}

// ------------------------------------------------------------------ playback
/**
 * Play a finished download.
 *
 * Audio plays inline; a video is handed to the browser, which knows better than
 * this page how to show it full screen.
 *
 * @param {object} entry One item of the download list.
 * @returns {void}
 */
function play(entry) {
    const source = "/media/" + encodeURIComponent(entry.id);
    if (entry.format === "mp4") {
        window.open(source, "_blank");
        return;
    }
    elements.player.hidden = false;
    elements.player.src = source;
    elements.player.play().catch(() => {
        say("The phone refused to start playback. Tap play in the player.", "");
    });
}

/**
 * Delete a download on the PC, after asking.
 *
 * @param {object} entry One item of the download list.
 * @returns {Promise<void>}
 */
async function remove(entry) {
    if (!window.confirm("Delete “" + entry.name + "” on the PC?")) {
        return;
    }
    try {
        const answer = await api("/api/downloads/" + encodeURIComponent(entry.id),
                                 {method: "DELETE"});
        if (answer.body.deleted) {
            say("Deleted.", "good");
            await loadDownloads();
        } else {
            say("The file could not be deleted.", "bad");
        }
    } catch (error) {
        setConnection(false);
        say("The PC cannot be reached.", "bad");
    }
}

// ------------------------------------------------------------------ rendering
/**
 * Build one button.
 *
 * @param {string} glyph Label of the button.
 * @param {string} title Accessible name.
 * @param {Function} action What to run on click.
 * @param {string} [extra] Additional CSS class.
 * @returns {HTMLButtonElement} The button.
 */
function button(glyph, title, action, extra) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = extra ? "icon " + extra : "icon";
    element.textContent = glyph;
    element.title = title;
    element.setAttribute("aria-label", title);
    element.addEventListener("click", action);
    return element;
}

/**
 * Build one row of the download list.
 *
 * @param {object} entry One item of the download list.
 * @returns {HTMLLIElement} The row.
 */
function downloadRow(entry) {
    const row = document.createElement("li");

    const badge = document.createElement("span");
    badge.className = "badge " + entry.status;
    badge.textContent = (entry.format || "?").toUpperCase();
    row.appendChild(badge);

    const body = document.createElement("div");
    body.className = "body";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = entry.name || entry.title || entry.url;
    body.appendChild(name);
    // A failed download knows neither length nor size, and a row of dashes says
    // nothing - then the line is left out entirely.
    const facts = [formatDuration(entry.duration), formatSize(entry.size),
                   formatDate(entry.finished_at)].filter((fact) => fact !== "-");
    if (facts.length > 0) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = facts.join(" · ");
        body.appendChild(meta);
    }
    if (entry.error) {
        const problem = document.createElement("div");
        problem.className = "problem";
        problem.textContent = entry.error;
        body.appendChild(problem);
    }
    bindShare(body, videoIdOf(entry.url), entry.title || entry.name);
    row.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "actions";
    if (entry.playable) {
        actions.appendChild(button("▶", "Play", () => play(entry)));
        const save = document.createElement("a");
        save.className = "icon";
        save.href = "/media/" + encodeURIComponent(entry.id);
        save.textContent = "⤓";
        save.title = "Save";
        save.setAttribute("aria-label", "Save");
        save.setAttribute("download", entry.name || "");
        actions.appendChild(save);
    }
    actions.appendChild(button("✕", "Delete", () => remove(entry), "danger"));
    actions.appendChild(button("–", "Hide", () => hideEntry(entry)));
    row.appendChild(actions);
    return row;
}

/**
 * Hide a download from the list but keep the file.
 *
 * @param {object} entry One item of the download list.
 * @returns {Promise<void>}
 */
async function hideEntry(entry) {
    try {
        const answer = await api("/api/downloads/hide", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({id: entry.id}),
        });
        if (answer.body.hidden) {
            say("Hidden from the list.", "good");
            lastSignature = "";
            await loadDownloads();
        } else {
            say("Could not hide that entry.", "bad");
        }
    } catch (error) {
        setConnection(false);
        say(standalone ? "Clipster cannot be reached." : "The PC cannot be reached.", "bad");
    }
}

/**
 * Clear the whole download list (files stay on disk).
 *
 * @returns {Promise<void>}
 */
async function clearHistory() {
    if (!window.confirm("Remove all entries from the list? Downloaded files are kept.")) {
        return;
    }
    try {
        const answer = await api("/api/downloads/clear", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: "{}",
        });
        if (answer.body.cleared) {
            say("List cleared.", "good");
            lastSignature = "";
            await loadDownloads();
        } else {
            say("Could not clear the list.", "bad");
        }
    } catch (error) {
        setConnection(false);
        say(standalone ? "Clipster cannot be reached." : "The PC cannot be reached.", "bad");
    }
}

/**
 * Apply the active status filter to the cached download rows.
 *
 * @returns {void}
 */
/**
 * How each column sorts, matching _SORT_KEYS on the Python side.
 *
 * Names read as text, everything else as a number, so that 9 MB sorts below
 * 10 MB rather than above it.
 */
const sortValues = {
    name: (entry) => String(entry.name || "").toLowerCase(),
    duration: (entry) => Number(entry.duration || 0),
    size: (entry) => Number(entry.size || 0),
    date: (entry) => String(entry.finished_at || ""),
};

/**
 * Which direction a column starts in, matching _SORT_DESCENDING_FIRST.
 *
 * Text reads best from A; numbers and dates from the largest, because the
 * newest download is the one being looked for.
 */
const sortDescendingFirst = {name: false, duration: true, size: true, date: true};

/** The column the list is sorted by, and which way round. */
let sortKey = "date";
let sortDescending = true;

/**
 * Sort the download list by the active column.
 *
 * @param {Array<object>} entries The rows to sort.
 * @returns {Array<object>} A sorted copy.
 */
function sortDownloads(entries) {
    const value = sortValues[sortKey] || sortValues.date;
    // Copied first: the unsorted order is what the next signature compares.
    return entries.slice().sort((left, right) => {
        const a = value(left);
        const b = value(right);
        if (a === b) {
            return 0;
        }
        return (a < b ? -1 : 1) * (sortDescending ? -1 : 1);
    });
}

/**
 * Sort by one column, or turn its direction around when it is already active.
 *
 * @param {string} key One of the keys in {@link sortValues}.
 * @returns {void}
 */
function setSort(key) {
    if (!sortValues[key]) {
        return;
    }
    if (key === sortKey) {
        sortDescending = !sortDescending;
    } else {
        sortKey = key;
        sortDescending = sortDescendingFirst[key];
    }
    paintSortButtons();
    renderDownloadList();
}

/**
 * Mark the column the list is sorted by, and which way.
 *
 * @returns {void}
 */
function paintSortButtons() {
    document.querySelectorAll("#sort-row .sort").forEach((button) => {
        const active = button.dataset.sort === sortKey;
        button.classList.toggle("on", active);
        const label = button.textContent.replace(/[ ▲▼]+$/, "");
        button.textContent = active ? label + (sortDescending ? " ▼" : " ▲") : label;
    });
}

function renderDownloadList() {
    const filtered = sortDownloads(downloadFilter === "all"
        ? downloadEntries
        : downloadEntries.filter((entry) => entry.status === downloadFilter));
    elements.downloads.textContent = "";
    filtered.forEach((entry) => elements.downloads.appendChild(downloadRow(entry)));
    elements.empty.hidden = filtered.length > 0;
    if (filtered.length === 0 && downloadEntries.length > 0) {
        elements.empty.hidden = false;
        elements.empty.textContent = "No download matches this filter.";
    } else if (filtered.length === 0) {
        elements.empty.textContent = "Nothing here yet.";
    }
}

/**
 * Fetch and render the download list.
 *
 * @returns {Promise<void>}
 */
async function loadDownloads() {
    let answer;
    try {
        answer = await api("/api/downloads");
    } catch (error) {
        setConnection(false);
        return;
    }
    const entries = answer.body.downloads || [];
    // Only rebuild when something actually changed, so a tap is not eaten by a
    // redraw that happened to land at the same moment.
    const signature = JSON.stringify(entries.map((entry) => [entry.id, entry.playable, entry.status]));
    if (signature === lastSignature) {
        return;
    }
    lastSignature = signature;
    downloadEntries = entries;
    renderDownloadList();
}

/**
 * Render the running downloads.
 *
 * @param {Array<object>} active What the PC is working on.
 * @param {number} queued How many links are waiting.
 * @returns {void}
 */
function renderActive(active, queued) {
    elements.running.hidden = active.length === 0 && queued === 0;
    elements.active.textContent = "";
    active.forEach((item) => {
        const row = document.createElement("li");
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = item.title || item.url;
        row.appendChild(name);

        const track = document.createElement("div");
        track.className = "track";
        const fill = document.createElement("div");
        const percent = typeof item.percent === "number" ? item.percent : null;
        fill.className = percent === null ? "fill unknown" : "fill";
        if (percent !== null) {
            fill.style.width = Math.max(0, Math.min(100, percent)) + "%";
        }
        track.appendChild(fill);
        row.appendChild(track);

        const phase = document.createElement("span");
        phase.className = "phase";
        phase.textContent = [item.phase, percent === null ? "" : percent.toFixed(0) + "%",
                             item.detail].filter(Boolean).join(" · ");
        row.appendChild(phase);
        elements.active.appendChild(row);
    });
    if (queued > 0) {
        const waiting = document.createElement("li");
        waiting.className = "phase";
        waiting.textContent = queued + (queued === 1 ? " link waiting" : " links waiting");
        elements.active.appendChild(waiting);
    }
}

/**
 * Ask the PC what it is doing and reflect it.
 *
 * @returns {Promise<void>}
 */
async function poll() {
    let answer;
    try {
        answer = await api(statusPath());
    } catch (error) {
        setConnection(false);
        return;
    }
    const active = answer.body.active || [];
    renderActive(active, answer.body.queued || 0);
    if (elements.quit) {
        elements.quit.hidden = !answer.body.can_quit;
    }
    applyStandalone(!!answer.body.can_quit);
    if (active.length > 0) {
        // A download just finished somewhere between two polls.
        await loadDownloads();
    }
}

/**
 * Adapt the UI when Clipster runs on the phone itself (not remoting a PC).
 *
 * @param {boolean} on Whether this is the standalone Android app.
 * @returns {void}
 */
function applyStandalone(on) {
    if (standalone === on) {
        return;
    }
    standalone = on;
    // Android is always this device — "Play on" only matters when remoting a PC.
    if (elements.targetCard && on) {
        elements.targetCard.hidden = true;
    }
    if (elements.targetHostChip) {
        elements.targetHostChip.hidden = on;
    }
    if (on) {
        setTarget("guest");
        const guest = document.querySelector('input[name=target][value=guest]');
        if (guest) {
            guest.checked = true;
        }
    }
}

/**
 * Shut the standalone app down: stop the local server, then close the launcher.
 *
 * @returns {Promise<void>}
 */
async function quitApp() {
    if (elements.quit) {
        elements.quit.disabled = true;
    }
    say("Stopping…", "info");
    try {
        await api("/api/quit", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: "{}",
        });
    } catch (error) {
        // Server may already be gone; the launcher still tears the rest down.
    }
    if (window.ClipsterBridge && typeof window.ClipsterBridge.quitApp === "function") {
        window.ClipsterBridge.quitApp();
        return;
    }
    say("Stopped.", "info");
    setConnection(false);
}

/**
 * Start or stop polling with the visibility of the page.
 *
 * A phone in a pocket must not keep asking; that is the battery talking.
 *
 * @returns {void}
 */
function syncPolling() {
    if (document.hidden) {
        window.clearInterval(pollTimer);
        pollTimer = null;
        return;
    }
    if (pollTimer === null) {
        pollTimer = window.setInterval(tick, POLL_INTERVAL);
    }
    tick();
}

/**
 * One poll of whichever view is on screen.
 *
 * Only the visible one is asked: the other would cost the phone's battery and
 * the PC's event loop for nothing.
 *
 * @returns {void}
 */
function tick() {
    if (view === "streaming") {
        pollStream();
        return;
    }
    if (view === "downloads") {
        poll();
        loadDownloads();
    }
}

/**
 * Show one of the main views (same order as the Linux app).
 *
 * @param {string} name "streaming", "downloads", "settings" or "about".
 * @returns {void}
 */
function showView(name) {
    const known = {streaming: 1, downloads: 1, settings: 1, about: 1};
    if (!known[name]) {
        name = "streaming";
    }
    view = name;
    // In the address, so a reload keeps the tab the user was on - and so the
    // view can be opened directly from a bookmark.
    if (window.location.hash !== "#" + name) {
        window.history.replaceState({}, "", window.location.pathname + "#" + name);
    }
    elements.viewStreaming.hidden = name !== "streaming";
    elements.viewDownloads.hidden = name !== "downloads";
    elements.viewSettings.hidden = name !== "settings";
    elements.viewAbout.hidden = name !== "about";
    elements.tabStreaming.classList.toggle("selected", name === "streaming");
    elements.tabDownloads.classList.toggle("selected", name === "downloads");
    elements.tabSettings.classList.toggle("selected", name === "settings");
    elements.tabAbout.classList.toggle("selected", name === "about");
    if (name === "settings") {
        loadSettings();
    } else if (name === "about") {
        loadAbout();
    }
    syncPolling();
}

/**
 * Fill the Settings form from the server.
 *
 * @returns {Promise<void>}
 */
async function loadSettings() {
    let answer;
    try {
        answer = await api("/api/settings");
    } catch (error) {
        saySettings("Could not load settings.", "bad");
        return;
    }
    if (answer.status !== 200) {
        saySettings("Could not load settings.", "bad");
        return;
    }
    const s = answer.body;
    elements.setLanguage.value = s.language || "en";
    elements.setFormat.value = s.default_format || "mp3";
    elements.setDownloadDir.value = s.download_dir || "";
    elements.setDownloadResolved.textContent = s.download_dir_resolved
        ? "Resolved: " + s.download_dir_resolved : "";
    elements.setHistory.value = String(s.history_limit || 100);
    elements.setParallel.checked = !!s.parallel_downloads;
    elements.setMaxParallel.value = String(s.max_parallel_downloads || 3);
    elements.setNoPlaylist.checked = !!s.no_playlist;
    elements.setRestrict.checked = !!s.restrict_filenames;
    elements.setAskAudio.checked = !!s.ask_audio_language;
    elements.setSuffix.value = s.discover_search_suffix || "";
    elements.setMode.value = s.discover_mode || "related";
    elements.setMaxResults.value = String(s.discover_max_results || 40);
    elements.setRequireSuffix.checked = !!s.discover_require_suffix;
    elements.setCookiesRisk.checked = !!s.cookies_risk_acknowledged;
    elements.setCookiesBrowser.value = s.cookies_from_browser || "";
    elements.setCookiesFile.value = s.cookies_file || "";
    elements.setMobile.value = s.playback_on_mobile || "stream";
    elements.setLocalOnly.checked = !!s.playback_local_only;
    elements.setShuffle.checked = !!s.discover_shuffle;
    elements.setRepeat.value = s.discover_repeat || "off";
    elements.setPlayVideo.checked = !!s.discover_play_video;
    elements.setVisualizer.value = s.discover_visualizer || "pulse";
    elements.setExtendCount.value = String(s.discover_extend_count || 8);
    syncCookieFields();
    saySettings("", "");
}

/**
 * Enable cookie fields only after the risk is acknowledged.
 *
 * @returns {void}
 */
function syncCookieFields() {
    const on = !!(elements.setCookiesRisk && elements.setCookiesRisk.checked);
    if (elements.setCookiesBrowser) {
        elements.setCookiesBrowser.disabled = !on;
    }
    if (elements.setCookiesFile) {
        elements.setCookiesFile.disabled = !on;
    }
}

/**
 * Persist the Settings form.
 *
 * @param {Event} event Form submit.
 * @returns {Promise<void>}
 */
async function saveSettings(event) {
    event.preventDefault();
    const body = {
        language: elements.setLanguage.value,
        default_format: elements.setFormat.value,
        download_dir: elements.setDownloadDir.value,
        history_limit: Number(elements.setHistory.value),
        parallel_downloads: elements.setParallel.checked,
        max_parallel_downloads: Number(elements.setMaxParallel.value),
        no_playlist: elements.setNoPlaylist.checked,
        restrict_filenames: elements.setRestrict.checked,
        ask_audio_language: elements.setAskAudio.checked,
        discover_search_suffix: elements.setSuffix.value,
        discover_mode: elements.setMode.value,
        discover_max_results: Number(elements.setMaxResults.value),
        discover_require_suffix: elements.setRequireSuffix.checked,
        cookies_risk_acknowledged: elements.setCookiesRisk.checked,
        cookies_from_browser: elements.setCookiesBrowser.value,
        cookies_file: elements.setCookiesFile.value,
        playback_on_mobile: elements.setMobile.value,
        playback_local_only: elements.setLocalOnly.checked,
        discover_shuffle: elements.setShuffle.checked,
        discover_repeat: elements.setRepeat.value,
        discover_play_video: elements.setPlayVideo.checked,
        discover_visualizer: elements.setVisualizer.value,
        discover_extend_count: Number(elements.setExtendCount.value),
    };
    try {
        const answer = await api("/api/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        if (answer.status !== 200) {
            saySettings("Could not save settings.", "bad");
            return;
        }
        saySettings("Saved.", "good");
        await loadSettings();
    } catch (error) {
        saySettings("Could not save settings.", "bad");
    }
}

/**
 * @param {string} text Message text; empty hides it.
 * @param {string} kind "good", "bad", or empty.
 * @returns {void}
 */
function saySettings(text, kind) {
    elements.settingsMessage.textContent = text;
    elements.settingsMessage.className = kind ? "message " + kind : "message";
    elements.settingsMessage.hidden = !text;
}

/**
 * Fill the About page from the server.
 *
 * @returns {Promise<void>}
 */
async function loadAbout() {
    let answer;
    try {
        answer = await api("/api/about");
    } catch (error) {
        return;
    }
    if (answer.status !== 200) {
        return;
    }
    const a = answer.body;
    elements.aboutName.textContent = a.name || "YouTube Clipster";
    elements.aboutVersion.textContent = a.version ? "Version " + a.version : "";
    elements.aboutText.textContent = a.text || "";
    elements.aboutLicense.textContent = a.license || "";
    elements.aboutAuthor.textContent = a.author || "";
    if (a.website) {
        elements.aboutWebsite.href = a.website;
        elements.aboutWebsite.textContent = a.website;
    }
    if (a.repository) {
        elements.aboutRepo.href = a.repository;
        elements.aboutRepo.textContent = a.repository;
    }
    elements.aboutPaths.innerHTML = "";
    const paths = a.paths || {};
    const labels = {
        download_dir: "Downloads",
        config: "Configuration",
        history: "Download list",
        log: "Log file",
    };
    Object.keys(labels).forEach((key) => {
        if (!paths[key]) {
            return;
        }
        const li = document.createElement("li");
        const strong = document.createElement("strong");
        strong.textContent = labels[key];
        li.appendChild(strong);
        li.appendChild(document.createTextNode(paths[key]));
        elements.aboutPaths.appendChild(li);
    });
}

// ---------------------------------------------------------------- update
/** Whether the last check found a newer version, so the button installs it. */
let updateAvailable = false;

/** Labels of the update button, in the language the phone is set to. */
const updateLabels = {
    en: {check: "Check for updates", install: "Install and restart",
         checking: "Looking for a new version...", installing: "Installing the update..."},
    de: {check: "Nach Updates suchen", install: "Installieren und neu starten",
         checking: "Suche nach einer neuen Version...", installing: "Update wird installiert..."},
};

/**
 * Return the update labels for the phone's language.
 *
 * @returns {Object} The label set.
 */
function updateWords() {
    return /^de\b/i.test(navigator.language || "") ? updateLabels.de : updateLabels.en;
}

/**
 * Show an update state and set what the button will do next.
 *
 * @param {string} text What to tell the user.
 * @param {boolean} offerInstall Whether the button should install now.
 * @param {boolean} busy Whether a request is still running.
 * @returns {void}
 */
function showUpdateState(text, offerInstall, busy) {
    updateAvailable = !!offerInstall;
    if (elements.updateState) {
        elements.updateState.textContent = text;
    }
    if (elements.updateButton) {
        const words = updateWords();
        elements.updateButton.textContent = offerInstall ? words.install : words.check;
        elements.updateButton.disabled = !!busy;
    }
}

/**
 * Ask the program to look for a newer version on GitHub.
 *
 * Deliberately the same endpoint the desktop About page drives: the version
 * comparison, the branch and the archive fallback all live in one place.
 *
 * @returns {Promise<void>}
 */
async function checkUpdate() {
    const words = updateWords();
    showUpdateState(words.checking, false, true);
    let answer;
    try {
        answer = await api("/api/update");
    } catch (error) {
        showUpdateState("The check failed.", false, false);
        return;
    }
    const body = answer.body || {};
    showUpdateState(body.message || "", !!body.available, false);
}

/**
 * Fetch the new version and let the program restart itself.
 *
 * @returns {Promise<void>}
 */
async function installUpdate() {
    const words = updateWords();
    showUpdateState(words.installing, false, true);
    let answer;
    try {
        answer = await api("/api/update", {method: "POST"});
    } catch (error) {
        showUpdateState("The update failed.", true, false);
        return;
    }
    const body = answer.body || {};
    // On success the program is on its way down; leave the button disabled so
    // nobody starts a second update into a process that is already restarting.
    showUpdateState(body.message || "", !body.ok, !!body.restarting);
}

// -------------------------------------------------------------- streaming
/**
 * Say something in the Streaming view.
 *
 * @param {string} text What to say; empty hides the line.
 * @param {string} [kind] "good", "bad", or nothing.
 * @returns {void}
 */
function sayStream(text, kind) {
    elements.streamMessage.textContent = text;
    elements.streamMessage.className = kind ? "message " + kind : "message";
    elements.streamMessage.hidden = !text;
}

/**
 * Run one transport action on whichever side is playing.
 *
 * @param {string} command "toggle", "next", "previous" or "stop".
 * @returns {void}
 */
function transport(command) {
    if (target !== "guest") {
        stream(command);
        return;
    }
    if (command === "toggle") {
        if (elements.player.paused) {
            elements.player.play().catch(() => undefined);
        } else {
            elements.player.pause();
        }
    } else if (command === "next") {
        // Pressed, not ended: repeat-one must not hand back the same song.
        playNextHere(false);
    } else if (command === "previous") {
        if (guestIndex > 0) {
            playHere(queueTracks[guestIndex - 1].video_id);
        }
    } else if (command === "stop") {
        elements.player.pause();
        elements.player.removeAttribute("src");
        elements.player.load();
    }
}

/** Whether a terms dialog is already open. */
let termsDialogOpen = false;

/**
 * Show terms text (app or streaming). Optionally require acceptance.
 *
 * @param {string} kind "streaming" or "app".
 * @param {boolean} [requireAccept] When true, Accept writes acceptance.
 * @returns {Promise<boolean>} Whether accepted (or already accepted / closed).
 */
async function showTerms(kind, requireAccept) {
    const which = kind === "app" ? "app" : "streaming";
    if (termsDialogOpen || !elements.termsDialog) {
        return which === "streaming" ? streamingTermsOk : true;
    }
    termsDialogOpen = true;
    try {
        const answer = await api("/api/terms");
        if (answer.status !== 200) {
            sayStream("Could not load the terms.", "bad");
            return false;
        }
        const data = answer.body;
        const already = which === "streaming" ? data.streaming_accepted : data.app_accepted;
        if (requireAccept && already) {
            if (which === "streaming") {
                streamingTermsOk = true;
            }
            return true;
        }
        elements.termsTitle.textContent = (which === "app"
            ? data.app_title : data.streaming_title) || "Terms";
        elements.termsBody.textContent = (which === "app"
            ? data.app_body : data.streaming_body) || "";
        elements.termsAccept.textContent = requireAccept
            ? (data.accept_label || "Accept") : "Close";
        elements.termsDecline.textContent = data.decline_label || "Decline";
        elements.termsDecline.hidden = !requireAccept;
        if (typeof elements.termsDialog.showModal === "function") {
            elements.termsDialog.showModal();
        } else {
            elements.termsDialog.setAttribute("open", "");
        }
        const accepted = await new Promise((resolve) => {
            const onAccept = async () => {
                cleanup();
                if (!requireAccept) {
                    resolve(true);
                    return;
                }
                try {
                    const result = await api("/api/terms", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({kind: which}),
                    });
                    resolve(result.status === 200 && result.body.ok !== false);
                } catch (error) {
                    resolve(false);
                }
            };
            const onDecline = () => {
                cleanup();
                resolve(false);
            };
            function cleanup() {
                elements.termsAccept.removeEventListener("click", onAccept);
                elements.termsDecline.removeEventListener("click", onDecline);
                elements.termsDecline.hidden = false;
                if (elements.termsDialog.open) {
                    elements.termsDialog.close();
                }
            }
            elements.termsAccept.addEventListener("click", onAccept);
            elements.termsDecline.addEventListener("click", onDecline);
        });
        if (which === "streaming" && requireAccept) {
            streamingTermsOk = !!accepted;
            if (accepted) {
                sayStream("Streaming terms accepted.", "good");
                if (view === "streaming") {
                    pollStream();
                }
            } else {
                sayStream("Streaming terms were declined.", "bad");
            }
        }
        return !!accepted;
    } finally {
        termsDialogOpen = false;
    }
}

/**
 * Show Streaming terms on this device and accept them when confirmed.
 *
 * @returns {Promise<boolean>} Whether terms are accepted afterwards.
 */
async function offerStreamingTerms() {
    if (streamingTermsOk) {
        return true;
    }
    return showTerms("streaming", true);
}

/**
 * Send one Streaming command to the PC.
 *
 * @param {string} command The command name.
 * @param {object} [extra] Additional fields, e.g. {index} or {seconds}.
 * @returns {Promise<void>}
 */
async function stream(command, extra) {
    try {
        const answer = await api("/api/discover", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(Object.assign({command: command}, extra || {})),
        });
        if (answer.status === 403 && answer.body.error === "terms_required") {
            await offerStreamingTerms();
            return;
        }
        if (!answer.body.ok) {
            sayStream((standalone ? "Could not do that (" : "The PC could not do that (")
                + (answer.body.error || answer.status) + ").", "bad");
            return;
        }
        sayStream("");
        if (answer.body.state) {
            renderStream(answer.body.state);
        }
    } catch (error) {
        setConnection(false);
        sayStream(standalone ? "Clipster cannot be reached." : "The PC cannot be reached.", "bad");
    }
}

/**
 * Build one row of the queue.
 *
 * @param {object} track One entry of the Streaming queue.
 * @param {boolean} current Whether this is the track being played.
 * @returns {HTMLLIElement} The row.
 */
function queueRow(track, current) {
    const row = document.createElement("li");
    row.className = current ? "queue-row current" : "queue-row";
    row.dataset.video = track.video_id;

    const badge = document.createElement("span");
    badge.className = "badge" + (current ? " ok" : "");
    badge.textContent = current ? "▶" : String(track.index + 1);
    row.appendChild(badge);

    const body = document.createElement("div");
    body.className = "body";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = track.title;
    body.appendChild(name);
    const facts = [track.uploader, formatDuration(track.duration)].filter(
        (fact) => fact && fact !== "-");
    if (facts.length > 0) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = facts.join(" · ");
        body.appendChild(meta);
    }
    body.addEventListener("click", () => playTrack(track));
    bindShare(body, track.video_id, track.title);
    row.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(button("✕", "Hide from queue", () => {
        unplayableIds[track.video_id] = true;
        stream("hide", {index: track.index});
    }));
    actions.appendChild(button("⬇", "Download", () => stream("download", {index: track.index})));
    row.appendChild(actions);
    return row;
}

/**
 * Play one queued track on whichever side is selected.
 *
 * @param {object} track One entry of the Streaming queue.
 * @returns {void}
 */
function playTrack(track) {
    if (target === "guest") {
        playHere(track.video_id);
        return;
    }
    stream("play", {index: track.index});
}

/**
 * Scroll the queue so the playing track sits in the middle.
 *
 * Only when the track actually changed: in between the user has to be able to
 * scroll around without the list yanking itself back.
 *
 * @param {string} videoId The track that is playing.
 * @returns {void}
 */
function centreQueue(videoId) {
    if (!videoId || videoId === centredOn) {
        return;
    }
    const row = elements.queue.querySelector('[data-video="' + videoId + '"]');
    if (!row) {
        return;                     // the queue has not caught up yet
    }
    centredOn = videoId;
    // Measured from the boxes, not from offsetTop: that is relative to the
    // nearest positioned ancestor, which is not necessarily this list.
    const box = elements.queue.getBoundingClientRect();
    const rowBox = row.getBoundingClientRect();
    const delta = (rowBox.top + rowBox.height / 2) - (box.top + box.height / 2);
    elements.queue.scrollTop = Math.max(0, elements.queue.scrollTop + delta);
}

/**
 * Show one Streaming state.
 *
 * @param {object} state The answer of /api/discover.
 * @returns {void}
 */
function renderStream(state) {
    if (typeof state.terms_accepted === "boolean") {
        streamingTermsOk = state.terms_accepted;
    }
    if (state.playback_source) {
        playbackSource = state.playback_source;
    }
    if (state.playback_modes) {
        playbackModes = state.playback_modes;
    }
    if (typeof state.visualizer === "string") {
        const wanted = STAGE_MODES.indexOf(state.visualizer) >= 0 ? state.visualizer : "pulse";
        if (wanted !== stageMode) {
            stageMode = wanted;
            // Switched to a mode that measures sound while something is already
            // playing: attach the analyser now rather than at the next song.
            if (target === "guest" && !elements.player.paused) {
                ensureAnalyser();
            }
            syncStage();
        }
    }
    if (typeof state.play_video === "boolean") {
        wantVideo = state.play_video;
    }
    if (Array.isArray(state.bands)) {
        remoteBands = state.bands;
    }
    remoteLevel = Number(state.level || 0);
    paintPlaybackModes();
    if (!state.available) {
        elements.streamTitle.textContent = standalone
            ? "Streaming is starting…"
            : "Streaming is not available on the PC.";
        return;
    }
    if (!state.terms_accepted) {
        sayStream(standalone
            ? "Accept the Streaming terms to search and play."
            : "Streaming needs its terms of use accepted once on the PC.", "bad");
        if (standalone) {
            offerStreamingTerms();
        }
    }

    const tracks = state.tracks || [];
    queueTracks = tracks;
    syncStreamingLayout(tracks.length > 0 || Boolean(state.busy));

    let current = state.current;
    if (target === "guest") {
        // In guest mode the PC is stopped, so its "current" says nothing.
        const mine = tracks.find((track) => track.video_id === guestVideoId);
        current = mine || current;
    }
    elements.streamTitle.textContent = current ? current.title : "Nothing yet.";
    elements.streamUploader.textContent = current ? (current.uploader || "") : "";
    const localPlaying = target === "guest" && !elements.player.paused && !elements.player.ended;
    elements.streamToggle.textContent = (target === "guest" ? localPlaying : state.playing) ? "⏸" : "▶";

    let duration = state.duration || (current ? current.duration : 0) || 0;
    let position = state.position || 0;
    if (target === "guest") {
        duration = elements.player.duration || (current ? current.duration : 0) || 0;
        position = elements.player.currentTime || 0;
    }
    const percent = duration > 0 ? Math.max(0, Math.min(100, (position / duration) * 100)) : 0;
    elements.streamFill.style.width = percent + "%";
    elements.streamTime.textContent = formatDuration(position) + " / " + formatDuration(duration);
    streamDuration = duration;
    elements.streamTrack.style.cursor = state.can_seek ? "pointer" : "default";
    elements.streamLevel.style.width = Math.max(0, Math.min(100, (state.level || 0) * 100)) + "%";

    if (typeof state.search_delay_ms === "number" && state.search_delay_ms > 0) {
        searchDelay = state.search_delay_ms;
    }
    if (state.volume_controllable !== undefined) {
        elements.volume.dataset.controllable = state.volume_controllable ? "yes" : "no";
    }
    if (target === "host" && typeof state.volume === "number" && document.activeElement !== elements.volume) {
        // Not while it is being dragged, or the slider would fight the user.
        elements.volume.value = String(state.volume);
        elements.volumeValue.textContent = String(state.volume);
    }
    updateVolumeRow();

    if (guestVideoId) {
        // Recovered from the id, not remembered as a number: the queue shifts
        // whenever something is inserted in front of the current track.
        guestIndex = tracks.findIndex((track) => track.video_id === guestVideoId);
        // Dislike (or refresh) removed the song this device was on — continue.
        if (guestIndex < 0 && tracks.length > 0) {
            const fallback = tracks[Math.max(0, Math.min(state.index, tracks.length - 1))];
            if (fallback) {
                playHere(fallback.video_id);
            }
        } else if (guestIndex < 0) {
            guestVideoId = "";
            try {
                elements.player.pause();
                elements.player.removeAttribute("src");
            } catch (error) {
                // ignore
            }
        }
    }
    const signature = JSON.stringify([tracks.map((t) => t.video_id), state.index, guestVideoId]);
    if (signature !== lastQueue) {
        lastQueue = signature;
        elements.queue.textContent = "";
        const currentId = target === "guest"
            ? guestVideoId
            : (tracks[state.index] || {}).video_id || "";
        tracks.forEach((track) => elements.queue.appendChild(
            queueRow(track, track.video_id === currentId)));
        elements.queueEmpty.hidden = tracks.length > 0;
    }
    elements.streamRefresh.textContent = state.busy ? "Searching..." : "Find similar";
    elements.streamRefresh.disabled = Boolean(state.busy);

    const playingId = target === "guest"
        ? guestVideoId
        : (tracks[state.index] || {}).video_id || "";
    // Always refresh the red marker — even when the list DOM was not rebuilt.
    markQueueCurrent(playingId);
    // Rebuild per-id votes: never reuse the previous title's thumbs on a new one.
    voteById = {};
    (state.votes || []).forEach((entry) => {
        if (entry && entry.video_id) {
            voteById[entry.video_id] = entry.vote === "up" || entry.vote === "down" ? entry.vote : "";
        }
    });
    tracks.forEach((track) => {
        if (track && track.video_id) {
            voteById[track.video_id] = track.vote === "up" || track.vote === "down" ? track.vote : "";
        }
    });
    syncVoteDisplay(playingId);
    centreQueue(playingId);
    renderVotes(state.votes || []);

    // First songs just arrived — start playback when nothing is on yet.
    if (tracks.length > 0 && !hadQueueTracks) {
        if (target === "guest" && !guestVideoId) {
            playHere(tracks[0].video_id);
        } else if (target === "host" && !state.playing && state.index < 0) {
            stream("play", {index: 0});
        }
    }
    hadQueueTracks = tracks.length > 0;
}

/**
 * Show only Search YouTube until the queue has something to play.
 *
 * @param {boolean} hasTracks Whether any songs are queued.
 * @returns {void}
 */
function syncStreamingLayout(hasTracks) {
    if (elements.streamEmptyHint) {
        elements.streamEmptyHint.hidden = hasTracks;
    }
    if (elements.nowPlaying) {
        elements.nowPlaying.hidden = !hasTracks;
    }
    if (elements.queueCard) {
        elements.queueCard.hidden = !hasTracks;
    }
    // Play on is remote-only; Android hides it entirely.
    if (elements.targetCard) {
        elements.targetCard.hidden = standalone || !hasTracks;
    }
}

/**
 * Highlight like / dislike to match the vote stored for the playing track.
 *
 * @param {string} vote ``up``, ``down``, or empty.
 * @returns {void}
 */
function updateVoteButtons(vote) {
    if (elements.streamLike) {
        elements.streamLike.classList.toggle("voted", vote === "up");
    }
    if (elements.streamDislike) {
        elements.streamDislike.classList.toggle("voted", vote === "down");
    }
}

/**
 * Resolve the stored vote for one YouTube id (empty when unknown / unrated).
 *
 * @param {string} videoId The current track.
 * @returns {string} ``up``, ``down``, or ``""``.
 */
function voteForId(videoId) {
    if (!videoId) {
        return "";
    }
    if (Object.prototype.hasOwnProperty.call(voteById, videoId)) {
        return voteById[videoId] || "";
    }
    const track = queueTracks.find((item) => item.video_id === videoId);
    return track && track.vote ? track.vote : "";
}

/**
 * Refresh 👍/👎 for the track that is actually playing now.
 *
 * @param {string} videoId The current YouTube id.
 * @returns {void}
 */
function syncVoteDisplay(videoId) {
    ratedVideoId = videoId || "";
    const vote = voteForId(ratedVideoId);
    updateVoteButtons(vote);
    lastVote = vote;
}

/**
 * Show every stored thumbs-up / thumbs-down with play and clear actions.
 *
 * @param {Array<object>} votes Rows from ``/api/discover``.
 * @returns {void}
 */
function renderVotes(votes) {
    if (!elements.votes || !elements.votesCard) {
        return;
    }
    const rows = Array.isArray(votes) ? votes : [];
    elements.votesCard.hidden = rows.length === 0;
    if (elements.votesEmpty) {
        elements.votesEmpty.hidden = rows.length > 0;
    }
    elements.votes.innerHTML = "";
    rows.forEach((entry) => {
        const row = document.createElement("li");
        row.className = "queue-row vote-row";
        row.dataset.video = entry.video_id || "";

        const badge = document.createElement("span");
        badge.className = "badge " + (entry.vote === "down" ? "failed" : "ok");
        badge.textContent = entry.vote === "down" ? "👎" : "👍";

        const body = document.createElement("div");
        body.className = "body";
        const name = document.createElement("div");
        name.className = "name";
        name.textContent = entry.title || entry.video_id || "";
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = entry.uploader || "";
        body.appendChild(name);
        body.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "row";
        const playBtn = document.createElement("button");
        playBtn.type = "button";
        playBtn.className = "icon";
        playBtn.title = "Play";
        playBtn.setAttribute("aria-label", "Play");
        playBtn.textContent = "▶";
        playBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            playVote(entry);
        });
        const clearBtn = document.createElement("button");
        clearBtn.type = "button";
        clearBtn.className = "icon";
        clearBtn.title = "Clear rating";
        clearBtn.setAttribute("aria-label", "Clear rating");
        clearBtn.textContent = "✕";
        clearBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            stream("clear_vote", {video_id: entry.video_id});
        });
        actions.appendChild(playBtn);
        actions.appendChild(clearBtn);

        row.appendChild(badge);
        row.appendChild(body);
        row.appendChild(actions);
        elements.votes.appendChild(row);
    });
}

/**
 * Play a rated track: enqueue on the host, or play locally in guest mode.
 *
 * @param {object} entry Vote row with ``video_id`` / ``title`` / ``uploader``.
 * @returns {Promise<void>}
 */
async function playVote(entry) {
    const videoId = entry && entry.video_id;
    if (!videoId) {
        return;
    }
    const inQueue = queueTracks.find((track) => track.video_id === videoId);
    if (target === "guest") {
        if (inQueue) {
            playHere(videoId);
            return;
        }
        try {
            const answer = await api("/api/discover/queue", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    video_id: videoId,
                    title: entry.title || videoId,
                    uploader: entry.uploader || "",
                    duration: 0,
                    play: false,
                }),
            });
            if (answer.body && answer.body.state) {
                renderStream(answer.body.state);
            }
            playHere(videoId);
        } catch (error) {
            sayStream(standalone ? "Clipster cannot be reached." : "The PC cannot be reached.", "bad");
        }
        return;
    }
    if (inQueue) {
        stream("play", {index: inQueue.index});
        return;
    }
    try {
        const answer = await api("/api/discover/queue", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                video_id: videoId,
                title: entry.title || videoId,
                uploader: entry.uploader || "",
                duration: 0,
                play: true,
            }),
        });
        if (answer.body && answer.body.state) {
            renderStream(answer.body.state);
        }
    } catch (error) {
        sayStream(standalone ? "Clipster cannot be reached." : "The PC cannot be reached.", "bad");
    }
}

/**
 * Ask the PC what Streaming is doing.
 *
 * @returns {Promise<void>}
 */
async function pollStream() {
    try {
        const answer = await api("/api/discover");
        if (answer.status === 200) {
            renderStream(answer.body);
        }
    } catch (error) {
        setConnection(false);
    }
}

// ---------------------------------------------------------------- searching
/**
 * Build one row of the search results.
 *
 * @param {object} found One result from /api/discover/search.
 * @returns {HTMLLIElement} The row.
 */
function resultRow(found) {
    const row = document.createElement("li");

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "♪";
    row.appendChild(badge);

    const body = document.createElement("div");
    body.className = "body";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = found.title;
    body.appendChild(name);
    const facts = [found.uploader, formatDuration(found.duration)].filter(
        (fact) => fact && fact !== "-");
    if (facts.length > 0) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = facts.join(" · ");
        body.appendChild(meta);
    }
    body.addEventListener("click", () => pick(found));
    row.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(button("＋", "Add and play", () => pick(found), "result-add"));
    row.appendChild(actions);
    return row;
}

/**
 * Fold the result list away, or open it again.
 *
 * @param {boolean} [open] Force a state; toggles when omitted.
 * @returns {void}
 */
function showResults(open) {
    const wanted = open === undefined ? elements.results.hidden : open;
    elements.results.hidden = !wanted;
    elements.resultsToggle.textContent = wanted ? "▲ Hide" : "▼ Show";
    elements.resultsToggle.setAttribute("aria-expanded", wanted ? "true" : "false");
}

/**
 * Search YouTube for whatever is in the box.
 *
 * @returns {Promise<void>}
 */
async function runSearch() {
    const query = elements.search.value.trim();
    elements.results.textContent = "";
    if (!query) {
        elements.searchNote.textContent = "";
        elements.resultsToggle.hidden = true;
        return;
    }
    elements.searchNote.textContent = "Searching for “" + query + "”...";
    try {
        const answer = await api("/api/discover/search", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({query: query}),
        });
        if (answer.status === 403) {
            elements.searchNote.textContent = standalone
                ? "Accept the Streaming terms to search."
                : "Accept the Streaming terms once on the PC.";
            await offerStreamingTerms();
            return;
        }
        const results = answer.body.results || [];
        if (!answer.body.ok) {
            elements.searchNote.textContent = standalone
                ? "The search failed."
                : "The search failed on the PC.";
            return;
        }
        elements.searchNote.textContent = results.length
            ? results.length + " results — tap one to play it"
            : "Nothing found.";
        results.forEach((found) => elements.results.appendChild(resultRow(found)));
        elements.resultsToggle.hidden = results.length === 0;
        showResults(results.length > 0);
    } catch (error) {
        setConnection(false);
        elements.searchNote.textContent = standalone
            ? "Clipster cannot be reached."
            : "The PC cannot be reached.";
    }
}

/**
 * Restart the idle timer after every keystroke.
 *
 * Only the last one searches: typing "beatles" would otherwise cost seven
 * searches, and the PC would answer them all.
 *
 * @returns {void}
 */
function scheduleSearch() {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(runSearch, searchDelay);
}

/**
 * Add a search result to the queue and start it.
 *
 * @param {object} found One result from the search.
 * @returns {Promise<void>}
 */
async function pick(found) {
    // Started before any await: a phone only allows playback while the tap is
    // still "live", and awaiting the PC first loses that permission - which is
    // why playing on the device did nothing at all.
    if (target === "guest") {
        playHere(found.video_id);
    }
    elements.searchNote.textContent = "Adding “" + found.title + "”...";
    try {
        const answer = await api("/api/discover/queue", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                video_id: found.video_id,
                title: found.title,
                uploader: found.uploader,
                duration: found.duration,
                // In guest mode the PC only queues it; this device plays it.
                play: target === "host",
            }),
        });
        if (!answer.body.ok) {
            elements.searchNote.textContent = answer.status === 403
                ? (standalone
                    ? "Accept the Streaming terms to play."
                    : "Accept the Streaming terms once on the PC.")
                : (standalone ? "That track was not accepted." : "The PC did not accept that track.");
            if (answer.status === 403) {
                await offerStreamingTerms();
            }
            return;
        }
        elements.searchNote.textContent = "Added: " + found.title;
        if (answer.body.state) {
            renderStream(answer.body.state);
        }
        // A freshly picked seed deserves a clean skip list.
        delete unplayableIds[found.video_id];
    } catch (error) {
        setConnection(false);
        elements.searchNote.textContent = "The PC cannot be reached.";
    }
}

// ------------------------------------------------------- where it plays
/**
 * Switch between playing on the PC and playing on this device.
 *
 * @param {string} name "host" or "guest".
 * @returns {Promise<void>}
 */
async function setTarget(name) {
    target = name;
    const guest = name === "guest";
    elements.targetNote.textContent = guest
        ? "The sound comes out of this device — useful when it is paired with a speaker."
        : "The sound comes out of the PC.";
    elements.player.hidden = !guest;
    if (guest) {
        // Both at once would be two songs over each other.
        await stream("stop");
    } else {
        elements.player.pause();
        guestVideoId = "";
        guestIndex = -1;
    }
    updateVolumeRow();
}

/**
 * Play one track on this device, relayed by the PC.
 *
 * @param {string} videoId The video id to play.
 * @returns {void}
 */
/**
 * Whether what a media element is playing came from the Streaming queue.
 *
 * The same audio element also plays a finished download straight from the
 * list - that must not advance a queue the user is not even looking at. Queue
 * playback is "/stream/" for a song from YouTube and "/queue/" for one that is
 * already on disk; a download plays from "/media/".
 *
 * @param {HTMLMediaElement} element The element to look at.
 * @returns {boolean} Whether the queue is what is playing.
 */
function fromQueue(element) {
    const source = element.currentSrc || "";
    return source.indexOf("/stream/") !== -1 || source.indexOf("/queue/") !== -1;
}

/**
 * Return where the audio for one queue entry comes from.
 *
 * A song that is already in the download folder plays straight off the disk of
 * the machine running Clipster - on the phone that is the phone itself, so it
 * costs no data at all and works with the radio switched off. Only what is not
 * there yet goes through the YouTube relay.
 *
 * @param {string} videoId The YouTube id, when the track has one.
 * @param {number} index Queue position, used for local files.
 * @returns {string} The URL to hand to the audio element.
 */
function trackSource(videoId, index) {
    const track = index >= 0 ? queueTracks[index] : null;
    if (track && track.local) {
        return "/queue/" + encodeURIComponent(String(index));
    }
    return "/stream/" + encodeURIComponent(videoId);
}

// ------------------------------------------------------------------- stage
/**
 * The stage: the same seven modes the desktop offers, drawn on a canvas.
 *
 * Where the numbers come from depends on who is making the sound. Playing on
 * this device, the Web Audio API measures the audio element directly - real
 * bands and a real waveform, same-origin so no CORS problem. Playing on the PC,
 * there is nothing here to measure, so the backend's own analysis arrives with
 * the state poll as `bands` and `level`.
 */
const STAGE_MODES = ["off", "text", "waveform", "cover", "pulse", "spectrum", "visualizer"];

/** Bars the spectrum draws - matches EQ_BAR_COUNT on the Python side. */
const STAGE_BARS = 12;

/** Which mode the stage is in, as the backend reports it. */
let stageMode = "pulse";

/** Web Audio pieces, built once on the first user gesture that starts audio. */
let audioContext = null;
let audioAnalyser = null;
let audioBins = null;
let audioWave = null;

/** Handle of the draw loop while the stage animates. */
let stageFrame = 0;

/** Bands and loudness the PC reported, used when it is the one playing. */
let remoteBands = [];
let remoteLevel = 0;

/**
 * Attach an analyser to the audio element, once.
 *
 * Must happen after a user gesture or the browser keeps the context suspended.
 * Failure is not fatal: the stage falls back to what the PC reports.
 *
 * @returns {void}
 */
function ensureAnalyser() {
    // Routing the element through an AudioContext is not free: from then on its
    // sound only reaches the speaker through the graph, and a suspended context
    // means silence. So it is only done for the modes that genuinely measure
    // audio, and never for a stage that is off or only shows text.
    if (!needsAudioData()) {
        resumeAudio();
        return;
    }
    if (audioAnalyser || !window.AudioContext) {
        resumeAudio();
        return;
    }
    try {
        audioContext = new AudioContext();
        const source = audioContext.createMediaElementSource(elements.player);
        audioAnalyser = audioContext.createAnalyser();
        audioAnalyser.fftSize = 1024;
        audioAnalyser.smoothingTimeConstant = 0.75;
        audioBins = new Uint8Array(audioAnalyser.frequencyBinCount);
        audioWave = new Uint8Array(audioAnalyser.fftSize);
        // On to the speaker as well, or playback goes silent.
        source.connect(audioAnalyser);
        audioAnalyser.connect(audioContext.destination);
    } catch (error) {
        audioAnalyser = null;
    }
    resumeAudio();
}

/**
 * Whether the current stage mode needs the audio measured at all.
 *
 * @returns {boolean} True for the modes driven by real sound.
 */
function needsAudioData() {
    return stageMode === "spectrum" || stageMode === "waveform"
        || stageMode === "pulse" || stageMode === "visualizer";
}

/**
 * Wake the audio graph if a browser suspended it.
 *
 * Once the element plays through a context, a suspended context is silence -
 * so this is called on every start and whenever playback actually begins.
 *
 * @returns {void}
 */
function resumeAudio() {
    if (audioContext && audioContext.state === "suspended") {
        audioContext.resume().catch(() => undefined);
    }
}

/**
 * Return the current spectrum as STAGE_BARS values in [0, 1].
 *
 * @returns {number[]} One value per bar.
 */
function stageBands() {
    if (audioAnalyser && target === "guest") {
        audioAnalyser.getByteFrequencyData(audioBins);
        const bars = [];
        // Logarithmic grouping: even bins would put ten bars on the hi-hats.
        for (let bar = 0; bar < STAGE_BARS; bar += 1) {
            const from = Math.floor(Math.pow(bar / STAGE_BARS, 2) * audioBins.length);
            const to = Math.max(from + 1,
                Math.floor(Math.pow((bar + 1) / STAGE_BARS, 2) * audioBins.length));
            let sum = 0;
            for (let bin = from; bin < to && bin < audioBins.length; bin += 1) {
                sum += audioBins[bin];
            }
            bars.push(Math.min(1, (sum / (to - from)) / 200));
        }
        return bars;
    }
    if (remoteBands.length > 0) {
        return remoteBands.slice(0, STAGE_BARS);
    }
    return new Array(STAGE_BARS).fill(0);
}

/**
 * Return the current loudness in [0, 1].
 *
 * @returns {number} How loud it is right now.
 */
function stageLevel() {
    if (audioAnalyser && target === "guest") {
        audioAnalyser.getByteTimeDomainData(audioWave);
        let sum = 0;
        for (let index = 0; index < audioWave.length; index += 1) {
            const value = (audioWave[index] - 128) / 128;
            sum += value * value;
        }
        return Math.min(1, Math.sqrt(sum / audioWave.length) * 2.2);
    }
    return Math.min(1, Math.max(0, remoteLevel));
}

/**
 * Draw one frame of the stage and ask for the next.
 *
 * @returns {void}
 */
function drawStage() {
    stageFrame = 0;
    const canvas = elements.stage;
    if (!canvas || canvas.hidden) {
        return;
    }
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== Math.round(width * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
    }
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--accent").trim() || "#e0393e";
    const muted = styles.getPropertyValue("--muted").trim() || "#8b8f98";

    if (stageMode === "spectrum" || stageMode === "visualizer") {
        drawBars(context, width, height, accent,
                 stageMode === "visualizer" ? 48 : STAGE_BARS);
    } else if (stageMode === "waveform") {
        drawWave(context, width, height, accent);
    } else if (stageMode === "pulse") {
        drawPulse(context, width, height, accent);
    } else if (stageMode === "text" || stageMode === "cover") {
        drawStageText(context, width, height, muted);
    }
    stageFrame = window.requestAnimationFrame(drawStage);
}

/**
 * Draw the spectrum, or the denser mountain the visualizer mode wants.
 *
 * @param {CanvasRenderingContext2D} context Where to draw.
 * @param {number} width Canvas width in CSS pixels.
 * @param {number} height Canvas height in CSS pixels.
 * @param {string} colour The accent colour.
 * @param {number} count How many bars to draw.
 * @returns {void}
 */
function drawBars(context, width, height, colour, count) {
    const bands = stageBands();
    /**
     * Clamp one band value into [0, 1], treating anything odd as silence.
     *
     * @param {number} value The reported band level.
     * @returns {number} A drawable value.
     */
    const level = (value) => {
        const number = Number(value);
        return Number.isFinite(number) ? Math.min(1, Math.max(0, number)) : 0;
    };
    const gap = count > 20 ? 1 : 3;
    const barWidth = Math.max(1, (width - gap * (count - 1)) / count);
    context.fillStyle = colour;
    for (let index = 0; index < count; index += 1) {
        // More bars than the analyser gives: interpolate rather than repeat.
        const at = (index / count) * bands.length;
        const low = level(bands[Math.floor(at)]);
        const high = level(bands[Math.min(bands.length - 1, Math.ceil(at))]);
        const value = low + (high - low) * (at - Math.floor(at));
        // Clamped when drawn, not only where the numbers come from: a bar taller
        // than the stage is drawn from a negative y and spills over the card.
        const barHeight = Math.min(height - 2, Math.max(2, value * (height - 6)));
        context.fillRect(index * (barWidth + gap), height - barHeight, barWidth, barHeight);
    }
}

/**
 * Draw the oscilloscope line.
 *
 * @param {CanvasRenderingContext2D} context Where to draw.
 * @param {number} width Canvas width in CSS pixels.
 * @param {number} height Canvas height in CSS pixels.
 * @param {string} colour The accent colour.
 * @returns {void}
 */
function drawWave(context, width, height, colour) {
    const middle = height / 2;
    context.strokeStyle = colour;
    context.lineWidth = 2;
    context.beginPath();
    if (audioAnalyser && target === "guest") {
        audioAnalyser.getByteTimeDomainData(audioWave);
        const step = Math.max(1, Math.floor(audioWave.length / width));
        for (let x = 0, index = 0; x < width; x += 1, index += step) {
            const value = ((audioWave[index] || 128) - 128) / 128;
            const y = middle - value * (middle - 4);
            if (x === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        }
    } else {
        // No audio here to measure: a level-driven line, like the PC's fallback.
        const level = stageLevel();
        for (let x = 0; x < width; x += 1) {
            const phase = (x / width) * Math.PI * 6 + Date.now() / 200;
            const y = middle - Math.sin(phase) * level * (middle - 4);
            if (x === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        }
    }
    context.stroke();
}

/**
 * Draw the beat ring, the desktop's default stage.
 *
 * @param {CanvasRenderingContext2D} context Where to draw.
 * @param {number} width Canvas width in CSS pixels.
 * @param {number} height Canvas height in CSS pixels.
 * @param {string} colour The accent colour.
 * @returns {void}
 */
function drawPulse(context, width, height, colour) {
    const level = stageLevel();
    const middleX = width / 2;
    const middleY = height / 2;
    const base = Math.min(width, height) * 0.18;
    const radius = base + level * base * 1.6;
    context.strokeStyle = colour;
    context.lineWidth = 2 + level * 4;
    context.globalAlpha = 0.35 + level * 0.65;
    context.beginPath();
    context.arc(middleX, middleY, Math.max(2, radius), 0, Math.PI * 2);
    context.stroke();
    context.globalAlpha = 1;
}

/**
 * Write the title on the stage, for the text and cover modes.
 *
 * @param {CanvasRenderingContext2D} context Where to draw.
 * @param {number} width Canvas width in CSS pixels.
 * @param {number} height Canvas height in CSS pixels.
 * @param {string} colour The muted colour.
 * @returns {void}
 */
function drawStageText(context, width, height, colour) {
    context.fillStyle = colour;
    context.font = "600 14px system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const text = elements.streamTitle.textContent || "";
    context.fillText(text.slice(0, 48), width / 2, height / 2);
}

/**
 * Show or hide the stage and keep its draw loop in step with the mode.
 *
 * @returns {void}
 */
function syncStage() {
    const canvas = elements.stage;
    if (!canvas) {
        return;
    }
    // No stage when it is switched off, and none behind a video - the picture
    // is the stage then.
    canvas.hidden = stageMode === "off" || !elements.streamVideo.hidden;
    if (canvas.hidden) {
        window.cancelAnimationFrame(stageFrame);
        stageFrame = 0;
        return;
    }
    if (!stageFrame) {
        drawStage();
    }
}

// --------------------------------------------------- how the queue is played
/** Shuffle, repeat and the sleep timer, as the backend reports them. */
let playbackModes = {shuffle: false, repeat: "off", sleep_minutes: 0};

/** The order the repeat button steps through, matching the desktop. */
const repeatOrder = ["off", "all", "one"];

/** Whether the sleep timer was still running at the previous poll. */
let sleepWasRunning = false;

/** Whether the settings ask for video rather than audio only. */
let wantVideo = false;

/**
 * Return the repeat mode after this one.
 *
 * @param {string} mode The current mode.
 * @returns {string} The next one in the cycle.
 */
function nextRepeat(mode) {
    const at = repeatOrder.indexOf(mode);
    return repeatOrder[(at < 0 ? 0 : at + 1) % repeatOrder.length];
}

/**
 * Save one playback mode and show the answer straight away.
 *
 * Saved rather than commanded: these are settings on the desktop too, so a
 * phone that turns shuffle on finds it on next time.
 *
 * @param {object} body The setting keys to change.
 * @returns {Promise<void>}
 */
async function saveMode(body) {
    try {
        const answer = await api("/api/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        if (answer.status === 200) {
            playbackModes.shuffle = !!answer.body.discover_shuffle;
            playbackModes.repeat = answer.body.discover_repeat || "off";
            paintPlaybackModes();
        }
    } catch (error) {
        setConnection(false);
    }
}

/**
 * Show the state of shuffle, repeat, the sleep timer and the source rule.
 *
 * @returns {void}
 */
function paintPlaybackModes() {
    const words = mobileWords();
    // The backend's own player is silent on a phone - the sound comes out of
    // this page - so the sleep timer has to be honoured here as well.
    const running = Number(playbackModes.sleep_minutes || 0);
    if (sleepWasRunning && running === 0 && target === "guest") {
        elements.player.pause();
        sayStream(/^de\b/i.test(navigator.language || "")
            ? "Einschlaftimer abgelaufen — Wiedergabe gestoppt."
            : "Sleep timer reached — playback stopped.", "");
    }
    sleepWasRunning = running > 0;
    elements.streamShuffle.classList.toggle("on", playbackModes.shuffle);
    elements.streamRepeat.textContent = playbackModes.repeat === "one" ? "🔂" : "🔁";
    elements.streamRepeat.classList.toggle("on", playbackModes.repeat !== "off");
    if (document.activeElement !== elements.streamSleep) {
        elements.streamSleep.value = String(playbackModes.sleep_minutes || 0);
    }
    const notes = [];
    if (playbackSource.local_only) {
        notes.push(words.switched);
    }
    if (playbackModes.sleep_minutes > 0) {
        notes.push(/^de\b/i.test(navigator.language || "")
            ? "Stopp in " + playbackModes.sleep_minutes + " Min"
            : "Stops in " + playbackModes.sleep_minutes + " min");
    }
    elements.playbackNote.textContent = notes.join(" · ");
}

// ---------------------------------------------------------------- sharing
/** How long a press has to last before it counts as "share this", in ms. */
const SHARE_PRESS_MS = 500;

/** Words the share and scan dialogs need, per language. */
const shareWords = {
    en: {
        title: "Share",
        hint: "Let the other person scan this with Clipster.",
        missing: "No code could be drawn — the qrcode package is missing on the PC.",
        noId: "That song came from a file, so there is nothing to share.",
        copied: "Link copied.",
        scanHint: "Hold the code in front of the camera.",
        scanNoCamera: "This device has no camera Clipster may use.",
        scanInsecure: "Scanning only works in the Clipster app, not over the network address.",
        scanDenied: "Without the camera there is nothing to scan.",
        scanUnknown: "That code is not a YouTube link.",
        scanQueued: "Added to the queue.",
    },
    de: {
        title: "Teilen",
        hint: "Der andere scannt das hier mit Clipster.",
        missing: "Kein Code möglich — auf dem PC fehlt das Paket qrcode.",
        noId: "Dieser Titel kommt aus einer Datei, da gibt es nichts zu teilen.",
        copied: "Link kopiert.",
        scanHint: "Den Code vor die Kamera halten.",
        scanNoCamera: "Dieses Gerät hat keine Kamera, die Clipster benutzen darf.",
        scanInsecure: "Scannen geht nur in der Clipster-App, nicht über die Netzwerkadresse.",
        scanDenied: "Ohne Kamera gibt es nichts zu scannen.",
        scanUnknown: "Dieser Code ist kein YouTube-Link.",
        scanQueued: "In die Warteschlange übernommen.",
    },
};

/**
 * Return the share wording for the phone's language.
 *
 * @returns {object} One entry of {@link shareWords}.
 */
function shareText() {
    return /^de\b/i.test(navigator.language || "") ? shareWords.de : shareWords.en;
}

/**
 * Pick the YouTube id out of a stored download URL.
 *
 * Only used to decide whether a row has anything to share; anything actually
 * scanned goes to the PC, which owns the real pattern.
 *
 * @param {string} url The URL the download came from.
 * @returns {string} The eleven character id, or an empty string.
 */
function videoIdOf(url) {
    const match = /(?:v=|youtu\.be\/|\/shorts\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})/.exec(
        String(url || ""));
    return match ? match[1] : "";
}

/**
 * Make a long press on one element offer the share code.
 *
 * A phone has no right mouse button, so the gesture is a press that lasts.
 * Moving the finger cancels it, otherwise scrolling the queue would keep
 * opening dialogs.
 *
 * @param {HTMLElement} element The element to watch.
 * @param {string} videoId The YouTube id of the song it shows.
 * @param {string} title Its title, shown above the code.
 * @returns {void}
 */
function bindShare(element, videoId, title) {
    let timer = 0;
    let fired = false;

    const start = () => {
        fired = false;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
            fired = true;
            showShare(videoId, title);
        }, SHARE_PRESS_MS);
    };
    const cancel = () => window.clearTimeout(timer);

    element.addEventListener("touchstart", start, {passive: true});
    element.addEventListener("touchend", cancel);
    element.addEventListener("touchmove", cancel, {passive: true});
    element.addEventListener("touchcancel", cancel);
    // A desktop browser pointed at the same page gets the familiar gesture.
    element.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        showShare(videoId, title);
    });
    // The press already did the work; the release must not also start playback.
    element.addEventListener("click", (event) => {
        if (fired) {
            event.stopPropagation();
            event.preventDefault();
            fired = false;
        }
    }, true);
}

/**
 * Show the QR code for one song.
 *
 * @param {string} videoId The YouTube id.
 * @param {string} title The song title.
 * @returns {void}
 */
function showShare(videoId, title) {
    const words = shareText();
    if (!videoId) {
        sayStream(words.noId, "");
        return;
    }
    const link = "https://www.youtube.com/watch?v=" + encodeURIComponent(videoId);
    elements.shareTitle.textContent = title || words.title;
    elements.shareHint.textContent = words.hint;
    elements.shareLink.textContent = link;
    elements.shareCode.alt = words.title;
    elements.shareCode.onerror = () => {
        elements.shareCode.removeAttribute("src");
        elements.shareHint.textContent = words.missing;
    };
    elements.shareCode.src = "/api/qr?v=" + encodeURIComponent(videoId);
    openDialog(elements.shareDialog);
}

/**
 * Put the shared link on the clipboard, for people with no camera to hand.
 *
 * @returns {Promise<void>}
 */
async function copyShareLink() {
    const link = elements.shareLink.textContent || "";
    if (!link) {
        return;
    }
    try {
        await navigator.clipboard.writeText(link);
        elements.shareHint.textContent = shareText().copied;
    } catch (error) {
        // Clipboard access can be refused; the link is on screen to read.
        elements.shareHint.textContent = link;
    }
}

// ---------------------------------------------------------------- scanning
/** The running camera stream, so it can be stopped again. */
let scanStream = null;

/** Handle of the frame loop while the scanner is open. */
let scanTimer = 0;

/**
 * Whether this page is allowed to open a camera at all.
 *
 * Browsers only hand out getUserMedia in a secure context. The Clipster app on
 * Android loads http://127.0.0.1, which counts as one; the same page opened
 * over a LAN address from another device does not, and no amount of asking
 * changes that - so the button stays hidden there rather than failing later.
 *
 * @returns {boolean} Whether scanning can work here.
 */
function canScan() {
    return Boolean(window.isSecureContext
        && navigator.mediaDevices
        && navigator.mediaDevices.getUserMedia
        && window.jsQR);
}

/**
 * Open the camera and look for a code until one is found.
 *
 * @returns {Promise<void>}
 */
async function startScan() {
    const words = shareText();
    if (!window.isSecureContext) {
        sayStream(words.scanInsecure, "bad");
        return;
    }
    if (!canScan()) {
        sayStream(words.scanNoCamera, "bad");
        return;
    }
    elements.scanHint.textContent = words.scanHint;
    openDialog(elements.scanDialog);
    try {
        scanStream = await navigator.mediaDevices.getUserMedia({
            video: {facingMode: "environment"},
            audio: false,
        });
    } catch (error) {
        closeDialog(elements.scanDialog);
        sayStream(words.scanDenied, "bad");
        return;
    }
    elements.scanVideo.srcObject = scanStream;
    try {
        await elements.scanVideo.play();
    } catch (error) {
        // Some browsers want a gesture; the dialog itself was one.
    }
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", {willReadFrequently: true});

    const look = () => {
        if (!scanStream) {
            return;
        }
        const width = elements.scanVideo.videoWidth;
        const height = elements.scanVideo.videoHeight;
        if (width > 0 && height > 0) {
            canvas.width = width;
            canvas.height = height;
            context.drawImage(elements.scanVideo, 0, 0, width, height);
            const frame = context.getImageData(0, 0, width, height);
            const found = window.jsQR(frame.data, width, height,
                                     {inversionAttempts: "dontInvert"});
            if (found && found.data) {
                stopScan();
                handleScanned(found.data);
                return;
            }
        }
        scanTimer = window.setTimeout(look, 120);
    };
    look();
}

/**
 * Stop the camera and close the scanner.
 *
 * @returns {void}
 */
function stopScan() {
    window.clearTimeout(scanTimer);
    scanTimer = 0;
    if (scanStream) {
        scanStream.getTracks().forEach((track) => track.stop());
        scanStream = null;
    }
    elements.scanVideo.srcObject = null;
    closeDialog(elements.scanDialog);
}

/**
 * Send a decoded code to the PC, which turns it into a queue entry.
 *
 * The link is parsed on the Python side so the pattern that recognises a
 * YouTube URL is maintained once, not once here and once there.
 *
 * @param {string} text Whatever the camera read.
 * @returns {Promise<void>}
 */
async function handleScanned(text) {
    const words = shareText();
    try {
        const answer = await api("/api/scan", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({text: text}),
        });
        if (answer.status === 403 && answer.body.error === "terms_required") {
            await offerStreamingTerms();
            return;
        }
        if (!answer.body.ok) {
            const unreadable = answer.body.error === "not_a_youtube_link";
            sayStream(unreadable
                ? words.scanUnknown
                : "Could not add it (" + (answer.body.error || answer.status) + ").", "bad");
            return;
        }
        sayStream(words.scanQueued, "good");
        if (answer.body.state) {
            renderStream(answer.body.state);
        }
    } catch (error) {
        setConnection(false);
    }
}

/**
 * Show a dialog, with a fallback for browsers without showModal.
 *
 * @param {HTMLDialogElement} dialog The dialog to open.
 * @returns {void}
 */
function openDialog(dialog) {
    // Android fires contextmenu *as well* after a long press, so this can be
    // asked for twice for one gesture - and showModal on an open dialog throws.
    if (dialog.open) {
        return;
    }
    if (typeof dialog.showModal === "function") {
        dialog.showModal();
        return;
    }
    dialog.setAttribute("open", "open");
}

/**
 * Hide a dialog again.
 *
 * @param {HTMLDialogElement} dialog The dialog to close.
 * @returns {void}
 */
function closeDialog(dialog) {
    if (typeof dialog.close === "function") {
        dialog.close();
        return;
    }
    dialog.removeAttribute("open");
}

/**
 * Say the mobile-data words in the language the phone is set to.
 *
 * @returns {{ask: string, switched: string, blocked: string}} The wording.
 */
function mobileWords() {
    const german = /^de\b/i.test(navigator.language || "");
    if (german) {
        return {
            ask: "Du bist im Mobilfunknetz. Titel aus dem Netz streamen und dafür " +
                 "Datenvolumen verbrauchen?",
            switched: "Mobile Daten: Es werden nur die heruntergeladenen Titel abgespielt.",
            blocked: "Dieser Titel liegt nicht lokal vor.",
        };
    }
    return {
        ask: "You are on a mobile connection. Stream from the network and use data for it?",
        switched: "Mobile data: only downloaded songs are played.",
        blocked: "That song is not on this device.",
    };
}

/**
 * Decide whether a track that is not on disk may be fetched right now.
 *
 * Asking happens here rather than on the PC: this is the device that knows it
 * is on mobile data, and a dialog on a PC in another room helps nobody.
 *
 * @param {object|null} track The queue entry that is about to play.
 * @returns {Promise<boolean>} Whether playback may go ahead.
 */
async function mayStream(track) {
    if (track && track.local) {
        return true;
    }
    const words = mobileWords();
    if (playbackSource.local_only) {
        sayStream(words.switched, "");
        await stream("library");
        return false;
    }
    if (playbackSource.ask) {
        if (window.confirm(words.ask)) {
            await stream("allow_mobile");
            playbackSource.ask = false;
            return true;
        }
        sayStream(words.switched, "");
        await stream("library");
        return false;
    }
    return true;
}

function playHere(videoId) {
    const index = queueTracks.findIndex((track) => track.video_id === videoId);
    const track = index >= 0 ? queueTracks[index] : null;
    const gated = !(track && track.local)
        && (playbackSource.local_only || playbackSource.ask);
    if (gated) {
        // Asking is asynchronous; every caller of playHere is not. Deciding
        // first and starting afterwards keeps them all unchanged.
        mayStream(track).then((allowed) => {
            if (allowed) {
                startHere(videoId);
            }
        });
        return;
    }
    startHere(videoId);
}

/**
 * Actually start a track on this device, once it is allowed to.
 *
 * @param {string} videoId The YouTube id of the queued track.
 * @returns {void}
 */
function startHere(videoId) {
    const generation = ++playGeneration;
    guestVideoId = videoId;
    // May be -1 for a fresh search hit the queue has not caught up with yet;
    // renderStream fills it in as soon as the queue arrives.
    guestIndex = queueTracks.findIndex((track) => track.video_id === videoId);
    markQueueCurrent(videoId);
    // New title → show that title's own rating, not the previous one's.
    syncVoteDisplay(videoId);
    const track = guestIndex >= 0 ? queueTracks[guestIndex] : null;
    // Video only for what comes off YouTube: a local file plays as it is, and
    // a phone that switched to its library wants sound, not a black rectangle.
    const asVideo = wantVideo && !(track && track.local);
    elements.player.hidden = asVideo;
    elements.streamVideo.hidden = !asVideo;
    if (asVideo) {
        elements.player.pause();
        elements.player.removeAttribute("src");
        elements.streamVideo.src = "/video/" + encodeURIComponent(videoId);
        elements.streamVideo.play().catch(() => undefined);
        syncStage();
        return;
    }
    elements.streamVideo.pause();
    elements.streamVideo.removeAttribute("src");
    ensureAnalyser();
    syncStage();
    elements.player.src = trackSource(videoId, guestIndex);
    window.clearTimeout(playWatchTimer);
    playWatchTimer = window.setTimeout(() => {
        if (generation !== playGeneration || guestVideoId !== videoId) {
            return;
        }
        // Never got past metadata / still silent → treat as unplayable.
        if (elements.player.readyState < 2 && elements.player.currentTime < 0.25) {
            skipUnplayable(videoId, "timeout");
        }
    }, 12000);
    elements.player.play().catch((error) => {
        if (generation !== playGeneration) {
            return;
        }
        // Phone autoplay gate — ask for a tap, do not burn through the queue.
        if (error && error.name === "NotAllowedError") {
            sayStream("Tap play in the player — the phone wants a tap first.", "");
            return;
        }
        skipUnplayable(videoId, "play");
    });
}

/**
 * Mark the playing queue row in red without waiting for the next poll.
 *
 * @param {string} videoId The track that is playing.
 * @returns {void}
 */
function markQueueCurrent(videoId) {
    if (!elements.queue) {
        return;
    }
    elements.queue.querySelectorAll(".queue-row").forEach((row) => {
        const on = Boolean(videoId) && row.dataset.video === videoId;
        row.classList.toggle("current", on);
        const badge = row.querySelector(".badge");
        if (badge) {
            badge.classList.toggle("ok", on);
            if (on) {
                badge.textContent = "▶";
            } else if (badge.textContent === "▶") {
                const track = queueTracks.find((item) => item.video_id === row.dataset.video);
                badge.textContent = track ? String(track.index + 1) : "";
            }
        }
    });
}

/**
 * Skip a track that could not be played and continue with the next usable one.
 *
 * @param {string} videoId The failed track.
 * @param {string} [reason] Why it failed (for the status line).
 * @returns {void}
 */
function skipUnplayable(videoId, reason) {
    if (!videoId || target !== "guest") {
        return;
    }
    unplayableIds[videoId] = true;
    window.clearTimeout(playWatchTimer);
    const title = ((queueTracks.find((track) => track.video_id === videoId) || {}).title) || "track";
    sayStream("Could not play “" + title + "” — skipping.", "bad");
    const start = queueTracks.findIndex((track) => track.video_id === videoId);
    if (start >= 0) {
        guestIndex = start;
    }
    // A failure is not the song ending, so repeat-one must not send it back.
    playNextHere(false).then(() => {
        if (guestVideoId !== videoId) {
            return;
        }
        // Nothing usable came back after this failure.
        guestVideoId = "";
        guestIndex = -1;
        markQueueCurrent("");
        try {
            elements.player.pause();
            elements.player.removeAttribute("src");
            elements.player.load();
        } catch (error) {
            // ignore
        }
        sayStream("No further playable tracks in the queue.", "bad");
    });
    void reason;
}

/**
 * Play whatever follows the track this device is on.
 *
 * Which row that is comes from the PC, not from here: shuffle and repeat are
 * one rule for both platforms, and a phone that just took the next row down the
 * list would silently ignore both.
 *
 * @param {boolean} [automatic] True when the song ended by itself, which is the
 *     only case where repeat-one repeats it.
 * @returns {Promise<void>}
 */
async function playNextHere(automatic) {
    const ended = automatic !== false;
    const skipped = {};
    for (let attempt = 0; attempt < 20; attempt += 1) {
        let answer;
        try {
            answer = await api("/api/discover/next", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({index: guestIndex, automatic: ended}),
            });
        } catch (error) {
            setConnection(false);
            return;
        }
        const index = answer.body && typeof answer.body.index === "number"
            ? answer.body.index : -1;
        if (index < 0 || index >= queueTracks.length || skipped[index]) {
            return;
        }
        const next = queueTracks[index];
        if (next && next.video_id && !unplayableIds[next.video_id]) {
            playHere(next.video_id);
            return;
        }
        // Already failed this session: move the playhead on and ask again.
        skipped[index] = true;
        guestIndex = index;
    }
}

/**
 * Show the volume row in the shape the current target needs.
 *
 * @returns {void}
 */
function updateVolumeRow() {
    if (target === "guest") {
        elements.volumeRow.classList.remove("unavailable");
        elements.volume.disabled = false;
        elements.volume.value = String(Math.round(elements.player.volume * 100));
        elements.volumeValue.textContent = elements.volume.value;
        return;
    }
    const usable = elements.volume.dataset.controllable === "yes";
    elements.volumeRow.classList.toggle("unavailable", !usable);
    elements.volume.disabled = !usable;
}

/**
 * Apply the slider to whichever side is playing.
 *
 * @returns {void}
 */
function applyVolume() {
    const value = Number(elements.volume.value);
    elements.volumeValue.textContent = String(value);
    if (target === "guest") {
        elements.player.volume = Math.max(0, Math.min(1, value / 100));
        return;
    }
    stream("volume", {seconds: value});
}

/**
 * Seek by tapping the progress bar.
 *
 * @param {MouseEvent} event The click on the bar.
 * @returns {void}
 */
function seekFromClick(event) {
    if (streamDuration <= 0) {
        return;
    }
    const box = elements.streamTrack.getBoundingClientRect();
    if (box.width <= 0) {
        return;
    }
    const share = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    stream("seek", {seconds: share * streamDuration});
}

// ---------------------------------------------------------------------- start
/**
 * Prefill the form from an Android share, and send it when it is a link.
 *
 * @returns {boolean} Whether something was shared.
 */
function acceptShare() {
    const shared = queryParam("url") || queryParam("text") || "";
    const match = shared.match(/https?:\/\/\S+/);
    if (!match) {
        return false;
    }
    elements.url.value = match[0];
    return true;
}

document.addEventListener("DOMContentLoaded", () => {
    const shared = acceptShare();
    const hash = (window.location.hash || "").replace(/^#/, "");
    const wanted = ["streaming", "downloads", "settings", "about"].indexOf(hash) >= 0
        ? hash : "streaming";
    hideToken();
    localizeChrome();
    elements.form.addEventListener("submit", submit);
    elements.refresh.addEventListener("click", () => {
        lastSignature = "";
        loadDownloads();
    });
    if (elements.historyClear) {
        elements.historyClear.addEventListener("click", clearHistory);
    }
    document.querySelectorAll("input[name=dl-filter]").forEach((radio) => {
        radio.addEventListener("change", () => {
            downloadFilter = radio.value;
            renderDownloadList();
        });
    });
    if (elements.quit) {
        elements.quit.addEventListener("click", quitApp);
    }
    elements.settingsForm.addEventListener("submit", saveSettings);
    elements.settingsReload.addEventListener("click", () => loadSettings());
    if (elements.setCookiesRisk) {
        elements.setCookiesRisk.addEventListener("change", syncCookieFields);
    }
    if (elements.aboutTermsApp) {
        elements.aboutTermsApp.addEventListener("click", () => showTerms("app", false));
    }
    if (elements.aboutTermsStreaming) {
        elements.aboutTermsStreaming.addEventListener("click", () => showTerms("streaming", false));
    }
    if (elements.updateButton) {
        elements.updateButton.addEventListener("click", () => {
            if (updateAvailable) {
                installUpdate();
            } else {
                checkUpdate();
            }
        });
    }

    elements.tabStreaming.addEventListener("click", () => showView("streaming"));
    elements.tabDownloads.addEventListener("click", () => showView("downloads"));
    elements.tabSettings.addEventListener("click", () => showView("settings"));
    elements.tabAbout.addEventListener("click", () => showView("about"));
    // Likes, dislikes and downloads always belong to the PC; the transport
    // follows whichever side is playing. Guest mode must pass the local index
    // so the vote hits the song this device is hearing, not a silent host index.
    [["stream-like", "like"], ["stream-dislike", "dislike"],
     ["stream-download", "download"]].forEach(([id, command]) => {
        document.getElementById(id).addEventListener("click", () => {
            const extra = {};
            if (target === "guest" && guestIndex >= 0) {
                extra.index = guestIndex;
            }
            if (command === "like") {
                const next = lastVote === "up" ? "" : "up";
                if (ratedVideoId) {
                    voteById[ratedVideoId] = next;
                }
                updateVoteButtons(next);
                lastVote = next;
            } else if (command === "dislike") {
                const next = lastVote === "down" ? "" : "down";
                if (ratedVideoId) {
                    voteById[ratedVideoId] = next;
                }
                updateVoteButtons(next);
                lastVote = next;
            }
            stream(command, extra);
        });
    });
    [["stream-previous", "previous"], ["stream-next", "next"]].forEach(([id, command]) => {
        document.getElementById(id).addEventListener("click", () => transport(command));
    });
    elements.streamToggle.addEventListener("click", () => transport("toggle"));
    if (elements.streamStop) {
        elements.streamStop.addEventListener("click", () => transport("stop"));
    }

    document.querySelectorAll("#sort-row .sort").forEach((button) => {
        button.addEventListener("click", () => setSort(button.dataset.sort));
    });
    elements.search.addEventListener("input", scheduleSearch);
    elements.search.addEventListener("search", runSearch);
    document.querySelectorAll("input[name=target]").forEach((radio) => {
        radio.addEventListener("change", () => setTarget(radio.value));
    });
    elements.volume.addEventListener("input", applyVolume);
    // One song ends, the next starts - the same as on the PC.
    elements.player.addEventListener("ended", () => {
        if (target === "guest" && fromQueue(elements.player)) {
            playNextHere(true);
        }
    });
    elements.player.addEventListener("error", () => {
        if (target === "guest" && fromQueue(elements.player) && guestVideoId) {
            skipUnplayable(guestVideoId, "error");
        }
    });
    elements.streamVideo.addEventListener("ended", () => {
        if (target === "guest") {
            playNextHere(true);
        }
    });
    elements.streamVideo.addEventListener("error", () => {
        if (target === "guest" && guestVideoId) {
            skipUnplayable(guestVideoId, "video");
        }
    });
    elements.player.addEventListener("playing", () => {
        window.clearTimeout(playWatchTimer);
        // A browser may have suspended the graph while the tab was away; an
        // element that plays through it would then be silent.
        resumeAudio();
    });
    elements.streamRefresh.addEventListener("click", () => {
        sayStream("Looking for similar songs...");
        unplayableIds = {};
        stream("refresh");
    });
    elements.streamLibrary.addEventListener("click", () => {
        unplayableIds = {};
        stream("library");
    });
    elements.streamShuffle.addEventListener("click", () => saveMode(
        {discover_shuffle: !playbackModes.shuffle}));
    elements.streamRepeat.addEventListener("click", () => saveMode(
        {discover_repeat: nextRepeat(playbackModes.repeat)}));
    elements.streamSleep.addEventListener("change", () => stream("sleep", {
        seconds: Number(elements.streamSleep.value) * 60,
    }));
    elements.streamScan.hidden = !canScan();
    elements.streamScan.addEventListener("click", startScan);
    elements.scanClose.addEventListener("click", stopScan);
    elements.scanDialog.addEventListener("close", stopScan);
    elements.shareClose.addEventListener("click", () => closeDialog(elements.shareDialog));
    elements.shareCopy.addEventListener("click", copyShareLink);
    elements.streamTrack.addEventListener("click", seekFromClick);
    elements.resultsToggle.addEventListener("click", () => showResults());
    document.addEventListener("visibilitychange", syncPolling);
    bootstrapStatus();
    showView(wanted);
    if (shared) {
        showView("downloads");
        elements.form.requestSubmit();
    }
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/sw.js").catch(() => {
            // Without a service worker the page still works; it just cannot be
            // installed to the home screen on Android.
        });
    }
});

/**
 * Read status once so Quit / standalone mode are right before the first poll.
 *
 * @returns {Promise<void>}
 */
async function bootstrapStatus() {
    try {
        const answer = await api(statusPath());
        if (answer.status === 200) {
            if (elements.quit) {
                elements.quit.hidden = !answer.body.can_quit;
            }
            applyStandalone(!!answer.body.can_quit);
        }
    } catch (error) {
        // First paint can retry via normal polling.
    }
}

/**
 * Localise a few chrome labels when the phone language is German.
 *
 * @returns {void}
 */
function localizeChrome() {
    if (!/^de\b/i.test(navigator.language || "")) {
        return;
    }
    if (elements.quit) {
        elements.quit.textContent = "Beenden";
    }
    elements.tabStreaming.textContent = "Streaming";
    elements.tabDownloads.textContent = "Downloads";
    elements.tabSettings.textContent = "Einstellungen";
    elements.tabAbout.textContent = "Über";
    if (elements.updateHeading) {
        elements.updateHeading.textContent = "Update";
    }
    if (elements.updateState) {
        elements.updateState.textContent = "Version noch nicht geprüft.";
    }
    if (elements.updateButton) {
        elements.updateButton.textContent = updateLabels.de.check;
    }
}