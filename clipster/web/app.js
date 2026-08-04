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
};

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
    window.history.replaceState({}, "", window.location.pathname);
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
        pollTimer = window.setInterval(poll, POLL_INTERVAL);
    }
    poll();
    loadDownloads();
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
    hideToken();
    elements.form.addEventListener("submit", submit);
    elements.refresh.addEventListener("click", () => {
        lastSignature = "";
        loadDownloads();
    });
    document.addEventListener("visibilitychange", syncPolling);
    syncPolling();
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
