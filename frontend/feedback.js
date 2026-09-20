/* Feedback loop for the dashboard. Loaded after app.js; it adds a panel under the work order
   and sends the technician's confirmation or correction to POST /api/feedback. */
(function () {
  // Wrap app.js's renderResult so every diagnosis (new or reopened) gets a feedback panel.
  const baseRenderResult = renderResult;
  renderResult = function (r) {
    baseRenderResult(r);
    mountFeedback(r);
  };

  const numberOrNull = (v) => (v === "" || v == null ? null : Number(v));

  function mountFeedback(r) {
    const host = document.querySelector(".result-grid");
    if (!host) return;
    const box = document.createElement("section");
    box.className = "feedback result-feedback";
    const cases = host.querySelector(".area-cases");
    if (cases) host.insertBefore(box, cases);
    else host.appendChild(box);
    draw(box, r);
  }

  function savedView(fb) {
    return `<h2>Feedback recorded</h2>
      <p class="fb-done"><b>${esc(fb.record_id)}</b> is now in the knowledge base as
        &ldquo;${esc(fb.cause)}&rdquo;.</p>
      <p class="fine">Similar complaints will find this case from now on.</p>`;
  }

  function draw(box, r) {
    if (r.feedback) {
      box.innerHTML = savedView(r.feedback);
      return;
    }
    const weak = r.diagnosis.insufficient_evidence;
    const needsEquipment = !r.equipment_type;

    box.innerHTML = `
      <h2>${weak ? "What was the real cause?" : "Was this diagnosis right?"}</h2>
      <p class="fine">${weak
        ? "The agents could not name a cause. Once a technician finds it, add it here so the next similar complaint is diagnosed."
        : "Confirm or correct it. The case is added to the knowledge base and used for the next similar complaint."}</p>
      <div class="fb-actions" ${weak ? "hidden" : ""}>
        <button type="button" class="fb-btn ok" data-act="correct">Correct</button>
        <button type="button" class="fb-btn" data-act="wrong">Incorrect</button>
      </div>
      <form class="fb-form" ${weak ? "" : "hidden"}>
        ${needsEquipment ? `<label>Equipment<select name="equipment"><option value="">Choose equipment</option></select></label>` : ""}
        <label>Actual cause<select name="cause"></select></label>
        <div class="fb-custom" hidden>
          <label>Cause found<input type="text" name="custom" maxlength="120" placeholder="For example: Worn door belt"></label>
          <label>What fixed it (one step per line, optional)<textarea name="steps" rows="3"></textarea></label>
        </div>
        <div class="fb-pair">
          <label>Actual cost in &#8377; (optional)<input type="number" name="cost" min="0" step="50"></label>
          <label>Time taken in hours (optional)<input type="number" name="hours" min="0" step="0.5"></label>
        </div>
        <button type="submit" class="primary">Save to knowledge base</button>
      </form>
      <p class="fb-msg" role="status"></p>`;

    const form = box.querySelector(".fb-form");

    if (needsEquipment) {
      fetch("/api/stats").then((res) => res.json()).then((s) => {
        s.equipment_types.forEach((t) => form.elements.equipment.add(new Option(t, t)));
      }).catch(() => {});
      form.elements.equipment.addEventListener("change", () => loadCauses(form, r));
    }
    form.elements.cause.addEventListener("change", () => toggleCustom(form));
    loadCauses(form, r);

    box.addEventListener("click", (e) => {
      const button = e.target.closest("[data-act]");
      if (!button) return;
      if (button.dataset.act === "correct") {
        send(box, r, { verdict: "correct" });
      } else {
        box.querySelector(".fb-actions").hidden = true;
        form.hidden = false;
      }
    });

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = form.elements;
      const custom = f.cause.value === "__other__";
      send(box, r, {
        verdict: "incorrect",
        cause: custom ? f.custom.value.trim() : f.cause.value,
        equipment_type: f.equipment ? f.equipment.value : null,
        cost_inr: numberOrNull(f.cost.value),
        downtime_hours: numberOrNull(f.hours.value),
        fix_steps: custom ? f.steps.value : null,
      });
    });
  }

  function toggleCustom(form) {
    form.querySelector(".fb-custom").hidden = form.elements.cause.value !== "__other__";
  }

  async function loadCauses(form, r) {
    const select = form.elements.cause;
    const equipment = r.equipment_type || (form.elements.equipment ? form.elements.equipment.value : "");
    let names = [];
    if (equipment) {
      try {
        const res = await fetch("/api/causes?equipment=" + encodeURIComponent(equipment));
        names = await res.json();
      } catch (e) { /* the list stays empty; "Something else" still works */ }
    }
    const proposed = r.diagnosis.insufficient_evidence || !r.diagnosis.ranked.length
      ? null : r.diagnosis.ranked[0].cause;
    select.innerHTML = "";
    names.filter((n) => n !== proposed).forEach((n) => select.add(new Option(n, n)));
    select.add(new Option("Something else (type it below)", "__other__"));
    toggleCustom(form);
  }

  async function send(box, r, payload) {
    const message = box.querySelector(".fb-msg");
    const buttons = box.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    message.className = "fb-msg";
    message.textContent = "Saving...";
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: r.id, ...payload }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof body.detail === "string" ? body.detail : "Could not save (status " + response.status + ").");
      }
      r.feedback = body;
      draw(box, r);
      window.dispatchEvent(new CustomEvent("cognilogix:feedback", { detail: body }));
      loadStats();          // refreshes "Past repairs on file" in the top bar
    } catch (error) {
      message.className = "fb-msg error";
      message.textContent = error.message;
      buttons.forEach((b) => (b.disabled = false));
    }
  }
})();