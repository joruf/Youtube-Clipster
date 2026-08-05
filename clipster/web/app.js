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
    queue: document.getElementById("queue"),
    queueEmpty: document.getElementById("queue-empty"),
    search: document.getElementById("search"),
    searchNote: document.getElementById("search-note"),
    results: document.getElementById("results"),
    resultsToggle: document.getElementById("results-toggle"),
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

/** Track being played on this device, while target is "guest". */
let guestVideoId = "";

/** Its position in the queue, or -1 while the queue has not caught up. */
let guestIndex = -1;

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
function renderDownloadList() {
    const filtered = downloadFilter === "all"
        ? downloadEntries
        : downloadEntries.filter((entry) => entry.status === downloadFilter);
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
        answer = await api("/api/status");
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
        playNextHere();
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
    row.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(button("▶", "Play", () => playTrack(track)));
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

    let current = state.current;
    if (target === "guest") {
        // In guest mode the PC is stopped, so its "current" says nothing.
        const mine = (state.tracks || []).find((track) => track.video_id === guestVideoId);
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

    const tracks = state.tracks || [];
    queueTracks = tracks;
    if (guestVideoId) {
        // Recovered from the id, not remembered as a number: the queue shifts
        // whenever something is inserted in front of the current track.
        guestIndex = tracks.findIndex((track) => track.video_id === guestVideoId);
    }
    const signature = JSON.stringify([tracks.map((t) => t.video_id), state.index]);
    if (signature !== lastQueue) {
        lastQueue = signature;
        elements.queue.textContent = "";
        tracks.forEach((track) => elements.queue.appendChild(
            queueRow(track, track.index === state.index)));
        elements.queueEmpty.hidden = tracks.length > 0;
    }
    elements.streamRefresh.textContent = state.busy ? "Searching..." : "Find similar";
    elements.streamRefresh.disabled = Boolean(state.busy);

    const playingId = target === "guest"
        ? guestVideoId
        : (tracks[state.index] || {}).video_id || "";
    centreQueue(playingId);
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
function playHere(videoId) {
    guestVideoId = videoId;
    // May be -1 for a fresh search hit the queue has not caught up with yet;
    // renderStream fills it in as soon as the queue arrives.
    guestIndex = queueTracks.findIndex((track) => track.video_id === videoId);
    elements.player.hidden = false;
    elements.player.src = "/stream/" + encodeURIComponent(videoId);
    elements.player.play().catch(() => {
        sayStream("Tap play in the player — the phone wants a tap first.", "");
    });
}

/**
 * Play whatever follows the track this device is on.
 *
 * @returns {void}
 */
function playNextHere() {
    if (guestIndex < 0 || guestIndex + 1 >= queueTracks.length) {
        return;
    }
    playHere(queueTracks[guestIndex + 1].video_id);
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

    elements.tabStreaming.addEventListener("click", () => showView("streaming"));
    elements.tabDownloads.addEventListener("click", () => showView("downloads"));
    elements.tabSettings.addEventListener("click", () => showView("settings"));
    elements.tabAbout.addEventListener("click", () => showView("about"));
    // Likes, dislikes and downloads always belong to the PC; the transport
    // follows whichever side is playing.
    [["stream-like", "like"], ["stream-dislike", "dislike"],
     ["stream-download", "download"]].forEach(([id, command]) => {
        document.getElementById(id).addEventListener("click", () => stream(command));
    });
    [["stream-previous", "previous"], ["stream-next", "next"]].forEach(([id, command]) => {
        document.getElementById(id).addEventListener("click", () => transport(command));
    });
    elements.streamToggle.addEventListener("click", () => transport("toggle"));
    if (elements.streamStop) {
        elements.streamStop.addEventListener("click", () => transport("stop"));
    }

    elements.search.addEventListener("input", scheduleSearch);
    elements.search.addEventListener("search", runSearch);
    document.querySelectorAll("input[name=target]").forEach((radio) => {
        radio.addEventListener("change", () => setTarget(radio.value));
    });
    elements.volume.addEventListener("input", applyVolume);
    // One song ends, the next starts - the same as on the PC.
    elements.player.addEventListener("ended", () => {
        // The same element also plays finished downloads; only a relayed stream
        // means "go on to the next song".
        const relayed = (elements.player.currentSrc || "").indexOf("/stream/") !== -1;
        if (target === "guest" && relayed) {
            playNextHere();
        }
    });
    elements.streamRefresh.addEventListener("click", () => {
        sayStream("Looking for similar songs...");
        stream("refresh");
    });
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
        const answer = await api("/api/status");
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
}