const STORAGE_KEY = "utnyilvantartas:selected-vehicle:v1";
const MONTHLY_SUFFIX = "_havi_bejarasi_jogosultsag";
if (!customElements.get("utnyilvantartas-card")) {
  await customElements.whenDefined("utnyilvantartas-card");
}

const Card = customElements.get("utnyilvantartas-card");

if (Card && !Card.prototype.__utnyMultiCarPatched) {
  const proto = Card.prototype;
  const originalRender = proto._render;

  proto.__utnyMultiCarPatched = true;

  proto._utnyMonthlyCandidates = function (hass = this._hass) {
    if (!hass?.states) return [];
    return Object.entries(hass.states)
      .filter(([id]) => id.startsWith("sensor.") && id.endsWith(MONTHLY_SUFFIX))
      .map(([entityId, state]) => ({
        entityId,
        state,
        entryId: state?.attributes?.config_entry_id || null,
        label: this._utnyVehicleLabel(entityId, state),
      }))
      .sort((a, b) => a.label.localeCompare(b.label, "hu-HU", { numeric: true, sensitivity: "base" }));
  };

  proto._utnyVehicleLabel = function (entityId, state) {
    const friendly = String(state?.attributes?.friendly_name || "").trim();
    if (friendly) {
      const cleaned = friendly
        .replace(/\s*[·|–—:-]?\s*havi\s+bej[aá]r[aá]si\s+jogosults[aá]g\s*$/iu, "")
        .trim();
      if (cleaned && cleaned.toLocaleLowerCase("hu-HU") !== friendly.toLocaleLowerCase("hu-HU")) {
        return cleaned;
      }
      if (cleaned && !/havi\s+bej[aá]r[aá]si\s+jogosults[aá]g/iu.test(cleaned)) {
        return cleaned;
      }
    }

    const objectId = String(entityId || "").split(".", 2)[1] || "";
    const base = objectId.endsWith(MONTHLY_SUFFIX)
      ? objectId.slice(0, -MONTHLY_SUFFIX.length)
      : objectId;
    return base.replace(/_/g, " ").replace(/\s+/g, " ").trim() || entityId || "Céges autó";
  };

  proto._utnyReadVehicleSelection = function () {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") return parsed;
      } catch (_) {
        return { entityId: raw, entryId: null };
      }
    } catch (_) {
      // localStorage may be unavailable in hardened/privacy browser modes.
    }
    return null;
  };

  proto._utnyWriteVehicleSelection = function (entityId, entryId = null) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ entityId, entryId }));
    } catch (_) {
      // Selection still works for the current page even without persistence.
    }
  };

  proto._utnyResolveSelectedMonthly = function (hass = this._hass) {
    if (!hass?.states) return null;

    // Preserve the existing dashboard-card contract: an explicit entity pins the card.
    const configured = this._config?.entity;
    if (configured && hass.states[configured]) return configured;

    const candidates = this._utnyMonthlyCandidates(hass);
    if (!candidates.length) return null;

    if (this._selectedMonthlyEntity && hass.states[this._selectedMonthlyEntity]) {
      return this._selectedMonthlyEntity;
    }

    const saved = this._utnyReadVehicleSelection();
    let selected = null;
    if (saved?.entryId) {
      selected = candidates.find((item) => item.entryId === saved.entryId) || null;
    }
    if (!selected && saved?.entityId) {
      selected = candidates.find((item) => item.entityId === saved.entityId) || null;
    }
    selected ||= candidates[0];

    this._selectedMonthlyEntity = selected.entityId;
    return selected.entityId;
  };

  proto._findMonthly = function () {
    return this._utnyResolveSelectedMonthly(this._hass);
  };

  proto._relevantSignature = function (hass) {
    if (!hass) return "";
    const monthly = this._utnyResolveSelectedMonthly(hass);
    const candidates = this._utnyMonthlyCandidates(hass);
    const candidateSignature = candidates
      .map((item) => `${item.entityId}:${item.entryId || ""}:${item.label}`)
      .join("|");

    if (!monthly) return `no-monthly|${candidateSignature}`;

    const objectId = monthly.split(".", 2)[1] || "";
    const prefix = objectId.endsWith(MONTHLY_SUFFIX)
      ? objectId.slice(0, -MONTHLY_SUFFIX.length)
      : objectId;

    const activeSignature = Object.entries(hass.states)
      .filter(([id]) => {
        const oid = id.split(".", 2)[1] || "";
        return oid === prefix || oid.startsWith(`${prefix}_`);
      })
      .map(([id, st]) => `${id}:${st?.last_updated || st?.last_changed || st?.state || ""}`)
      .sort()
      .join("|");

    return `${monthly}|vehicles:${candidateSignature}|active:${activeSignature}`;
  };

  proto._utnyEscapeHtml = function (value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  };

  proto._utnyInjectVehicleSelector = function () {
    const root = this.shadowRoot;
    if (!root || this._config?.entity) return;

    const candidates = this._utnyMonthlyCandidates();
    if (candidates.length <= 1) return;

    const active = this._utnyResolveSelectedMonthly();
    const hero = root.querySelector(".hero");
    const heroText = hero?.firstElementChild;
    if (!heroText) return;

    const switcher = document.createElement("div");
    switcher.className = "utny-vehicle-switcher";
    switcher.innerHTML = `
      <label for="utny-vehicle-select">Autó</label>
      <select id="utny-vehicle-select" aria-label="Megjelenített autó" ${this._busy ? "disabled" : ""}>
        ${candidates.map((item) => `
          <option value="${this._utnyEscapeHtml(item.entityId)}" ${item.entityId === active ? "selected" : ""}>
            ${this._utnyEscapeHtml(item.label)}
          </option>`).join("")}
      </select>
    `;

    const style = document.createElement("style");
    style.textContent = `
      .utny-vehicle-switcher {
        display:flex;
        align-items:center;
        gap:9px;
        margin-top:12px;
        width:min(100%, 390px);
      }
      .utny-vehicle-switcher label {
        font-size:11px;
        font-weight:800;
        letter-spacing:.04em;
        opacity:.9;
        white-space:nowrap;
      }
      .utny-vehicle-switcher select {
        min-width:0;
        flex:1 1 auto;
        height:36px;
        border:1px solid rgba(255,255,255,.38);
        border-radius:10px;
        padding:0 34px 0 11px;
        background:rgba(255,255,255,.96);
        color:#1f2937;
        font:inherit;
        font-size:12px;
        font-weight:750;
        outline:none;
        box-shadow:0 1px 2px rgba(0,0,0,.12);
        cursor:pointer;
      }
      .utny-vehicle-switcher select:focus {
        box-shadow:0 0 0 2px rgba(255,255,255,.32), 0 1px 2px rgba(0,0,0,.12);
      }
      .utny-vehicle-switcher select:disabled {
        opacity:.62;
        cursor:wait;
      }
      @media (max-width:760px) {
        .utny-vehicle-switcher { width:100%; }
      }
    `;

    heroText.appendChild(switcher);
    root.appendChild(style);

    const select = root.getElementById("utny-vehicle-select");
    if (!select) return;

    const lock = () => {
      this._interactionHover = true;
      if (this._interactionReleaseTimer) {
        window.clearTimeout(this._interactionReleaseTimer);
        this._interactionReleaseTimer = null;
      }
    };
    select.addEventListener("focus", lock);
    select.addEventListener("pointerdown", lock);
    select.addEventListener("blur", () => this._unlockInteractiveDelayed?.());

    select.addEventListener("change", () => {
      const next = select.value;
      const candidate = candidates.find((item) => item.entityId === next);
      if (!candidate || !this._hass?.states?.[next]) return;

      if (this._interactionReleaseTimer) {
        window.clearTimeout(this._interactionReleaseTimer);
        this._interactionReleaseTimer = null;
      }
      this._interactionHover = false;
      this._pendingRender = false;
      this._manualEditing = false;
      this._actionsHover = false;
      this._selectedMonthlyEntity = next;
      this._utnyWriteVehicleSelection(next, candidate.entryId);
      this._expandedDates?.clear?.();
      this._expandedDiagnostics?.clear?.();
      this._pdfPreviewUrl = null;
      this._message = null;
      this._lastRelevantSignature = null;
      this._render();
    });
  };

  proto._utnyAccountingRecords = function () {
    const entityId = this._utnyResolveSelectedMonthly();
    const state = entityId ? this._hass?.states?.[entityId] : null;
    const records = state?.attributes?.records;
    return Array.isArray(records) ? records : [];
  };

  proto._utnyCloseAccountingEditor = function () {
    this.shadowRoot?.querySelector(".utny-accounting-overlay")?.remove();
    this._interactionHover = false;
    this._unlockInteractiveDelayed?.();
  };

  proto._utnyOpenAccountingEditor = function (record) {
    const root = this.shadowRoot;
    if (!root || !record?.date || record.kelio_present !== true) return;

    root.querySelector(".utny-accounting-overlay")?.remove();
    this._interactionHover = true;
    if (this._interactionReleaseTimer) {
      window.clearTimeout(this._interactionReleaseTimer);
      this._interactionReleaseTimer = null;
    }

    const monthlyId = this._utnyResolveSelectedMonthly();
    const monthlyState = monthlyId ? this._hass?.states?.[monthlyId] : null;
    const entryId = monthlyState?.attributes?.config_entry_id || null;
    const overridden = record.accounting_override === true;
    const morning = record.morning_commute_eligible === true;
    const evening = record.evening_commute_eligible === true;
    const automaticMorning = overridden
      ? record.automatic_morning_commute_eligible
      : record.morning_commute_eligible;
    const automaticEvening = overridden
      ? record.automatic_evening_commute_eligible
      : record.evening_commute_eligible;
    const note = overridden
      ? String(record.accounting_override_note || "")
      : "Másik céges autóval történt az utazás";

    const overlay = document.createElement("div");
    overlay.className = "utny-accounting-overlay";
    overlay.innerHTML = `
      <div class="utny-accounting-dialog" role="dialog" aria-modal="true" aria-label="Napi elszámolás szerkesztése">
        <div class="utny-accounting-head">
          <div>
            <span>NAPI ELSZÁMOLÁS SZERKESZTÉSE</span>
            <b>${this._utnyEscapeHtml(record.date)}</b>
          </div>
          <button type="button" class="utny-accounting-close" title="Bezárás"><ha-icon icon="mdi:close"></ha-icon></button>
        </div>
        <div class="utny-accounting-info">
          A Kelio- és GPS-adatok változatlanok maradnak. Itt kizárólag azt állítod be,
          hogy az adott reggeli/esti bejárási út elszámolható-e.
        </div>
        <div class="utny-accounting-auto">
          <span>Automatikus eredmény</span>
          <b>Reggel: ${automaticMorning === true ? "elszámolható" : automaticMorning === false ? "nem elszámolható" : "nem eldönthető"}</b>
          <b>Este: ${automaticEvening === true ? "elszámolható" : automaticEvening === false ? "nem elszámolható" : "nem eldönthető"}</b>
        </div>
        <label class="utny-accounting-check">
          <input id="utny-account-morning" type="checkbox" ${morning ? "checked" : ""}>
          <span><b>Reggeli út elszámolható</b><small>Otthon → Munkahely</small></span>
        </label>
        <label class="utny-accounting-check">
          <input id="utny-account-evening" type="checkbox" ${evening ? "checked" : ""}>
          <span><b>Esti út elszámolható</b><small>Munkahely → Otthon</small></span>
        </label>
        <label class="utny-accounting-note">
          <span>Indok / megjegyzés</span>
          <textarea id="utny-account-note" rows="3">${this._utnyEscapeHtml(note)}</textarea>
        </label>
        <div class="utny-accounting-presets">
          <button type="button" id="utny-account-none"><ha-icon icon="mdi:cash-remove"></ha-icon> Egyik út sem elszámolható</button>
        </div>
        <div class="utny-accounting-actions">
          ${overridden ? `<button type="button" id="utny-account-reset" class="reset"><ha-icon icon="mdi:restore"></ha-icon> Automatikus visszaállítása</button>` : ""}
          <button type="button" id="utny-account-cancel" class="cancel">Mégse</button>
          <button type="button" id="utny-account-save" class="save"><ha-icon icon="mdi:content-save-check"></ha-icon> Mentés</button>
        </div>
      </div>
    `;
    root.appendChild(overlay);

    const close = () => this._utnyCloseAccountingEditor();
    overlay.querySelector(".utny-accounting-close")?.addEventListener("click", close);
    overlay.querySelector("#utny-account-cancel")?.addEventListener("click", close);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });

    overlay.querySelector("#utny-account-none")?.addEventListener("click", () => {
      const morningInput = overlay.querySelector("#utny-account-morning");
      const eveningInput = overlay.querySelector("#utny-account-evening");
      if (morningInput) morningInput.checked = false;
      if (eveningInput) eveningInput.checked = false;
    });

    const setBusy = (busy) => {
      overlay.querySelectorAll("button,input,textarea").forEach((el) => {
        el.disabled = !!busy;
      });
    };

    overlay.querySelector("#utny-account-save")?.addEventListener("click", async () => {
      if (!entryId) {
        this._message = { type: "error", text: "A config entry azonosító nem található." };
        close();
        this._render();
        return;
      }
      const morningInput = overlay.querySelector("#utny-account-morning");
      const eveningInput = overlay.querySelector("#utny-account-evening");
      const noteInput = overlay.querySelector("#utny-account-note");
      setBusy(true);
      this._busy = "accounting";
      try {
        await this._hass.callService("utnyilvantartas", "set_accounting_override", {
          date: record.date,
          morning_eligible: !!morningInput?.checked,
          evening_eligible: !!eveningInput?.checked,
          note: String(noteInput?.value || ""),
          entry_id: entryId,
        });
        this._message = { type: "success", text: `Elszámolás módosítva: ${record.date}.` };
      } catch (err) {
        this._message = { type: "error", text: `Elszámolás módosítási hiba: ${err?.message || err}` };
      } finally {
        this._busy = null;
        this._interactionHover = false;
        this._pendingRender = false;
        this._lastRelevantSignature = null;
        overlay.remove();
        await new Promise((resolve) => window.setTimeout(resolve, 100));
        this._render();
      }
    });

    overlay.querySelector("#utny-account-reset")?.addEventListener("click", async () => {
      if (!entryId) return;
      setBusy(true);
      this._busy = "accounting";
      try {
        await this._hass.callService("utnyilvantartas", "remove_accounting_override", {
          date: record.date,
          entry_id: entryId,
        });
        this._message = { type: "success", text: `Automatikus elszámolás visszaállítva: ${record.date}.` };
      } catch (err) {
        this._message = { type: "error", text: `Visszaállítási hiba: ${err?.message || err}` };
      } finally {
        this._busy = null;
        this._interactionHover = false;
        this._pendingRender = false;
        this._lastRelevantSignature = null;
        overlay.remove();
        await new Promise((resolve) => window.setTimeout(resolve, 100));
        this._render();
      }
    });
  };

  proto._utnyInjectAccountingEditors = function () {
    const root = this.shadowRoot;
    if (!root) return;
    const records = this._utnyAccountingRecords();
    if (!records.length) return;

    const rows = Array.from(root.querySelectorAll("tr.main-row"));
    rows.forEach((row, index) => {
      const record = records[index];
      if (!record?.date || record.kelio_present !== true) return;
      const dateCell = row.querySelector("td.date");
      if (!dateCell) return;

      const wrap = document.createElement("span");
      wrap.className = "utny-accounting-row-actions";
      if (record.accounting_override === true) {
        const badge = document.createElement("span");
        badge.className = "utny-accounting-badge";
        badge.textContent = "KÉZI ELSZ.";
        badge.title = record.accounting_override_note || "Kézi elszámolási felülbírálás";
        wrap.appendChild(badge);
      }

      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "utny-accounting-edit";
      edit.title = "Napi elszámolás szerkesztése";
      edit.innerHTML = '<ha-icon icon="mdi:pencil-outline"></ha-icon>';
      edit.addEventListener("pointerdown", () => {
        this._interactionHover = true;
      });
      edit.addEventListener("click", (event) => {
        event.stopPropagation();
        this._utnyOpenAccountingEditor(record);
      });
      wrap.appendChild(edit);
      dateCell.appendChild(wrap);
    });

    const style = document.createElement("style");
    style.textContent = `
      td.date { vertical-align:middle; }
      .utny-accounting-row-actions { display:inline-flex; align-items:center; gap:5px; margin-left:7px; vertical-align:middle; }
      .utny-accounting-edit {
        appearance:none; width:27px; height:27px; padding:0; border-radius:7px;
        border:1px solid color-mix(in srgb,var(--primary-color) 28%,var(--divider-color));
        background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color));
        color:var(--primary-color); cursor:pointer; display:inline-flex; align-items:center; justify-content:center;
      }
      .utny-accounting-edit ha-icon { --mdc-icon-size:15px; pointer-events:none; }
      .utny-accounting-badge {
        display:inline-flex; align-items:center; padding:2px 6px; border-radius:999px;
        background:color-mix(in srgb,#8e24aa 14%,var(--card-background-color)); color:#8e24aa;
        border:1px solid color-mix(in srgb,#8e24aa 35%,var(--divider-color));
        font-size:8px; font-weight:850; white-space:nowrap;
      }
      .utny-accounting-overlay {
        position:fixed; inset:0; z-index:10000; display:flex; align-items:center; justify-content:center;
        padding:18px; background:rgba(0,0,0,.48); backdrop-filter:blur(2px);
      }
      .utny-accounting-dialog {
        width:min(560px,100%); max-height:calc(100vh - 36px); overflow:auto;
        background:var(--card-background-color); color:var(--primary-text-color);
        border:1px solid var(--divider-color); border-radius:18px; padding:18px;
        box-shadow:0 18px 55px rgba(0,0,0,.32);
      }
      .utny-accounting-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }
      .utny-accounting-head>div { display:flex; flex-direction:column; gap:3px; }
      .utny-accounting-head span { font-size:9px; letter-spacing:.08em; font-weight:850; color:var(--secondary-text-color); }
      .utny-accounting-head b { font-size:21px; }
      .utny-accounting-close { appearance:none; border:0; background:var(--secondary-background-color); color:var(--primary-text-color); width:36px; height:36px; border-radius:10px; cursor:pointer; }
      .utny-accounting-info { padding:10px 11px; border-radius:10px; margin-bottom:11px; background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color)); color:var(--secondary-text-color); font-size:11px; line-height:1.45; }
      .utny-accounting-auto { display:flex; gap:8px; flex-wrap:wrap; align-items:center; padding:9px 10px; margin-bottom:11px; border:1px solid var(--divider-color); border-radius:10px; font-size:10px; }
      .utny-accounting-auto span { width:100%; color:var(--secondary-text-color); font-weight:750; }
      .utny-accounting-auto b { padding:4px 7px; border-radius:7px; background:var(--secondary-background-color); }
      .utny-accounting-check { display:flex; align-items:center; gap:11px; padding:11px; margin-top:8px; border:1px solid var(--divider-color); border-radius:11px; background:var(--secondary-background-color); cursor:pointer; }
      .utny-accounting-check input { width:20px; height:20px; flex:0 0 auto; }
      .utny-accounting-check span { display:flex; flex-direction:column; gap:2px; }
      .utny-accounting-check b { font-size:12px; }
      .utny-accounting-check small { color:var(--secondary-text-color); font-size:10px; }
      .utny-accounting-note { display:flex; flex-direction:column; gap:5px; margin-top:12px; font-size:10px; color:var(--secondary-text-color); }
      .utny-accounting-note textarea { width:100%; min-height:70px; resize:vertical; padding:9px 10px; border:1px solid var(--divider-color); border-radius:10px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; font-size:11px; }
      .utny-accounting-presets { display:flex; flex-wrap:wrap; gap:7px; margin-top:10px; }
      .utny-accounting-presets button { appearance:none; min-height:34px; border-radius:9px; border:1px solid color-mix(in srgb,var(--bad) 30%,var(--divider-color)); background:color-mix(in srgb,var(--bad) 8%,var(--card-background-color)); color:var(--bad); font:inherit; font-size:10px; font-weight:750; cursor:pointer; display:inline-flex; align-items:center; gap:5px; padding:0 10px; }
      .utny-accounting-presets ha-icon { --mdc-icon-size:15px; }
      .utny-accounting-actions { display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap; margin-top:16px; padding-top:13px; border-top:1px solid var(--divider-color); }
      .utny-accounting-actions button { appearance:none; min-height:40px; border-radius:10px; padding:0 13px; font:inherit; font-size:10.5px; font-weight:800; cursor:pointer; display:inline-flex; align-items:center; gap:6px; }
      .utny-accounting-actions .save { border:0; background:var(--primary-color); color:white; }
      .utny-accounting-actions .cancel { border:1px solid var(--divider-color); background:var(--secondary-background-color); color:var(--primary-text-color); }
      .utny-accounting-actions .reset { margin-right:auto; border:1px solid color-mix(in srgb,#8e24aa 35%,var(--divider-color)); background:color-mix(in srgb,#8e24aa 9%,var(--card-background-color)); color:#8e24aa; }
      .utny-accounting-actions button:disabled,.utny-accounting-presets button:disabled { opacity:.55; cursor:wait; }
      @media (max-width:600px) {
        .utny-accounting-overlay { padding:9px; align-items:flex-end; }
        .utny-accounting-dialog { width:100%; max-height:92vh; border-radius:17px 17px 10px 10px; }
        .utny-accounting-actions { flex-direction:column; }
        .utny-accounting-actions button,.utny-accounting-actions .reset { width:100%; justify-content:center; margin-right:0; }
        .utny-accounting-row-actions { margin-left:4px; }
        .utny-accounting-badge { display:none; }
      }
    `;
    root.appendChild(style);
  };

  proto._render = function (...args) {
    const result = originalRender.apply(this, args);
    this._utnyInjectVehicleSelector();
    this._utnyInjectAccountingEditors();
    return result;
  };
}

console.info(
  "%c Útnyilvántartás Panel v0.4.47 multi-car + napi elszámolás betöltve ",
  "color:#fff;background:#1976d2;font-weight:700;padding:3px 6px;border-radius:4px"
);
