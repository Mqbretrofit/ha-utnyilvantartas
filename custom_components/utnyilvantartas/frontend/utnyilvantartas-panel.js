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

  proto._render = function (...args) {
    const result = originalRender.apply(this, args);
    this._utnyInjectVehicleSelector();
    return result;
  };
}

console.info(
  "%c Útnyilvántartás Panel v0.4.46 multi-car betöltve ",
  "color:#fff;background:#1976d2;font-weight:700;padding:3px 6px;border-radius:4px"
);
