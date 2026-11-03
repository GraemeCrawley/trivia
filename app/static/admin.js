/* bargames — organizer client. Relative URLs only (see app.js).
 *
 * This is one person's phone, held in one hand, running a room. So: the state
 * of the room is readable without scrolling, and the only control she can ever
 * be hunting for mid-question — "End this question" — is docked under the thumb
 * and never scrolls away.
 */
const bargamesAdmin = (() => {
  const $ = (id) => document.getElementById(id);
  let es = null;

  // The server's phase names are a state machine's names. Hers are English.
  const PHASE_TEXT = {
    none: "No game yet",
    lobby: "Waiting in the lobby",
    question: "Question in play",
    reveal: "Showing the answer",
    round_leaderboard: "Between rounds",
    final: "Game over",
  };

  async function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  // A refused control is the one thing on this screen she cannot be left to
  // guess at: a room is watching, she tapped START, and nothing happened.
  // #panel-error lives inside #admin-panel on purpose — #admin-error is inside
  // #admin-login, which is hidden the moment she signs in, so a message written
  // there is invisible exactly when it matters.
  let errorContext = null;

  function stateSignature() {
    return lastState
      ? `${lastState.phase}:${(lastState.players || []).length}`
      : null;
  }

  function showError(message) {
    const el = $("panel-error");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    // The dock is pinned to the bottom of the viewport and she may be scrolled
    // anywhere in a long panel, so put the message where she is looking.
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    errorContext = stateSignature();
  }

  function clearError() {
    const el = $("panel-error");
    if (el) { el.hidden = true; el.textContent = ""; }
    errorContext = null;
  }

  // Run a panel control and surface its refusal in words. No timed auto-hide:
  // a message that disappears on a four-second timer is one she misses entirely
  // while she is looking at the room rather than at her phone. It goes when it
  // stops being true — see render().
  // Say what happened. Tapping a control on the organizer phone used to look
  // exactly like tapping nothing: no busy state, and silence on success.
  function note(message) {
    const el = $("panel-note");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(note._t);
    note._t = setTimeout(() => { el.hidden = true; }, 2600);
  }

  async function act(fn, opts) {
    const o = opts || {};
    const btn = o.btn ? $(o.btn) : null;
    const label = btn ? btn.textContent : null;
    if (btn) {
      btn.disabled = true;
      btn.classList.add("busy");
      if (o.busy) btn.textContent = o.busy;
    }
    clearError();
    try {
      const r = await fn();
      if (r && !r.ok) {
        const d = await r.json().catch(() => ({}));
        showError(d.detail || "That didn't work. Try it again.");
        return false;
      }
      if (o.done) note(typeof o.done === "function" ? await o.done(r) : o.done);
    } catch (e) {
      showError("Lost the connection to the game. Check the wifi and try again.");
      return false;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("busy");
        if (label !== null) btn.textContent = label;
      }
    }
    return true;
  }

  let lastState = null;
  let setTray = null;
  let lastPhase = null;

  function render(s) {
    // Re-open the tray whenever the game moves on. Which control is the right
    // one changes with the phase, and a button she needs must never be hidden
    // behind a tray she collapsed three questions ago.
    if (setTray && s.phase && s.phase !== lastPhase) {
      lastPhase = s.phase;
      setTray(true);
    }
    lastState = s;

    // A refusal describes a state ("Nobody has joined yet"). Once that state has
    // moved on — somebody joined, the phase changed — the message is no longer
    // true, and a stale error on this screen is worse than no error at all.
    if (errorContext !== null && stateSignature() !== errorContext) clearError();

    $("admin-phase").textContent = PHASE_TEXT[s.phase] || s.phase;

    if (s.pin) {
      $("pin-display").textContent = s.pin;
      const link = `${location.origin}${location.pathname.replace(/admin$/, "")}j/${s.pin}`;
      const a = $("join-link");
      a.href = link;
      a.textContent = link;
    }

    const prog = $("admin-progress");
    if (s.progress && s.phase === "question") {
      prog.textContent = `${s.progress.answered} of ${s.progress.total} in`;
      prog.hidden = false;
    } else {
      prog.hidden = true;
    }

    const players = s.players || [];
    const joined = players.filter(p => !p.seated).length;
    const seated = players.length - joined;
    $("roster").textContent = seated
      ? `${joined} playing, ${seated} joining next question`
      : `${joined} playing`;

    renderPlayerList(players);

    bargames.fillBubbles($("admin-bubbles"), players);
    bargames.fillRace($("admin-race"), s.leaderboard || []);

    // An empty lobby is the state she will actually be looking at while she
    // waits for people to arrive, so it should not be two headings with nothing
    // underneath them. Hide the section, not just its contents.
    const section = (el) => el && el.closest(".admin-section");
    const bubbleSection = section($("admin-bubbles"));
    if (bubbleSection) bubbleSection.hidden = players.length === 0;
    const raceSection = section($("admin-race"));
    if (raceSection) raceSection.hidden = (s.leaderboard || []).length === 0;

    // Nothing has been uploaded yet, so there is nothing to delete — and in an
    // empty lobby this is the only other control on screen, which puts the most
    // destructive button on the page directly above the one she is about to tap.
    // Nothing uploaded yet means nothing to delete.
    const foot = $("photo-foot");
    if (foot) foot.hidden = !players.some(p => p.selfie);

    // Config only matters before a game exists; once one is running it is dead
    // weight between her and the controls.
    $("cfg-block").hidden = !["none", "final"].includes(s.phase);

    // Only show the control that is actually meaningful right now, so the
    // organizer is never hunting for the right button with a room watching.
    renderFlagged(s.flagged || []);

    const p = s.progress_label;
    if (p && $("admin-progress-label")) {
      $("admin-progress-label").textContent =
        `Round ${p.round} of ${p.rounds} · Question ${p.question} of ${p.questions}`;
      // Meaningless before the game starts, and a stale "Question 1 of 8" on a
      // lobby screen reads as if something has already happened.
      // Also hide it when there is nothing to say: an empty pill on screen
      // reads as a broken control, which is exactly how it was reported.
      const text = $("admin-progress-label").textContent.trim();
      $("admin-progress-label").hidden =
        !text || s.phase === "lobby" || s.phase === "none" || s.phase === "final";
    }

    // Nothing is pending in the lobby or once the game is over; leaving the
    // heading up said "Who we're waiting for" under a finished game.
    if ($("waiting-block")) {
      $("waiting-block").hidden = !(s.phase === "question" || s.phase === "reveal");
    }

    const paused = !!s.paused;
    const running = s.phase === "question" || s.phase === "reveal";
    if ($("btn-pause")) {
      $("btn-pause").hidden = !running;
      $("btn-pause").textContent = paused ? "Resume" : "Pause";
    }
    if ($("btn-next-question")) $("btn-next-question").hidden = !running;
    if ($("more-zone")) $("more-zone").hidden = s.phase === "none";

    $("btn-start").hidden = s.phase !== "lobby";
    $("btn-end-question").hidden = s.phase !== "question";
    $("btn-next-round").hidden = s.phase !== "round_leaderboard";
  }

  function renderFlagged(list) {
    const box = $("flagged-queue");
    if (!box) return;
    box.hidden = list.length === 0;

    // The queue lives well down a scrolling page, and she has no reason to go
    // looking. The count rides in the status line at the top so she knows there
    // is a judgement waiting without hunting for one.
    const count = $("flagged-count");
    if (count) {
      const open = list.filter(f => f.accepted).length;
      count.hidden = list.length === 0;
      count.textContent = list.length === 1
        ? "1 close call" : `${list.length} close calls`;
      count.classList.toggle("settled", open === 0);
    }

    $("flagged-list").replaceChildren(...list.map(f => {
      const row = document.createElement("div");
      row.className = "flagrow" + (f.accepted ? "" : " revoked");
      row.dataset.playerId = f.player_id;
      row.dataset.questionId = f.question_id;

      const text = document.createElement("div");
      text.className = "flagtext";

      // The question, not just the answer. The queue accumulates across a whole
      // round, so by the time she reads it the question is three questions back
      // and "was 'vegemight' close enough" is unanswerable without it. The
      // server already sends the prompt; the baseline render dropped it.
      if (f.prompt) {
        const q = document.createElement("span");
        q.className = "flagq";
        q.textContent = f.prompt;
        text.appendChild(q);
      }

      const line = document.createElement("span");
      line.className = "flagline";
      const who = document.createElement("strong");
      who.textContent = f.name;
      const given = document.createElement("em");
      given.className = "flaggiven";
      given.textContent = `“${f.given}”`;
      line.append(who, document.createTextNode(" answered "), given);
      text.appendChild(line);
      row.appendChild(text);

      const btn = document.createElement("button");
      // One button per row, and its label is the verdict it will produce —
      // never a bare "Undo", which does not say what comes back.
      btn.className = "flagbtn" + (f.accepted ? "" : " restore");
      btn.textContent = f.accepted ? `Revoke ${f.points}` : `Give back ${f.points}`;
      btn.onclick = async () => {
        btn.disabled = true;
        await post("api/admin/review", {
          player_id: f.player_id, question_id: f.question_id, accept: !f.accepted,
        });
        refresh();
      };
      row.appendChild(btn);
      return row;
    }));
  }

  function renderPlayerList(players) {
    const box = $("player-list"), block = $("player-list-block");
    if (!box || !block) return;
    block.hidden = players.length === 0;
    box.replaceChildren(...players.map((p) => {
      const row = document.createElement("div");
      row.className = "player-row";

      const face = bargames.avatar(p);
      face.classList.add("small");
      row.appendChild(face);

      const name = document.createElement("span");
      name.className = "player-name";
      name.textContent = p.name + (p.seated ? " (joining next question)" : "");
      row.appendChild(name);

      const remove = document.createElement("button");
      remove.className = "player-remove";
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${p.name}`);
      remove.textContent = "Remove";
      remove.onclick = async () => {
        // Confirm by name: removing the wrong person mid-game loses their
        // score with no way back.
        if (!confirm(`Remove ${p.name} from the game? Their score goes too.`)) return;
        await act(() => post("api/admin/remove_player", { player_id: p.id }),
                  { btn: null, done: `Removed ${p.name}.` });
        refresh();
      };
      row.appendChild(remove);
      return row;
    }));
  }

  async function refresh() {
    const r = await fetch("api/admin/state");
    if (r.ok) render(await r.json());
  }

  function connect() {
    if (es) es.close();
    es = new EventSource("api/stream");
    es.onmessage = () => refresh();
    es.onerror = () => setTimeout(refresh, 1000);
  }

  function showPanel() {
    $("admin-login").hidden = true;
    $("admin-bar").hidden = false;
    $("admin-panel").hidden = false;
    refresh();
    connect();
  }

  function init() {
    $("btn-login").onclick = async () => {
      const fd = new FormData();
      fd.append("password", $("admin-password").value);
      const r = await fetch("api/admin/login", { method: "POST", body: fd });
      if (!r.ok) {
        $("admin-error").textContent = "That password isn't right.";
        $("admin-error").hidden = false;
        return;
      }
      showPanel();
    };
    $("admin-password").onkeydown = (e) => { if (e.key === "Enter") $("btn-login").click(); };

    $("btn-create").onclick = async () => {
      await act(() => post("api/admin/create", {
        timer_seconds: Number($("cfg-timer").value),
        questions_per_round: Number($("cfg-per-round").value),
        reveal_seconds: Number($("cfg-reveal").value),
      }));
      refresh();
    };
    $("btn-start").onclick = async () => {
      await act(() => post("api/admin/start"),
                { btn: "btn-start", busy: "Starting…", done: "Game started." });
      refresh();
    };
    $("btn-end-question").onclick = async () => {
      await act(() => post("api/admin/end_question"),
                { btn: "btn-end-question", busy: "Ending…",
                  done: "Question ended — scoring what was in." });
      refresh();
    };
    $("btn-next-round").onclick = async () => {
      await act(() => post("api/admin/next_round"),
                { btn: "btn-next-round", busy: "Starting…", done: "Next round." });
      refresh();
    };
    $("btn-pause").onclick = async () => {
      // Read the state back rather than trusting a local toggle: two organizer
      // devices can both be signed in, so this phone's idea of paused may be
      // stale.
      const r = await fetch("api/admin/state");
      const cur = r.ok ? (await r.json()).paused : false;
      await act(() => post("api/admin/pause", { paused: !cur }),
                { btn: "btn-pause", busy: "…",
                  done: cur ? "Resumed." : "Paused — every clock is stopped." });
      refresh();
    };

    $("btn-next-question").onclick = async () => {
      await act(() => post("api/admin/next_question"),
                { btn: "btn-next-question", busy: "Skipping…", done: "Moved on." });
      refresh();
    };

    // Reset is two taps on purpose. It is the one control here that cannot be
    // undone, and it sits next to buttons she will be using all night.
    $("btn-reset").onclick = () => { $("reset-confirm").hidden = false; };
    $("btn-reset-no").onclick = () => { $("reset-confirm").hidden = true; };
    $("btn-reset-yes").onclick = async () => {
      await act(() => post("api/admin/reset"),
                { btn: "btn-reset-yes", busy: "Resetting…",
                  done: "Game reset — everyone kept, scores back to zero." });
      $("reset-confirm").hidden = true;
      refresh();
    };

    $("btn-delete-selfies").onclick = async () => {
      if (!confirm("Delete every photo from tonight? This cannot be undone.")) return;
      await act(() => post("api/admin/delete_selfies"), {
        btn: "btn-delete-selfies", busy: "Deleting…",
        done: async (r) => {
          const d = await r.json().catch(() => ({}));
          const n = d.removed ?? 0;
          return `Deleted ${n} photo${n === 1 ? "" : "s"}.`;
        },
      });
      refresh();
    };

    // One chevron collapses every control in the tray, so the organizer can
    // see the room instead of a wall of buttons. Her choice sticks for the
    // session, and render() re-opens it whenever the phase changes.
    const tray = $("btn-tray"), trayBox = $("control-buttons");
    if (tray && trayBox) {
      setTray = (open) => {
        trayBox.hidden = !open;
        tray.setAttribute("aria-expanded", String(open));
        tray.classList.toggle("collapsed", !open);
        const label = $("tray-label");
        if (label) label.textContent = open ? "Hide controls" : "Show controls";
        try { localStorage.setItem("bg_tray", open ? "1" : "0"); } catch (e) {}
      };
      let open = true;
      try { open = localStorage.getItem("bg_tray") !== "0"; } catch (e) {}
      setTray(open);
      tray.onclick = () => setTray(trayBox.hidden);
    }

    // Already signed in from earlier? Skip the login screen.
    fetch("api/admin/state").then(r => { if (r.ok) showPanel(); });
  }

  return { init };
})();
