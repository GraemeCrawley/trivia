/* bargames — participant client.
 *
 * Two rules that are easy to break and expensive to break:
 *  1. Every URL here is RELATIVE. The app may be served under a sub-path
 *     (e.g. /trivia/) by a reverse proxy that strips it, so an absolute
 *     "/api/state" would 404 there while working perfectly locally.
 *  2. The countdown is RENDERED here, never enforced here. The server owns the
 *     deadline and rejects late answers; this only draws the number. A phone
 *     that sleeps through a question therefore wakes up showing the truth.
 */
const bargames = (() => {
  let clockOffset = 0;      // server_time - local time, milliseconds
  let state = null;
  let renderedQuestion = null;
  let answeredQuestion = null;
  let es = null;

  const $ = (id) => document.getElementById(id);
  const now = () => Date.now() + clockOffset;

  function token() {
    const fromUrl = new URLSearchParams(location.search).get("t");
    if (fromUrl) {
      try { localStorage.setItem("bg_token", fromUrl); } catch (e) {}
      return fromUrl;
    }
    try { return localStorage.getItem("bg_token") || ""; } catch (e) { return ""; }
  }

  function withToken(path) {
    const t = token();
    return t ? `${path}${path.includes("?") ? "&" : "?"}t=${encodeURIComponent(t)}` : path;
  }

  // ---- identity colour -----------------------------------------------
  // Twelve curated silks in the stylesheet, picked by a hash of the player id
  // rather than by join order: her colour then survives a reload, a reconnect,
  // and somebody else leaving, which join order would not.
  const SILKS = 12;

  function hashOf(id) {
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
    return h;
  }

  // ---- avatars -------------------------------------------------------
  // A selfie when she took one, otherwise her initial on her silk. Identical in
  // the bubble field, the race and the pick-a-player grid, so a face learned in
  // one place is recognised in the others.
  function avatar(p) {
    const el = document.createElement("div");
    el.className = "avatar";
    el.dataset.silk = hashOf(p.id) % SILKS;
    // Her initial is always underneath. The photo is layered over it, so a
    // selfie that never arrives degrades to exactly what a player who skipped
    // the photo gets — never a broken image icon in a row of faces. Selfie
    // serving is fail-closed (403 with no cookie, wrong game, or ended game),
    // so this is the normal path at the end of the night, not an edge case.
    el.textContent = p.initial;
    if (p.selfie) {
      const img = document.createElement("img");
      img.alt = "";
      img.onerror = () => img.remove();
      img.src = `selfie/${p.id}${p.photo_rev ? `?r=${p.photo_rev}` : ""}`;
      el.appendChild(img);   // in the DOM before src settles, or it never loads
    }
    return el;
  }

  function bubble(p) {
    const wrap = document.createElement("div");
    // State is carried by a class, not by colour alone: the stylesheet dims,
    // shrinks, dashes and pulses "thinking" so it still reads for a colourblind
    // player across a dark table.
    wrap.className = "bubble " + (p.answered ? "in" : "thinking");
    // Stagger both the phase and the period. Same delay with the same duration
    // still drifts in unison after one cycle, which reads as a machine rather
    // than a room full of people.
    const h = hashOf(p.id);
    wrap.style.animationDelay = `${(h % 40) / 10}s`;
    wrap.style.setProperty("--drift", `${5 + (h % 9) * 0.45}s`);
    wrap.dataset.playerId = p.id;
    wrap.dataset.silk = h % SILKS;
    wrap.appendChild(avatar(p));
    const name = document.createElement("span");
    name.className = "bname";
    name.textContent = p.name;
    wrap.appendChild(name);
    // The check glyph is decorative CSS, so the state goes to a screen reader here.
    wrap.setAttribute("aria-label", `${p.name} — ${p.answered ? "answered" : "still thinking"}`);
    return wrap;
  }

  // ---- the bubble field: drag them, throw them, watch them bounce --------
  //
  // A real little physics loop rather than a CSS animation, because they
  // should be throwable and CSS cannot do collisions or a flick. One rAF loop
  // per container; nodes are reconciled in place on every snapshot so a
  // leaderboard update never teleports a bubble out from under a thumb.
  const fields = new WeakMap();
  const REST = 0.86;          // energy kept in a wall bounce
  const DRAG = 0.995;         // air resistance, so a hard throw settles
  const IDLE = 0.22;          // gentle drift so the field never goes dead

  function bubbleField(container, players, opts) {
    if (!container) return;
    const neutral = !!(opts && opts.neutral);
    const wanted = players.filter((p) => !p.seated);

    let f = fields.get(container);
    if (!f) {
      f = { nodes: new Map(), raf: null, w: 0, h: 0 };
      fields.set(container, f);
      container.classList.add("bubble-field");
    }

    // --- reconcile: add newcomers, drop leavers, restyle the rest ---
    const seen = new Set();
    for (const p of wanted) {
      seen.add(p.id);
      let n = f.nodes.get(p.id);
      if (!n) {
        const el = bubble(p);
        el.style.position = "absolute";
        el.style.touchAction = "none";      // we handle the gesture ourselves
        container.appendChild(el);
        n = {
          el, x: Math.random() * Math.max(1, container.clientWidth - 80),
          y: Math.random() * Math.max(1, container.clientHeight - 90),
          vx: (Math.random() - 0.5) * 2.2, vy: (Math.random() - 0.5) * 2.2,
          dragging: false, px: 0, py: 0, lastT: 0,
          face: `${p.selfie ? 1 : 0}:${p.photo_rev || 0}`,
        };
        attachDrag(n, container, f);
        f.nodes.set(p.id, n);
      }
      // classes may change every snapshot (answered / still thinking)
      n.el.className = "bubble " + (neutral ? "neutral" : (p.answered ? "in" : "thinking"));
      const label = n.el.querySelector(".bname");
      if (label) label.textContent = p.name;

      // Rebuild the face when it actually changed. Reconciling in place is
      // what keeps a bubble from teleporting on every snapshot, but it also
      // meant a player who added a photo kept her initial disc until she
      // reloaded the page.
      const stamp = `${p.selfie ? 1 : 0}:${p.photo_rev || 0}`;
      if (n.face !== stamp) {
        n.face = stamp;
        const old = n.el.querySelector(".avatar");
        if (old) old.replaceWith(avatar(p));
      }
    }
    for (const [id, n] of [...f.nodes]) {
      if (!seen.has(id)) { n.el.remove(); f.nodes.delete(id); }
    }

    if (!f.raf) step(container, f);
  }

  function attachDrag(n, container, f) {
    n.el.addEventListener("pointerdown", (e) => {
      n.dragging = true;
      n.el.setPointerCapture(e.pointerId);
      n.el.classList.add("held");
      const r = container.getBoundingClientRect();
      n.grabX = e.clientX - r.left - n.x;
      n.grabY = e.clientY - r.top - n.y;
      n.px = e.clientX; n.py = e.clientY; n.lastT = performance.now();
      n.vx = n.vy = 0;
    });

    n.el.addEventListener("pointermove", (e) => {
      if (!n.dragging) return;
      const r = container.getBoundingClientRect();
      n.x = e.clientX - r.left - n.grabX;
      n.y = e.clientY - r.top - n.grabY;
      const now = performance.now();
      const dt = Math.max(8, now - n.lastT);
      // Velocity from the actual gesture, so a flick throws and a slow drag
      // just lets go.
      n.vx = ((e.clientX - n.px) / dt) * 16;
      n.vy = ((e.clientY - n.py) / dt) * 16;
      n.px = e.clientX; n.py = e.clientY; n.lastT = now;
    });

    const release = (e) => {
      if (!n.dragging) return;
      n.dragging = false;
      n.el.classList.remove("held");
      const cap = 28;
      n.vx = Math.max(-cap, Math.min(cap, n.vx));
      n.vy = Math.max(-cap, Math.min(cap, n.vy));
      try { n.el.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    n.el.addEventListener("pointerup", release);
    n.el.addEventListener("pointercancel", release);
  }

  function step(container, f) {
    const still = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const tick = () => {
      const W = container.clientWidth, H = container.clientHeight;
      const list = [...f.nodes.values()];

      // 1. integrate
      for (const n of list) {
        if (n.dragging || still) continue;
        n.vx *= DRAG; n.vy *= DRAG;
        // Never let the field go completely still — it should look alive even
        // when nobody has touched it.
        if (Math.hypot(n.vx, n.vy) < IDLE) {
          const a = Math.random() * Math.PI * 2;
          n.vx += Math.cos(a) * 0.06; n.vy += Math.sin(a) * 0.06;
        }
        n.x += n.vx; n.y += n.vy;
      }

      // 2. bubble-to-bubble: equal mass, so they swap the velocity along the
      // line between them. 30 bubbles is 435 pairs — nothing.
      for (let i = 0; i < list.length; i++) {
        for (let k = i + 1; k < list.length; k++) {
          const a = list[i], b = list[k];
          const ar = (a.el.offsetWidth || 78) / 2, br = (b.el.offsetWidth || 78) / 2;
          const dx = (b.x + br) - (a.x + ar), dy = (b.y + br) - (a.y + ar);
          const d = Math.hypot(dx, dy) || 0.01;
          const overlap = ar + br - d;
          if (overlap <= 0) continue;
          const nx = dx / d, ny = dy / d, push = overlap / 2;
          if (!a.dragging) { a.x -= nx * push; a.y -= ny * push; }
          if (!b.dragging) { b.x += nx * push; b.y += ny * push; }
          const avn = a.vx * nx + a.vy * ny, bvn = b.vx * nx + b.vy * ny;
          if (!a.dragging) { a.vx += (bvn - avn) * nx * REST; a.vy += (bvn - avn) * ny * REST; }
          if (!b.dragging) { b.vx += (avn - bvn) * nx * REST; b.vy += (avn - bvn) * ny * REST; }
        }
      }

      // 3. walls LAST, so a collision push can never shove one out of the
      // field. Clamping before collisions is what let them escape.
      for (const n of list) {
        const w = n.el.offsetWidth || 78, h = n.el.offsetHeight || 92;
        if (n.x < 0) { n.x = 0; if (!n.dragging) n.vx = Math.abs(n.vx) * REST; }
        if (n.y < 0) { n.y = 0; if (!n.dragging) n.vy = Math.abs(n.vy) * REST; }
        if (n.x + w > W) { n.x = W - w; if (!n.dragging) n.vx = -Math.abs(n.vx) * REST; }
        if (n.y + h > H) { n.y = H - h; if (!n.dragging) n.vy = -Math.abs(n.vy) * REST; }
        n.el.style.transform = `translate3d(${Math.round(n.x)}px, ${Math.round(n.y)}px, 0)`;
      }

      f.raf = requestAnimationFrame(tick);
    };
    f.raf = requestAnimationFrame(tick);
  }

  // Kept as the name the rest of the app calls.
  function fillBubbles(container, players, opts) {
    bubbleField(container, players, opts);
  }

  // Per-container lane state for the standings, so a re-render animates
  // existing rows instead of rebuilding them. Was USED but never declared:
  // every render() threw here, which silently killed everything downstream of
  // it — on the organizer panel that meant several controls never appeared.
  const raceState = new WeakMap();

  function fillRace(container, board, result) {
    if (!container) return;
    // `result` is only passed during a reveal: {deltas, answered} keyed by
    // player id. Outside a reveal the marks stay hidden and the rows are just
    // the running standings.
    let st = raceState.get(container);
    if (!st) { st = { lanes: new Map(), scores: new Map() }; raceState.set(container, st); }

    // Cheap early-out. Every phone in the room answering inside the same second
    // pushes a snapshot per answer, and re-writing thirty transforms per frame
    // buys nothing: if no score and no position changed, there is nothing to draw.
    const rsig = result
      ? board.map(p => `${result.deltas[p.id] ?? ""}${result.answered[p.id] ? "a" : ""}`).join(",")
      : "";
    const sig = board.map(p => `${p.id}:${p.score}`).join("|") + "#" + rsig;
    if (st.sig === sig) return;
    st.sig = sig;

    const lead = Math.max(1, ...board.map(p => p.score));
    const ordered = [];

    board.forEach((p, i) => {
      let lane = st.lanes.get(p.id);
      if (!lane) {
        lane = buildLane(p);
        st.lanes.set(p.id, lane);
      }
      const prev = st.scores.get(p.id);
      st.scores.set(p.id, p.score);

      lane.classList.toggle("leader", i === 0 && p.score > 0);
      lane.querySelector(".saddle").textContent = i + 1;
      lane.querySelector(".hname").textContent = p.name;
      lane.querySelector(".hscore").textContent = p.score;

      const mark = lane.querySelector(".hmark");
      if (mark) {
        if (!result) {
          mark.hidden = true;
          mark.className = "hmark";
        } else {
          const gained = result.deltas[p.id] || 0;
          const answered = !!result.answered[p.id];
          mark.hidden = false;
          // Three outcomes, not two: right, wrong, and never answered. A
          // player who ran out of time has not got it wrong, and being told
          // she did would be its own small injustice.
          mark.className = "hmark " + (gained > 0 ? "right" : answered ? "wrong" : "absent");
          mark.textContent = gained > 0 ? `✓ +${gained}` : answered ? "✗" : "—";
          mark.setAttribute("title", gained > 0 ? `Correct, +${gained}`
                                    : answered ? "Wrong" : "No answer");
        }
      }
      lane.setAttribute("aria-label", `${i + 1}. ${p.name}, ${p.score} points`);

      const horse = lane.querySelector(".horse");
      horse.style.setProperty("--frac", p.score / lead);
      if (prev !== undefined && p.score > prev) {
        horse.classList.remove("gained");
        void horse.offsetWidth;            // restart the flash
        horse.classList.add("gained");
      }
      ordered.push(lane);
    });

    // Appending an element that is already in the DOM moves it, so this reorders
    // without destroying anything.
    container.replaceChildren(...ordered);
    for (const [id, lane] of st.lanes) {
      if (!lane.isConnected) { st.lanes.delete(id); st.scores.delete(id); }
    }
  }

  function buildLane(p) {
    const lane = document.createElement("div");
    lane.className = "lane";
    lane.dataset.playerId = p.id;
    lane.dataset.silk = hashOf(p.id) % SILKS;

    const nm = document.createElement("span");
    nm.className = "hname";

    // Filled in only during a reveal: whether she got THIS question right, and
    // what it earned her. The running total alone does not tell her that.
    const mark = document.createElement("span");
    mark.className = "hmark";
    mark.hidden = true;

    // The track is its own container so the horse can be positioned as a
    // fraction of the TRACK's width in CSS (100cqw), not of its own width,
    // which is what a percentage translateX would mean. That keeps the movement
    // a pure GPU transform: no per-frame measuring, and no layout for thirty
    // lanes every time somebody scores.
    const track = document.createElement("span");
    track.className = "track";

    const horse = document.createElement("div");
    horse.className = "horse";
    const face = avatar(p);
    const saddle = document.createElement("span");
    saddle.className = "saddle";
    horse.append(face, saddle);
    track.appendChild(horse);

    const sc = document.createElement("span");
    sc.className = "hscore";

    lane.append(nm, mark, track, sc);
    return lane;
  }

  // ---- question rendering --------------------------------------------
  function showScreen(id) {
    ["screen-gone", "screen-lobby", "screen-question", "screen-waiting",
     "screen-reveal", "screen-leaderboard", "screen-final"].forEach(s => {
      const el = $(s);
      if (el) el.hidden = (s !== id);
    });
  }

  // A tap that moves a few pixels is a scroll as far as the browser is
  // concerned, and no click event ever fires — one of the everyday reasons a
  // phone button "needs tapping twice". So answer buttons also activate on
  // pointerup when the finger barely moved. answer() is idempotent per
  // question, so a click firing too is harmless.
  function onTap(el, fn) {
    let sx = 0, sy = 0, tracking = false;
    el.addEventListener("pointerdown", (e) => {
      tracking = true; sx = e.clientX; sy = e.clientY;
    });
    el.addEventListener("pointerup", (e) => {
      if (!tracking) return;
      tracking = false;
      if (Math.hypot(e.clientX - sx, e.clientY - sy) <= 12) fn();
    });
    el.addEventListener("pointercancel", () => { tracking = false; });
    el.addEventListener("click", fn);
  }

  // One tap is the whole interaction, so it gets immediate visible confirmation
  // here rather than waiting on the round trip. The server is still the one that
  // decides whether it counted.
  function markPicked(el) {
    if (!el) return;
    const group = el.parentElement;
    if (group) group.querySelectorAll(".picked").forEach(n => n.classList.remove("picked"));
    el.classList.add("picked");
  }

  function renderQuestion(s) {
    const q = s.question;
    if (!q) return;
    $("q-prompt").textContent = q.prompt;

    const img = $("q-image");
    img.hidden = !q.image;
    if (q.image) {
      // A question that names a picture nobody shipped must still be a question.
      // A broken-image placeholder where the photo should be reads as "your
      // phone is broken", and she has fifteen seconds to decide what it means.
      img.onerror = () => { img.hidden = true; };
      img.src = `static/questions/${q.image}`;
    }

    ["q-options", "q-truefalse", "q-wheel", "q-text", "q-players"]
      .forEach(id => { const el = $(id); if (el) el.hidden = true; });

    if (q.type === "multiple_choice") {
      const grid = $("q-options");
      grid.hidden = false;
      // A 2x2 grid is right for "Eddie / Sam / Tahoe" and wrong for options
      // that are whole sentences — at 129 characters a quarter of the screen
      // is unreadable. Long options stack full width instead.
      const longest = Math.max(0, ...(q.options || []).map((o) => String(o).length));
      grid.classList.toggle("stacked", longest > 42);
      grid.replaceChildren(...(q.options || []).map(opt => {
        const b = document.createElement("button");
        b.className = "opt";
        b.dataset.value = opt;
        b.textContent = opt;
        onTap(b, () => { markPicked(b); answer(opt); });
        return b;
      }));
    } else if (q.type === "true_false") {
      const tf = $("q-truefalse");
      tf.hidden = false;
      tf.querySelectorAll("[data-value]").forEach(b => {
        b.classList.remove("picked");
        b.onclick = () => { markPicked(b); answer(b.dataset.value === "true"); };
      });
    } else if (q.type === "number_wheel") {
      const box = $("q-wheel");
      box.hidden = false;
      const lo = q.min ?? 0, hi = q.max ?? 100;
      const input = $("wheel-input");
      input.min = lo;
      input.max = hi;
      // Step at a thousandth of the range, so a wheel from 0-10 is not a
      // three-position switch and one from 0-100000 is not a pixel hunt.
      input.step = Math.max(1, Math.round((hi - lo) / 1000));
      input.value = Math.round((lo + hi) / 2);
      $("wheel-min").textContent = lo;
      $("wheel-max").textContent = hi;
      $("wheel-value").textContent = input.value;
      input.oninput = () => { $("wheel-value").textContent = input.value; };
      $("wheel-submit").onclick = () => answer(Number(input.value));
    } else if (q.type === "free_text") {
      const box = $("q-text");
      box.hidden = false;
      const input = $("q-text-input");
      input.value = "";
      const submit = () => { if (input.value.trim()) answer(input.value.trim()); };
      $("q-text-submit").onclick = submit;
      input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    } else if (q.type === "pick_player") {
      const grid = $("q-players");
      grid.hidden = false;
      const others = s.players.filter(p => !p.seated && p.id !== (s.you && s.you.id));
      grid.replaceChildren(...others.map(p => {
        const b = bubble({ ...p, answered: true });
        b.classList.remove("in");
        b.dataset.value = p.id;
        b.setAttribute("role", "button");
        b.setAttribute("tabindex", "0");
        b.setAttribute("aria-label", p.name);
        const pick = () => { markPicked(b); answer(p.id); };
        b.onclick = pick;
        b.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
        };
        return b;
      }));
    }
  }

  function flashAnswerError(message) {
    const el = $("answer-error");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    document.querySelectorAll(".opt.picked, .tf.picked")
      .forEach((b) => b.classList.remove("picked"));
    clearTimeout(flashAnswerError._t);
    flashAnswerError._t = setTimeout(() => { el.hidden = true; }, 3500);
  }

  async function answer(value) {
    if (answeredQuestion === state.question.id) return;
    answeredQuestion = state.question.id;
    try {
      const r = await fetch(withToken("api/answer"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: value }),
      });
      if (!r.ok) {
        // Silence here was indistinguishable from a dead button: she tapped,
        // the highlight appeared, nothing happened, so she tapped again.
        answeredQuestion = null;
        const d = await r.json().catch(() => ({}));
        flashAnswerError(d.detail || "That didn't go through — tap again.");
      }
    } catch (e) {
      answeredQuestion = null;
      flashAnswerError("Lost the connection — tap again.");
    }
    refresh();
  }

  function tickRevealCountdown() {
    const box = $("reveal-countdown");
    if (!box) return;
    if (!state || state.phase !== "reveal" || !state.reveal_until) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const left = Math.max(0, state.reveal_until * 1000 - now()) / 1000;
    const total = state.reveal_seconds || 5;
    const bar = box.querySelector(".reveal-countdown-bar");
    if (bar) bar.style.transform = `scaleX(${Math.max(0, Math.min(1, left / total))})`;
    const text = box.querySelector(".reveal-countdown-text");
    if (text) {
      text.textContent = state.paused
        ? "Paused"
        : `Next question in ${Math.max(0, Math.ceil(left))}s`;
    }
  }

  let renderedAt = 0;
  async function markRendered() {
    const t = Date.now();
    if (t - renderedAt < 1000) return;      // at most once a second
    renderedAt = t;
    try {
      const r = await fetch(withToken("api/rendered"), { method: "POST" });
      if (!r.ok) return;
      const d = await r.json();
      // Apply it here rather than waiting for a snapshot: starting a clock
      // changes no shared state, so the server broadcasts nothing, and a phone
      // whose first POST was dropped would otherwise wait for someone else to
      // answer before it learned its own deadline.
      if (d.deadline && state) {
        state.you_deadline = d.deadline;
        if (d.server_time) clockOffset = d.server_time * 1000 - Date.now();
      }
    } catch (e) { /* offline; the tick will try again */ }
  }

  function tickWaitingClock() {
    const el = $("waiting-time");
    if (!el) return;
    const waiting = $("screen-waiting");
    if (!state || waiting.hidden || !state.waiting_until) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    if (state.paused) { el.textContent = "Paused"; return; }
    const left = Math.max(0, state.waiting_until * 1000 - now()) / 1000;
    el.textContent = left > 0
      ? `${Math.ceil(left)}s left`
      : "Any moment now…";
  }

  function tickTimer() {
    tickRevealCountdown();
    tickWaitingClock();
    // The retry lives here, not in render(): with nobody answering, no
    // snapshot arrives for seconds at a time, so a render-driven retry never
    // ran — which is exactly the case where the countdown looked frozen.
    if (state && state.phase === "question" && !state.you_seated &&
        !state.you_answered && !state.you_deadline) {
      markRendered();
    }
    if (!state || state.phase !== "question" || !state.you_deadline) return;
    if (state.paused) {
      const t = $("timer-text");
      if (t) t.textContent = "❚❚";
      return;
    }
    const left = Math.max(0, state.you_deadline * 1000 - now()) / 1000;

    // Her time is up and she did not answer. Done HERE rather than by calling
    // render(): render is snapshot-driven and no snapshot arrives while the
    // room is quiet, and an earlier attempt that latched a flag then delegated
    // to render could miss on the boundary comparison and then never retry —
    // leaving her on a dead question screen showing 0, which is exactly the
    // "stuck timer" being reported.
    if (left <= 0 && !state.you_answered && !state.you_seated) {
      const waiting = $("screen-waiting");
      if (waiting && waiting.hidden) {
        showScreen("screen-waiting");
        const p = state.progress || { answered: 0, total: 0 };
        $("bubble-count").textContent =
          `Out of time — waiting for the rest of the room. ${p.answered} of ${p.total} in`;
        fillBubbles($("bubbles"), state.players || []);
      }
      return;
    }
    const text = $("timer-text");
    if (text) text.textContent = Math.ceil(left);
    const urgent = left <= 5;
    const box = $("timer");
    if (box) box.classList.toggle("urgent", urgent);
    const ring = document.querySelector("#timer-ring .ring-fg");
    if (ring) {
      const frac = Math.max(0, Math.min(1, left / (state.timer_seconds || 15)));
      const c = 2 * Math.PI * 17;
      ring.style.strokeDasharray = `${c}`;
      ring.style.strokeDashoffset = `${c * (1 - frac)}`;
      // Colour AND stroke width, so the last five seconds read without hue.
      ring.classList.toggle("urgent", urgent);
    }
  }

  function renderReveal(s) {
    const r = s.reveal || {};
    const correct = Array.isArray(r.correct) ? r.correct[0] : r.correct;
    const head = $("reveal-correct");
    const answer = Object.assign(document.createElement("span"), {
      className: "reveal-answer",
      // pick_player has no right answer — it scores by how many people agreed —
      // so it gets a statement where the others get a label and a value.
      textContent: s.question && s.question.type === "pick_player"
        ? "The room has spoken" : String(correct),
    });
    head.replaceChildren(
      ...(s.question && s.question.type === "pick_player"
        ? [] : [document.createTextNode("The answer was")]),
      answer,
    );
    const pts = $("reveal-points");
    pts.textContent = r.your_points > 0 ? `+${r.your_points}` : "Nothing that time";
    pts.classList.toggle("zero", !(r.your_points > 0));
  }

  function render(s) {
    // A new build is live and this phone is still running the old one. Reload
    // it rather than leaving someone on stale code all night — this has been
    // the single most common cause of "it still looks broken" today.
    if (s.build && window.BARGAMES_BUILD && s.build !== window.BARGAMES_BUILD) {
      try {
        const last = Number(sessionStorage.getItem("bg_reloaded") || 0);
        if (Date.now() - last > 30000) {        // never loop
          sessionStorage.setItem("bg_reloaded", String(Date.now()));
          location.reload();
          return;
        }
      } catch (e) { /* storage blocked; stay put rather than risk a loop */ }
    }

    state = s;
    if (s.server_time) clockOffset = s.server_time * 1000 - Date.now();

    // The server does not know this phone: it is left over from a game that
    // has been replaced. Say so, rather than showing her a lobby she is not in.
    if (s.you_known === false) {
      try { localStorage.removeItem("bg_token"); } catch (e) {}
      showScreen("screen-gone");
      const n = $("you-name"), sc = $("you-score");
      if (n) n.textContent = "";
      if (sc) sc.textContent = "";
      return;
    }

    if (s.you) {
      const n = $("you-name"), sc = $("you-score");
      if (n) n.textContent = s.you.name;
      if (sc) sc.textContent = s.you.score;
    }
    const pl = $("progress-label");
    if (pl && s.progress_label) {
      const p = s.progress_label;
      pl.textContent = `Round ${p.round} of ${p.rounds} · Question ${p.question} of ${p.questions}`;
    }

    const edit = $("btn-edit-me"), sheet = $("edit-sheet");
    if (edit) edit.hidden = !(s.phase === "lobby") || (sheet && !sheet.hidden);
    if (sheet && s.phase !== "lobby") sheet.hidden = true;

    if (s.phase === "lobby") {
      showScreen("screen-lobby");
      fillBubbles($("lobby-roster"), s.players, { neutral: true });
    } else if (s.phase === "question") {
      if (s.you_seated) {
        showScreen("screen-waiting");
        $("bubble-count").textContent = "You're in — you start on the next question.";
        fillBubbles($("bubbles"), s.players);
      } else if (s.you_answered) {
        showScreen("screen-waiting");
        const p = s.progress;
        $("bubble-count").textContent = `${p.answered} of ${p.total} in`;
        fillBubbles($("bubbles"), s.players);
      } else if (s.you_deadline && now() > s.you_deadline * 1000) {
        // Her own clock has run out and she did not answer. She used to be
        // left on the question with a dead 0 and a grid that still looked
        // tappable but silently failed — which is what "the timer gets stuck"
        // looks like from the other side of the screen.
        showScreen("screen-waiting");
        const p = s.progress;
        $("bubble-count").textContent =
          `Out of time — waiting for the rest of the room. ${p.answered} of ${p.total} in`;
        fillBubbles($("bubbles"), s.players);
      } else {
        showScreen("screen-question");
        if (renderedQuestion !== s.question.id) {
          renderedQuestion = s.question.id;
          answeredQuestion = null;
          renderQuestion(s);
          // Clear the previous question's number so a stale one never sits
          // there looking frozen while we wait for a deadline.
          const t = $("timer-text");
          if (t) t.textContent = "–";
        }
        // Tell the server this phone has it on screen; THIS is what starts her
        // own clock. Kept OUTSIDE the render check and retried while the
        // deadline is still missing: it was fire-and-forget, so one dropped
        // POST on bar wifi meant no deadline ever arrived and the countdown
        // sat frozen for the whole question.
        if (!s.you_deadline) markRendered();
        tickTimer();
      }
    } else if (s.phase === "reveal") {
      showScreen("screen-reveal");
      renderReveal(s);
      fillRace($("race"), s.leaderboard, {
        deltas: (s.reveal && s.reveal.deltas) || {},
        answered: Object.fromEntries((s.players || []).map(p => [p.id, p.answered])),
      });
      renderedQuestion = null;
      tickRevealCountdown();
    } else if (s.phase === "round_leaderboard") {
      showScreen("screen-leaderboard");
      const done = $("round-done"), pl = s.progress_label;
      if (done && pl) {
        // round_index has not advanced yet, so this is the round that ended.
        done.textContent = `Round ${pl.round} of ${pl.rounds} is over`;
      }
      fillRace($("leaderboard"), s.leaderboard);
      renderedQuestion = null;
    } else if (s.phase === "final") {
      showScreen("screen-final");
      fillRace($("final-leaderboard"), s.leaderboard);
      announceResult(s);
    }
  }

  // ---- the ending -----------------------------------------------------
  let celebrated = false;

  function announceResult(s) {
    const headline = $("final-headline"), sub = $("final-subline");
    if (!headline) return;
    const winners = s.winners || [];
    const me = s.you && s.you.id;
    const iWon = me && winners.includes(me);
    const names = (s.leaderboard || [])
      .filter((p) => winners.includes(p.id)).map((p) => p.name);

    if (iWon && winners.length > 1) {
      headline.textContent = "You tied for first!";
      sub.textContent = `Joint winners: ${names.join(" and ")}.`;
    } else if (iWon) {
      headline.textContent = "You won!";
      sub.textContent = `${s.you.score} points. Nobody else got close enough.`;
    } else if (names.length) {
      headline.textContent = "That's the game";
      sub.textContent = names.length > 1
        ? `${names.join(" and ")} tied for first.`
        : `${names[0]} wins.`;
    } else {
      headline.textContent = "That's the game";
      sub.textContent = "Nobody scored. Extraordinary.";
    }

    if (iWon && !celebrated) {
      celebrated = true;
      celebrate();
    }
  }

  function celebrate() {
    // Animated with the Web Animations API rather than a stylesheet: no
    // library, no CDN, and nothing added to style.css.
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    const colours = ["#ffb000", "#23c16b", "#ff5fa2", "#4aa8ff", "#f4f1ff"];
    const layer = document.createElement("div");
    layer.setAttribute("aria-hidden", "true");     // decorative; never announced
    layer.style.cssText =
      "position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:9999";
    document.body.appendChild(layer);

    for (let i = 0; i < 90; i++) {
      const bit = document.createElement("div");
      const size = 6 + Math.random() * 7;
      bit.style.cssText =
        `position:absolute;top:-24px;left:${Math.random() * 100}%;` +
        `width:${size}px;height:${size * (0.4 + Math.random())}px;` +
        `background:${colours[i % colours.length]};` +
        `border-radius:${Math.random() < 0.4 ? "50%" : "2px"};`;
      layer.appendChild(bit);
      bit.animate(
        [
          { transform: `translate3d(0,0,0) rotate(0deg)`, opacity: 1 },
          { transform: `translate3d(${(Math.random() - 0.5) * 160}px,` +
                       `${window.innerHeight + 80}px,0) ` +
                       `rotate(${(Math.random() - 0.5) * 1080}deg)`, opacity: 0.9 },
        ],
        {
          duration: 2600 + Math.random() * 1800,
          delay: Math.random() * 700,
          easing: "cubic-bezier(.22,.7,.3,1)",
          fill: "forwards",
        }
      );
    }
    // Clean up: leaving 90 absolutely-positioned nodes on the page would keep
    // compositing work alive for as long as the phone stays on this screen.
    setTimeout(() => layer.remove(), 5600);
  }

  async function refresh() {
    try {
      const r = await fetch(withToken("api/state"));
      if (r.ok) render(await r.json());
    } catch (e) {}
  }

  function connect() {
    if (es) es.close();
    es = new EventSource(withToken("api/stream"));
    es.onmessage = (ev) => { try { render(JSON.parse(ev.data)); } catch (e) {} };
    // EventSource reconnects on its own; this only re-syncs the snapshot so a
    // gap can never leave the screen showing stale truth.
    es.onerror = () => setTimeout(refresh, 1000);
  }

  function initEditMe() {
    const open = $("btn-edit-me"), sheet = $("edit-sheet");
    if (!open || !sheet) return;
    const nameIn = $("edit-name"), fileIn = $("edit-selfie");
    const drop = $("edit-selfie-drop"), err = $("edit-error");

    open.onclick = () => {
      nameIn.value = (state && state.you && state.you.name) || "";
      err.hidden = true;
      sheet.hidden = false;
      open.hidden = true;
      // Deliberately NOT focusing the name field: that threw the keyboard up
      // over the sheet, which is half the screen gone for someone who only
      // wanted to change her photo. She taps the field if she wants it.
      sheet.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    const close = () => { sheet.hidden = true; open.hidden = false; };
    $("btn-edit-cancel").onclick = close;

    // Same object-URL preview as joining: the photo never leaves the page
    // until she saves.
    fileIn.onchange = () => {
      const f = fileIn.files[0];
      if (!f) return;
      const url = URL.createObjectURL(f);
      const img = document.createElement("img");
      img.alt = "";
      img.onload = () => URL.revokeObjectURL(url);
      img.src = url;
      drop.replaceChildren(img);
      drop.classList.add("filled");
    };

    $("btn-edit-save").onclick = async () => {
      err.hidden = true;
      if (!nameIn.value.trim()) {
        err.textContent = "Add a name so people know who you are.";
        err.hidden = false;
        return;
      }
      const fd = new FormData();
      fd.append("name", nameIn.value.trim());
      if (fileIn.files[0]) fd.append("selfie", fileIn.files[0]);
      $("btn-edit-save").disabled = true;
      $("btn-edit-save").textContent = "Saving…";
      try {
        const r = await fetch(withToken("api/profile"), { method: "POST", body: fd });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          err.textContent = r.status === 403
            ? "You're not in this game any more — join again to play."
            : (d.detail || "Could not save that.");
          err.hidden = false;
          return;
        }
        fileIn.value = "";
        drop.replaceChildren(document.createTextNode("+"));
        drop.classList.remove("filled");
        close();
        refresh();
      } catch (e) {
        err.textContent = "Network problem — try again.";
        err.hidden = false;
      } finally {
        $("btn-edit-save").disabled = false;
        $("btn-edit-save").textContent = "Save";
      }
    };
  }

  function initPlay() {
    initEditMe();
    const t = token();
    if (!t) { location.href = "."; return; }
    if (!new URLSearchParams(location.search).get("t")) {
      history.replaceState({}, "", `play?t=${encodeURIComponent(t)}`);
    }
    refresh();
    connect();
    setInterval(tickTimer, 100);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { refresh(); connect(); }
    });
  }

  function initAdminEntry() {
    const entry = $("admin-entry"), box = $("admin-prompt");
    if (!entry || !box) return;
    const input = $("admin-prompt-input"), err = $("admin-prompt-error");

    const open = () => {
      box.hidden = false;
      entry.setAttribute("aria-expanded", "true");
      input.focus();
    };
    const close = () => {
      box.hidden = true;
      entry.setAttribute("aria-expanded", "false");
      err.hidden = true;
      input.value = "";
    };

    entry.onclick = () => (box.hidden ? open() : close());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !box.hidden) close();
    });
    // A guest who taps it by accident should be able to get rid of it without
    // hunting for a close button.
    document.addEventListener("click", (e) => {
      if (!box.hidden && !box.contains(e.target) && !entry.contains(e.target)) close();
    });

    const submit = async () => {
      err.hidden = true;
      const fd = new FormData();
      fd.append("password", input.value);
      $("admin-prompt-go").disabled = true;
      try {
        const r = await fetch("api/admin/login", { method: "POST", body: fd });
        if (r.ok) { location.href = "admin"; return; }
        const d = await r.json().catch(() => ({}));
        err.textContent = r.status === 429
          ? (d.detail || "Too many attempts. Wait a minute.")
          : "That password isn't right.";
        err.hidden = false;
        input.select();
      } catch (e) {
        err.textContent = "Network problem — try again.";
        err.hidden = false;
      } finally {
        $("admin-prompt-go").disabled = false;
      }
    };

    $("admin-prompt-go").onclick = submit;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  }

  function initJoin() {
    initAdminEntry();

    const steps = ["step-pin", "step-name", "step-selfie"];
    const err = $("join-error");

    function show(id) {
      steps.forEach((s) => { const el = $(s); if (el) el.hidden = (s !== id); });
      err.hidden = true;
      // Keep the error next to the control that caused it. It used to live at
      // the bottom of the card, which on a tall phone put it well below the
      // fold — so a rejected PIN looked like a button that simply did nothing.
      const active = $(id);
      if (active && err.parentNode !== active) active.appendChild(err);
      const focusable = { "step-pin": "pin-input", "step-name": "name-input" }[id];
      if (focusable) $(focusable).focus();
    }

    function fail(message) {
      err.textContent = message;
      err.hidden = false;
      if (err.scrollIntoView) err.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    // ---- step 1: the PIN, checked before we ask for anything else ----
    async function submitPin() {
      const pin = $("pin-input").value.trim();
      if (!/^[0-9]{4}$/.test(pin)) return fail("The PIN is four digits.");
      $("btn-pin-next").disabled = true;
      try {
        const r = await fetch(`api/pin/${encodeURIComponent(pin)}`);
        const d = await r.json().catch(() => ({ ok: false }));
        if (!d.ok) return fail("That PIN doesn't match a game. Check the organizer's phone.");
        show("step-name");
      } catch (e) {
        fail("Network problem — try again.");
      } finally {
        $("btn-pin-next").disabled = false;
      }
    }

    $("btn-pin-next").onclick = submitPin;
    $("pin-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitPin();
    });
    // NO auto-advance on the fourth digit. It saves one tap and costs
    // predictability: the screen moves out from under a thumb that was still
    // travelling to Continue, and a mistyped fourth digit fires a wrong-PIN
    // error before she has finished typing. Enter still works.

    // ---- step 2: the name ----
    function submitName() {
      if (!$("name-input").value.trim()) return fail("Add a name so people know who you are.");
      show("step-selfie");
    }
    $("btn-name-next").onclick = submitName;
    $("name-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitName();
    });
    $("btn-name-back").onclick = () => show("step-pin");

    // ---- step 3: the photo, or not ----
    // Show her the photo she just took. It never leaves the page — the preview
    // is an object URL, revoked as soon as the image has decoded, so no selfie
    // is ever copied into a data URI or held in storage.
    //
    // (This is ui-studio's code, restored: I deleted it by rewriting the whole
    // of initJoin for the wizard instead of editing around it. Taking a photo
    // then appeared to do nothing at all.)
    const fileInput = $("selfie-input"), drop = $("selfie-drop");
    if (fileInput && drop) {
      fileInput.onchange = () => {
        const f = fileInput.files[0];
        if (!f) return;
        const url = URL.createObjectURL(f);
        const img = document.createElement("img");
        img.alt = "";
        img.onload = () => URL.revokeObjectURL(url);
        img.src = url;
        drop.replaceChildren(img);
        drop.classList.add("filled");
        // Once there is a photo, "Join the game" is the obvious action and
        // "Skip for now" is not what she means any more.
        const skip = $("btn-skip-selfie");
        if (skip) skip.hidden = true;
      };
    }

    $("btn-selfie-back").onclick = () => show("step-name");

    async function join({ withPhoto }) {
      err.hidden = true;
      const fd = new FormData();
      fd.append("pin", $("pin-input").value.trim());
      fd.append("name", $("name-input").value.trim());
      const file = withPhoto ? $("selfie-input").files[0] : null;
      if (file) fd.append("selfie", file);

      $("btn-join").disabled = true;
      $("btn-skip-selfie").disabled = true;
      try {
        const r = await fetch("api/join", { method: "POST", body: fd });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          fail(d.detail || "Could not join.");
          // A PIN that has gone stale (organizer started a new game) is the
          // one failure worth sending her back a screen for.
          if (r.status === 400) show("step-pin");
          return;
        }
        const d = await r.json();
        try { localStorage.setItem("bg_token", d.token); } catch (e) {}
        location.href = `play?t=${encodeURIComponent(d.token)}`;
      } catch (e) {
        fail("Network problem — try again.");
      } finally {
        $("btn-join").disabled = false;
        $("btn-skip-selfie").disabled = false;
      }
    }

    $("btn-join").onclick = () => join({ withPhoto: true });
    $("btn-skip-selfie").onclick = () => join({ withPhoto: false });

    // A link with the PIN already in it should not make her retype it.
    if (/^[0-9]{4}$/.test($("pin-input").value.trim())) {
      show("step-name");
    } else {
      show("step-pin");
    }
  }

  return { initPlay, initJoin, avatar, bubble, fillBubbles, fillRace, $ };
})();
