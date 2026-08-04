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
    connection: document.getElementById("connection"),
    player: document.getElementById("player"),
    tabDownloads: document.getElementById("tab-downloads"),
    tabStreaming: document.getElementById("tab-streaming"),
    viewDownloads: document.getElementById("view-downloads"),
    viewStreaming: document.getElementById("view-streaming"),
    streamTitle: document.getElementById("stream-title"),
    streamUploader: document.getElementById("stream-uploader"),
    streamTrack: document.getElementById("stream-track"),
    streamFill: document.getElementById("stream-fill"),
    streamTime: document.getElementById("stream-time"),
    streamLevel: document.getElementById("stream-level"),
    streamToggle: document.getElementById("stream-toggle"),
    streamMessage: document.getElementById("stream-message"),
    streamRefresh: document.getElementById("stream-refresh"),
    queue: document.getElementById("queue"),
    queueEmpty: document.getElementById("queue-empty"),
    search: document.getElementById("search"),
    searchNote: document.getElementById("search-note"),
    results: document.getElementById("results"),
    resultsToggle: document.getElementById("results-toggle"),
    targetNote: document.getElementById("target-note"),
    volumeRow: document.getElementById("volume-row"),
    volume: document.getElementById("volume"),
    volumeValue: document.getElementById("volume-value"),
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

/** Which view is on screen: "downloads" or "streaming". */
let view = "downloads";

/** Signature of the queue as last rendered, so a poll does not rebuild it. */
let lastQueue = "";

/** Length of the track being played, needed to turn a tap into a position. */
let streamDuration = 0;

let pollTimer = null;
let lastSignature = "";

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
    row.appendChild(actions);
    return row;
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
    const signature = JSON.stringify(entries.map((entry) => [entry.id, entry.playable]));
    if (signature === lastSignature) {
        return;
    }
    lastSignature = signature;
    elements.downloads.textContent = "";
    entries.forEach((entry) => elements.downloads.appendChild(downloadRow(entry)));
    elements.empty.hidden = entries.length > 0;
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
    if (active.length > 0) {
        // A download just finished somewhere between two polls.
        await loadDownloads();
    }
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
    poll();
    loadDownloads();
}

// -------------------------------------------------------------- streaming
/**
 * Show one of the two views.
 *
 * @param {string} name "downloads" or "streaming".
 * @returns {void}
 */
function showView(name) {
    view = name;
    // In the address, so a reload keeps the tab the user was on - and so the
    // view can be opened directly from a bookmark.
    if (window.location.hash !== "#" + name) {
        window.history.replaceState({}, "", window.location.pathname + "#" + name);
    }
    const streaming = name === "streaming";
    elements.viewDownloads.hidden = streaming;
    elements.viewStreaming.hidden = !streaming;
    elements.tabDownloads.classList.toggle("selected", !streaming);
    elements.tabStreaming.classList.toggle("selected", streaming);
    syncPolling();
}

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
    }
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
            sayStream("Accept the Streaming terms once on the PC, then try again.", "bad");
            return;
        }
        if (!answer.body.ok) {
            sayStream("The PC could not do that (" + (answer.body.error || answer.status) + ").", "bad");
            return;
        }
        sayStream("");
        if (answer.body.state) {
            renderStream(answer.body.state);
        }
    } catch (error) {
        setConnection(false);
        sayStream("The PC cannot be reached.", "bad");
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
    if (!state.available) {
        elements.streamTitle.textContent = "Streaming is not available on the PC.";
        return;
    }
    if (!state.terms_accepted) {
        sayStream("Streaming needs its terms of use accepted once on the PC.", "bad");
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
            elements.searchNote.textContent = "Accept the Streaming terms once on the PC.";
            return;
        }
        const results = answer.body.results || [];
        if (!answer.body.ok) {
            elements.searchNote.textContent = "The search failed on the PC.";
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
        elements.searchNote.textContent = "The PC cannot be reached.";
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
                ? "Accept the Streaming terms once on the PC."
                : "The PC did not accept that track.";
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
    const wanted = window.location.hash === "#streaming" ? "streaming" : "downloads";
    hideToken();
    elements.form.addEventListener("submit", submit);
    elements.refresh.addEventListener("click", () => {
        lastSignature = "";
        loadDownloads();
    });

    elements.tabDownloads.addEventListener("click", () => showView("downloads"));
    elements.tabStreaming.addEventListener("click", () => showView("streaming"));
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
    showView(wanted);
    if (shared) {
        elements.form.requestSubmit();
    }
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/sw.js").catch(() => {
            // Without a service worker the page still works; it just cannot be
            // installed to the home screen on Android.
        });
    }
});
