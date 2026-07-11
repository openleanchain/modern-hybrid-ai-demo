const $ = (id) => document.getElementById(id);

const STORIES = [
  {
    id: "investigate",
    label: "Billing investigation",
    message: "My last three invoices look wrong - figure out what happened and propose a fix.",
    overview: "A customer says recent invoices look wrong. Watch the system route the case into investigation, check evidence, explain the finding, and stop before a risky change.",
    guide: ["Customer problem", "Route the case", "Investigate evidence", "Show the finding", "Safety checkpoint", "Takeaway"],
  },
  {
    id: "balance",
    label: "Balance from records",
    message: "What's my current balance?",
    overview: "A customer asks for a current account fact. Watch the system avoid unnecessary model work and answer from company records.",
    guide: ["Customer problem", "Route the case", "Read records", "Answer directly", "Safety checkpoint", "Takeaway"],
  },
  {
    id: "policy",
    label: "Policy with evidence",
    message: "Why did my plan change and what is the refund policy?",
    overview: "A customer asks for policy-aware help. Watch the system gather policy evidence and produce a grounded answer.",
    guide: ["Customer problem", "Route the case", "Find policy evidence", "Draft response", "Safety checkpoint", "Takeaway"],
  },
  {
    id: "refund",
    label: "Refund approval",
    message: "I was overcharged on my latest invoice - please refund the difference.",
    overview: "A customer asks for money to change hands. Watch the system validate and stage the action instead of casually committing it.",
    guide: ["Customer problem", "Detect risk", "Validate action", "Stage proposal", "Approval checkpoint", "Takeaway"],
  },
  {
    id: "outage",
    label: "Model outage",
    message: "What plan am I on?",
    overview: "A customer needs help while the model path is unavailable. Watch what still works and what degrades safely.",
    guide: ["Customer problem", "Service health", "Route safely", "Use backup path", "Resilience checkpoint", "Takeaway"],
    outage: true,
  },
  {
    id: "memory",
    label: "Tiered memory",
    overview: "A longer conversation continues over many turns. Watch memory provide continuity while records remain the source of truth.",
    guide: ["Conversation starts", "Load memory", "Keep recent turns", "Compress when needed", "Refresh records", "Takeaway"],
    memory: true,
  },
];

const REPLAY = [
  "Hi, I have a question about my account.",
  "What plan am I on?",
  "What's my current balance?",
  "Why is my balance not zero?",
  "What was my last payment?",
  "When was my plan changed?",
  "What does the proration policy say?",
  "Is there a cancellation fee?",
  "What is the refund policy?",
  "Which invoices are still open?",
  "Can you recap what we've covered?",
  "Anything else about billing?",
];

const LANES = {
  1: { name: "Tier 1", plain: "Records fast lane", node: "tier1" },
  2: { name: "Tier 2", plain: "Policy reasoning lane", node: "tier2" },
  3: { name: "Tier 3", plain: "Investigation lane", node: "tier3" },
};

let selected = STORIES[0];
let steps = overviewSteps(selected);
let index = 0;
let autoTimer = null;
let autoRunning = false;
let memoryPlayback = null;

const personaSel = $("persona");
window.PERSONAS.forEach((p, i) => {
  const option = document.createElement("option");
  option.value = i;
  option.textContent = `${p.name} - ${p.org} (${p.customer})`;
  personaSel.appendChild(option);
});

const persona = () => window.PERSONAS[personaSel.value || 0];
const sessKey = () => `hybrid.sess.${persona().tenant}.${persona().customer}`;
const getSession = () => localStorage.getItem(sessKey());
const setSession = (id) => localStorage.setItem(sessKey(), id);

personaSel.addEventListener("change", () => {
  stopAutoRun();
  $("messages").innerHTML = "";
  $("trace").innerHTML = "";
  steps = overviewSteps(selected);
  index = 0;
  renderAll();
  resumeMemory();
});

function initStorySelect() {
  $("story-select").innerHTML = STORIES.map((story) =>
    `<option value="${escapeHtml(story.id)}">${escapeHtml(story.label)}</option>`
  ).join("");
  $("story-select").value = selected.id;
  $("story-select").addEventListener("change", () => {
    stopAutoRun();
    selected = STORIES.find((story) => story.id === $("story-select").value) || STORIES[0];
    memoryPlayback = null;
    steps = overviewSteps(selected);
    index = 0;
    renderAll();
  });
}

async function refreshHealth() {
  try {
    const res = await fetch("/api/health").then((r) => r.json());
    const up = res.llm && res.llm.reachable;
    $("llm-dot").className = "dot " + (up ? "ok" : "down");
    $("llm-label").textContent = up ? `model ${res.llm.mode}` : "model service down";
    renderResilience(res.resilience || {});
  } catch {
    $("llm-dot").className = "dot down";
    $("llm-label").textContent = "main system unreachable";
  }
}

function overviewSteps(story) {
  return [
    {
      kicker: "Overview",
      title: story.label,
      body: story.overview,
      note: story.memory ? "This story replays 12 turns." : `Customer moment: "${story.message}"`,
      behindTitle: "How to watch this story",
      behind: [
        "Use Next and Back for manual presentation.",
        "Use Auto-Run to advance with pauses.",
        "Open optional evidence only when the audience asks.",
      ],
      nodes: [],
    },
  ];
}

function buildStepsFromResult(story, res) {
  const route = res.route || {};
  const lane = LANES[route.tier] || LANES[2];
  const traceLines = traceSteps(res.trace).slice(0, 5);
  const fallback = res.resilience && res.resilience.served_by_fallback;

  return [
    {
      kicker: "1. Customer problem",
      title: story.guide[0],
      body: story.memory ? "A long conversation unfolds over multiple turns." : "The system receives one business case, not an open-ended chat.",
      note: story.memory ? "The memory replay sent 12 customer turns." : story.message,
      behindTitle: "Case received",
      behind: ["Load case context.", "Check input safety.", "Prepare to route."],
      nodes: ["harness"],
    },
    {
      kicker: "2. Routing",
      title: story.memory ? "Load memory and keep routing normally" : `${lane.name}: ${lane.plain}`,
      body: story.memory ? "Each turn still goes through the enterprise harness." : (route.reason || "The enterprise harness selected the handling lane."),
      note: story.memory ? memoryLine(res.memory) : `Risk: ${route.risk || "unknown"} / Complexity: ${route.complexity || "unknown"}`,
      behindTitle: "Path selected",
      behind: story.memory
        ? ["Open session context.", "Use memory as context.", "Route each turn normally."]
        : [`Selected ${lane.plain}.`, "Risk and complexity are visible.", "The route is not hidden inside a chat transcript."],
      nodes: story.memory ? ["harness", "memory"] : ["harness", lane.node],
    },
    {
      kicker: "3. Internal work",
      title: story.memory ? "Recent turns stay readable" : route.tier === 3 ? "Agent Local-Harness investigates" : story.guide[2],
      body: story.memory
        ? "The newest turns stay detailed so the case can continue naturally."
        : route.tier === 3
          ? "The local harness controls a bounded investigation loop around one Tier 3 agent."
          : "The selected lane gathers the fact or policy evidence needed for a controlled answer.",
      note: story.memory ? recentTurns(res.memory) : (traceLines.join("\n") || "No detailed trace was returned."),
      behindTitle: story.memory ? "Raw turn buffer" : route.tier === 3 ? "Investigation workbench" : "Evidence checks",
      behind: story.memory
        ? ["Count turns, not messages.", "Checkpoint every turn.", "Keep recent context detailed."]
        : route.tier === 3
          ? ["Plan the next check.", "Use shared tools.", "Observe results.", "Stay within budget."]
          : ["Use governed tools.", "Ground response in evidence.", "Keep trace available."],
      nodes: story.memory ? ["harness", "memory"] : route.tier === 3 ? ["harness", "tier3", "agent"] : ["harness", lane.node],
    },
    {
      kicker: "4. What happened",
      title: story.memory ? memoryCompressionTitle(res.memory) : fallback ? "Backup path used" : story.guide[3],
      body: story.memory ? memoryCompressionBody(res.memory) : fallback ? fallbackText(res) : (res.text || "No response text returned."),
      note: story.memory ? memorySummary(res.memory) : fallback ? (res.text || "") : outcomeNote(res),
      behindTitle: story.memory ? "Tiered memory" : fallback ? "Resilience" : "Customer-facing outcome",
      behind: story.memory
        ? ["Compress older context when needed.", "Keep raw turns if compression waits.", "Do not store live figures as authority."]
        : fallback
          ? ["Model path was unavailable.", "Fallback answered or queued safely.", "The degradation is visible."]
          : ["Answer is business-readable.", "Evidence remains optional.", "The stage keeps attention on the story."],
      nodes: story.memory ? ["harness", "memory", "tier2"] : fallback ? ["harness", lane.node] : ["harness", lane.node, route.tier === 3 ? "agent" : lane.node],
    },
    {
      kicker: "5. Control point",
      title: story.memory ? "Records remain the source of truth" : res.requires_confirmation ? "Approval required" : story.guide[4],
      body: story.memory
        ? "Memory provides continuity, but balances, invoices, plans, and status are refreshed from records."
        : res.requires_confirmation
          ? "A proposed action is waiting for confirmation. The system did not commit the change on its own."
          : "If the case asks for money or account status to change, the write gate validates and stages the action.",
      note: story.memory ? "Memory is context, not authority." : "This is where the system proves it knows when to stop.",
      behindTitle: story.memory ? "Trust rule" : "Safety checkpoint",
      behind: story.memory
        ? ["Memory carries context.", "Records carry facts.", "Guardrails protect business values."]
        : ["Risky writes require validation.", "Confirmation is explicit.", "Audit and trace remain available."],
      nodes: story.memory ? ["harness", "memory", "tier1"] : ["harness", lane.node, "gate"],
    },
    {
      kicker: "6. Takeaway",
      title: story.memory ? "Long cases stay coherent without becoming loose chat" : "The system handles cases, not just messages",
      body: story.memory
        ? "The same guided pattern explains memory: load context, route the case, keep recent turns, summarize older context, and refresh facts from records."
        : "The visitor sees a business case move through routing, evidence, outcome, and control points on one page.",
      note: "Choose another story from the dropdown to compare paths without changing the page.",
      behindTitle: "Next",
      behind: ["Pick another story.", "Use auto-run for presentation.", "Open evidence only when needed."],
      nodes: story.memory ? ["harness", "memory", "tier1", "tier2"] : ["harness", lane.node, route.tier === 3 ? "agent" : lane.node, "gate"],
    },
  ];
}

function renderAll() {
  renderOverview();
  renderStep();
  renderGuide();
}

function renderOverview() {
  $("overview-title").textContent = selected.label;
  $("overview-copy").textContent = selected.overview;
  $("prepare-story").textContent = selected.memory ? "Run Memory Story" : "Run Story";
}

function renderGuide() {
  const activeGuide = steps[index] && steps[index].guideIndex != null ? steps[index].guideIndex : Math.min(index, selected.guide.length - 1);
  $("guide-list").innerHTML = selected.guide.map((item, i) =>
    `<li class="${i === activeGuide ? "active" : ""}">${escapeHtml(item)}</li>`
  ).join("");
}

function renderStep() {
  const step = steps[index];
  $("step-count").textContent = `Step ${index + 1} of ${steps.length}`;
  $("progress-fill").style.width = `${((index + 1) / steps.length) * 100}%`;
  $("stage-kicker").textContent = step.kicker;
  $("stage-title").textContent = step.title;
  $("stage-body").textContent = step.body;
  $("stage-note").textContent = step.note || "";
  $("behind-title").textContent = step.behindTitle || "Behind the scenes";
  $("behind-list").innerHTML = (step.behind || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelectorAll(".lane-strip span").forEach((el) => {
    el.classList.toggle("active", (step.nodes || []).includes(el.dataset.node));
  });
  $("prev-step").disabled = index === 0;
  $("next-step").textContent = index === steps.length - 1 ? "Restart" : "Next";
  updateMemoryStripForStep(step);
  renderGuide();
}

async function prepareSelectedStory() {
  stopAutoRun();
  $("prepare-story").disabled = true;
  $("prepare-story").textContent = selected.memory ? "Running memory..." : "Running...";
  memoryPlayback = null;
  steps = [{
    kicker: "Running",
    title: selected.memory ? "Replaying the memory story." : "The system is handling this case.",
    body: "The guided steps will appear when the system returns.",
    note: selected.memory ? "Running 12 turns with short pauses." : selected.message,
    behindTitle: "In progress",
    behind: ["Receive case.", "Route path.", "Gather evidence."],
    nodes: ["harness"],
  }];
  index = 0;
  renderStep();

  if (selected.memory) {
    memoryPlayback = { executed: new Set(), latest: null };
    steps = memoryPlaybackSteps();
    index = 0;
    renderStep();
  } else {
    const result = await runSelectedCase(selected);
    if (result) {
      steps = buildStepsFromResult(selected, result);
      index = 0;
      renderStep();
    }
  }
  $("prepare-story").disabled = false;
  $("prepare-story").textContent = selected.memory ? "Run Memory Story" : "Run Story";
}

async function runSelectedCase(story) {
  if (story.outage) await setChaos(true);
  const result = await runCase(story.message);
  if (story.outage) setTimeout(() => setChaos(false), 1200);
  return result;
}

async function runCase(message) {
  addMessage(message, "customer");
  const p = persona();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tenant_id: p.tenant,
        customer_id: p.customer,
        message,
        session_id: getSession(),
      }),
    }).then((r) => r.json());
    if (res.session_id) setSession(res.session_id);
    addMessage(res.text || "No response text returned.", "system", responseTags(res));
    renderRouteSummary(res.route, res.trace, res.guard);
    renderTrace(res.trace);
    renderMemory(res.memory);
    if (res.resilience) renderResilience(res.resilience);
    return res;
  } catch {
    steps = [{
      kicker: "Connection issue",
      title: "The story could not run.",
      body: "The browser could not reach the main system.",
      note: "Confirm the Flask app is running.",
      behindTitle: "What happened",
      behind: ["The request to /api/chat failed."],
      nodes: [],
    }];
    index = 0;
    renderStep();
    return null;
  }
}

function memoryPlaybackSteps() {
  const out = [{
    kicker: "Memory overview",
    title: "Watch the raw-turn buffer fill.",
    body: "Each Next click records one real turn. Auto-run does the same thing with a pause between turns.",
    note: "The rail below will move: 1, 2, 3 ... summarize ... then the next batch continues.",
    behindTitle: "Synchronized memory demo",
    behind: ["Each step records a real turn.", "The rail updates after the backend response.", "Summarization appears when the system actually reports it."],
    nodes: ["harness", "memory"],
    guideIndex: 0,
  }];

  REPLAY.forEach((message, i) => {
    out.push({
      kicker: `Turn ${i + 1}`,
      title: `Record customer turn ${i + 1}`,
      body: "This pause corresponds to one real user + assistant exchange being checkpointed.",
      note: message,
      behindTitle: "Real memory step",
      behind: ["Send one turn to /api/chat.", "Persist the raw turn.", "Update the memory rail from the returned snapshot."],
      nodes: ["harness", "memory"],
      guideIndex: i < 3 ? 1 : i < 9 ? 2 : 3,
      turnIndex: i,
    });
  });

  out.push({
    kicker: "Takeaway",
    title: "Memory is visible as a changing state, not a hidden chat log.",
    body: "The audience can see raw turns accumulate, the summarize checkpoint activate, and the next batch continue.",
    note: "Business facts still refresh from records when a case needs them.",
    behindTitle: "Trust rule",
    behind: ["Memory carries context.", "Records carry facts.", "The rail changes only when real memory state changes."],
    nodes: ["harness", "memory", "tier1"],
    guideIndex: 5,
  });
  return out;
}

async function executeMemoryTurn(step) {
  if (!memoryPlayback) memoryPlayback = { executed: new Set(), latest: null };
  if (memoryPlayback.executed.has(step.turnIndex)) return;

  $("stage-title").textContent = `Recording turn ${step.turnIndex + 1}`;
  $("stage-body").textContent = "Sending this turn now. The rail will update when the backend returns the new memory snapshot.";
  $("stage-note").textContent = REPLAY[step.turnIndex];

  const result = await runCase(REPLAY[step.turnIndex]);
  memoryPlayback.executed.add(step.turnIndex);
  memoryPlayback.latest = result;
  if (!result || !result.memory) return;

  const memory = result.memory;
  const compressed = !!memory.compressed_this_turn;
  step.title = compressed
    ? `Turn ${step.turnIndex + 1} recorded, then summarized`
    : `Turn ${step.turnIndex + 1} recorded`;
  step.body = compressed
    ? "This pause shows the exact moment older turns were folded into the running summary."
    : "This pause shows one raw turn being added to the memory buffer.";
  step.note = memoryStatusLine(memory);
  step.behind = compressed
    ? ["The turn was checkpointed.", "The oldest batch was summarized.", "The raw buffer now starts the next cycle."]
    : ["The turn was checkpointed.", "The raw buffer advanced by one.", "No summarization happened on this pause."];
  step.guideIndex = compressed ? 3 : step.guideIndex;
}

async function setChaos(down) {
  try {
    await fetch("/api/chaos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ down }),
    });
  } catch {
    // Physical service outages are valid demonstrations too.
  }
  await refreshHealth();
}

async function nextStep() {
  if (index === steps.length - 1) await goToStep(0);
  else await goToStep(index + 1);
}

async function prevStep() {
  await goToStep(Math.max(0, index - 1));
}

async function goToStep(nextIndex) {
  index = nextIndex;
  const step = steps[index];
  if (selected.memory && step && step.turnIndex != null) {
    await executeMemoryTurn(step);
  }
  renderStep();
}

async function autoRun() {
  if (steps.length === 1) await prepareSelectedStory();
  stopAutoRun(false);
  $("auto-run").classList.add("hidden");
  $("pause-run").classList.remove("hidden");
  autoRunning = true;
  while (autoRunning && index < steps.length - 1) {
    await wait(3000);
    if (!autoRunning) break;
    await goToStep(index + 1);
  }
  stopAutoRun();
}

function stopAutoRun(showAuto = true) {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = null;
  autoRunning = false;
  if (showAuto) {
    $("auto-run").classList.remove("hidden");
    $("pause-run").classList.add("hidden");
  }
}

function renderResilience(res) {
  const breaker = res.breaker || {};
  const state = (breaker.state || "closed").replace("_", "-");
  const queue = res.human_queue_depth == null ? "unknown" : res.human_queue_depth;
  const fallback = res.served_by_fallback
    ? `Backup path used: ${res.fallback_rung || "fallback"}.`
    : "Primary path is available.";
  $("resilience-summary").innerHTML =
    `<div class="pill-row"><span class="pill ${state === "open" ? "warn" : "ok"}">${escapeHtml(state)}</span>` +
    `<span class="pill">human queue ${escapeHtml(String(queue))}</span></div>` +
    `<p>${escapeHtml(fallback)}</p>`;
}

function renderRouteSummary(route, trace, guard) {
  if (!route) {
    $("route-summary").textContent = "No route returned.";
    return;
  }
  const lane = LANES[route.tier] || LANES[2];
  const flags = guard && guard.output && guard.output.flags ? guard.output.flags.length : 0;
  $("route-summary").innerHTML =
    `<div class="pill-row"><span class="pill">${escapeHtml(lane.name)}</span>` +
    `<span class="pill">${escapeHtml(route.risk)} risk</span>` +
    `<span class="pill">${escapeHtml(route.complexity)} complexity</span></div>` +
    `<p><strong>${escapeHtml(lane.plain)}</strong>: ${escapeHtml(route.reason || "Selected by enterprise harness.")}</p>` +
    `<p class="muted">${escapeHtml(String(trace ? trace.tokens || 0 : 0))} model tokens tracked / ${flags} guard flags</p>`;
}

function renderTrace(trace) {
  const traceStepsRaw = trace && trace.steps ? trace.steps : [];
  $("trace").innerHTML = traceStepsRaw.length
    ? traceStepsRaw.map((s) => {
      const kind = traceKindLabel(s.kind);
      const ms = Number(s.ms || 0);
      return `<li class="trace-row trace-${escapeHtml(kind.toLowerCase())}">` +
        `<span>${escapeHtml(kind)}</span>` +
        `<strong>${escapeHtml(traceStepName(s))}</strong>` +
        `<small>${ms ? escapeHtml(ms.toFixed(1)) + "ms" : ""}</small>` +
        `</li>`;
    }).join("")
    : `<li class="trace-row trace-empty"><strong>No trace yet.</strong></li>`;
}

function traceKindLabel(kind) {
  const k = String(kind || "step").toLowerCase();
  if (k === "model") return "LLM";
  if (k === "memory") return "MEM";
  if (k === "guard") return "GATE";
  if (k === "breaker") return "CB";
  if (k === "fallback") return "FB";
  return k.toUpperCase();
}

function traceStepName(step) {
  const name = step.name || step.kind || "";
  if (step.kind === "tier" && name === "agent:final") return "agent: final";
  if (step.kind === "tier" && name.startsWith("agent:step")) return name.replace("agent:step", "agent: step");
  return name;
}

function renderMemory(memory) {
  if (!memory) return;
  const waiting = memory.raw_turn_count >= memory.max_raw && !memory.compressed_this_turn;
  if ($("mem-fill")) {
    $("mem-fill").style.width = `${Math.min(100, Math.round((memory.raw_turn_count / memory.max_raw) * 100))}%`;
    $("mem-count").textContent = waiting
      ? `${memory.raw_turn_count} / ${memory.max_raw} turns - waiting to compress`
      : `${memory.raw_turn_count} / ${memory.max_raw} turns`;
    if (memory.has_summary) $("mem-summary").textContent = memory.summary;
    else if (waiting) $("mem-summary").textContent = "Compression is waiting for the model path. Raw turns stay checkpointed.";
    else $("mem-summary").textContent = "Run the memory story to see recent turns and summary behavior.";
    $("mem-compress-flag").classList.toggle("hidden", !memory.compressed_this_turn);
    if (memory.compressed_this_turn) $("mem-compress-flag").textContent = `compressed ${memory.compressed_this_turn.folded} turns`;
    $("mem-turns").innerHTML = (memory.raw_turns || []).slice(-5).map((turn) =>
      `<li><strong>${escapeHtml(turn.user)}</strong><span>${escapeHtml((turn.assistant || "").slice(0, 100))}</span></li>`
    ).join("");
  }
  renderMemoryStrip(memory);
}

async function resumeMemory() {
  const id = getSession();
  if (!id) {
    renderMemory({ raw_turn_count: 0, max_raw: 10, raw_turns: [], has_summary: false });
    return;
  }
  try {
    const res = await fetch(`/api/memory/${id}`).then((r) => r.json());
    if (res.exists) renderMemory(res.memory);
  } catch {
    // Keep current state visible.
  }
}

function addMessage(text, who, tags = []) {
  const item = document.createElement("div");
  item.className = "msg " + who;
  if (tags.length) {
    const tagRow = document.createElement("div");
    tagRow.className = "msg-tags";
    tags.forEach((label) => {
      const tag = document.createElement("span");
      tag.textContent = label;
      tagRow.appendChild(tag);
    });
    item.appendChild(tagRow);
  }
  const body = document.createElement("p");
  body.textContent = text;
  item.appendChild(body);
  $("messages").appendChild(item);
}

function responseTags(res) {
  const tags = [];
  if (res.requires_confirmation) tags.push("approval required");
  if (res.resilience && res.resilience.served_by_fallback) tags.push(`backup: ${res.resilience.fallback_rung || "fallback"}`);
  return tags;
}

function traceSteps(trace) {
  return (trace && trace.steps ? trace.steps : []).map(plainStep).filter(Boolean);
}

function plainStep(step) {
  const name = step.name || "";
  if (step.kind === "memory" && name === "loaded") return "Opened the case context.";
  if (step.kind === "memory" && name === "turn recorded") return "Saved the latest customer turn.";
  if (step.kind === "memory" && name.includes("compressed")) return "Folded older turns into the running summary.";
  if (step.kind === "memory" && name.includes("deferred")) return "Deferred memory compression because the model path is down.";
  if (name === "classify_intent") return "Checked request complexity.";
  if (name === "extract_entities") return "Extracted billing details.";
  if (name === "draft_reply") return "Drafted a grounded customer response.";
  if (name === "record_lookup") return "Checked company records.";
  if (name === "knowledge_search") return "Looked up policy evidence.";
  if (name.includes("knowledge_search:fts")) return "Used backup keyword policy search.";
  if (name.includes("agent-local harness")) return "Opened the Tier 3 Agent Local-Harness.";
  if (name.startsWith("agent:step")) return "Agent Local-Harness chose the next check.";
  if (name.startsWith("agent:final")) return "Agent Local-Harness produced a final finding.";
  if (name.includes("write-gate")) return "Stopped at the approval checkpoint.";
  if (step.kind === "fallback" && name.includes("cache")) return "Used a recent approved cached answer.";
  if (step.kind === "fallback") return "Used a controlled backup path.";
  if (step.kind === "breaker") return "Checked model-service health.";
  return name || step.kind || "";
}

function memoryLine(memory) {
  if (!memory) return "Memory snapshot unavailable.";
  return `${memory.raw_turn_count || 0} / ${memory.max_raw || 10} recent turns in the raw buffer.`;
}

function recentTurns(memory) {
  const turns = memory && memory.raw_turns ? memory.raw_turns : [];
  return turns.slice(-3).map((turn) => `Customer: ${turn.user}`).join("\n") || "No recent turns returned.";
}

function memoryCompressionTitle(memory) {
  if (memory && memory.compressed_this_turn) return "Older turns were folded into a summary";
  if (memory && memory.raw_turn_count >= memory.max_raw && !memory.has_summary) return "Compression is waiting for the model path";
  return "Compression has not triggered yet";
}

function memoryCompressionBody(memory) {
  if (memory && memory.compressed_this_turn) return "The oldest batch became a compact running summary.";
  if (memory && memory.raw_turn_count >= memory.max_raw && !memory.has_summary) return "Raw turns stay safe until the model path can summarize them.";
  return "The conversation has not crossed the compression threshold in this run.";
}

function memorySummary(memory) {
  return memory && memory.has_summary ? memory.summary : "No running summary yet.";
}

function memoryStatusLine(memory) {
  if (!memory) return "Memory snapshot unavailable.";
  const raw = memory.raw_turn_count || 0;
  const max = memory.max_raw || 10;
  if (memory.compressed_this_turn) {
    return `Summarized ${memory.compressed_this_turn.folded} older turns; ${raw}/${max} raw turns remain for the next batch.`;
  }
  if (raw >= max && !memory.has_summary) {
    return `${raw}/${max} raw turns are checkpointed; summarization is waiting for the model path.`;
  }
  return `${raw}/${max} raw turns are kept in detail.`;
}

function renderMemoryStrip(memory) {
  if (!memory) return;
  const raw = memory.raw_turn_count || 0;
  const max = memory.max_raw || 10;
  const batch = memory.batch || 5;
  const compressed = !!memory.compressed_this_turn;
  const waiting = raw >= max && !compressed && !memory.has_summary;
  const fill = Math.min(100, Math.round((raw / max) * 100));
  const folded = compressed ? memory.compressed_this_turn.folded || batch : batch;
  const kept = compressed ? memory.compressed_this_turn.kept || raw : Math.min(raw, batch);

  const label = compressed
    ? `${kept} / ${max} turns after summary`
    : `${raw} / ${max} turns`;
  const state = compressed
    ? `summarized ${folded} older turns`
    : waiting
      ? "waiting to summarize"
      : "recent details";

  $("memory-rail").innerHTML = `
    <div class="memory-progress-label">
      <strong>${label}</strong>
      <span>${state}</span>
    </div>
    <div class="memory-progress-bar" aria-hidden="true">
      <i style="width: ${fill}%"></i>
    </div>
  `;

  $("memory-rail-note").textContent = compressed
    ? `Summary created; ${kept} newest turns stay detailed.`
    : waiting
      ? "The buffer is full; summarization is waiting for the model path."
      : "When the bar reaches 10, older turns are summarized.";
}

function updateMemoryStripForStep(step) {
  $("memory-rail")?.classList.toggle("spotlight", !!(step.nodes || []).includes("memory"));
}

function outcomeNote(res) {
  if (res.requires_confirmation) return "No risky change was committed automatically.";
  return "Optional evidence is available below.";
}

function fallbackText(res) {
  const rung = res.resilience && res.resilience.fallback_rung;
  if (rung === "cache") return "A recent approved answer was reused while the model path was unavailable.";
  if (rung === "rules") return "Rules handled the case while the model path was unavailable.";
  if (rung === "template") return "A controlled template answered while the model path was unavailable.";
  if (rung === "human") return "The case was queued for human review because automation could not answer safely.";
  return "A controlled fallback path handled the case.";
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"]/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
}

async function runCustomCase(event) {
  event.preventDefault();
  const input = $("custom-case-input");
  const button = $("custom-case-run");
  const message = (input.value || "").trim();
  if (!message) return;
  stopAutoRun();
  button.disabled = true;
  button.textContent = "Running...";
  await runCase(message);
  button.disabled = false;
  button.textContent = "Run";
}

$("prepare-story").addEventListener("click", prepareSelectedStory);
$("next-step").addEventListener("click", nextStep);
$("prev-step").addEventListener("click", prevStep);
$("auto-run").addEventListener("click", autoRun);
$("pause-run").addEventListener("click", () => stopAutoRun());
$("custom-case-form").addEventListener("submit", runCustomCase);

initStorySelect();
renderAll();
resumeMemory();
refreshHealth();
setInterval(refreshHealth, 5000);
