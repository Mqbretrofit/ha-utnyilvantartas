console.info("%c Útnyilvántartás Panel v0.4.40 betöltve ", "color:#fff;background:#1976d2;font-weight:700;padding:3px 6px;border-radius:4px");

class UtnyilvantartasCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._busy = null;
    this._message = null;
    this._expandedDates = new Set();
    this._expandedDiagnostics = new Set();
    this._manualForm = {
      date: "",
      morning: true,
      evening: true,
      note: "Hónap végi kézi kiegészítés – a Kelio aznapi bejegyzése még nem érhető el",
    };
    this._manualEditing = false;
    this._actionsHover = false;
    this._interactionHover = false;
    this._interactionReleaseTimer = null;
    this._pendingRender = false;
    this._lastRelevantSignature = null;
    this._pdfPreviewUrl = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set panel(panel) {
    this._panel = panel;
    const cfg = panel?.config || {};
    this._config = { ...this._config, ...cfg };
    this._render();
  }

  set narrow(narrow) {
    this._narrow = narrow;
  }

  _relevantSignature(hass) {
    if (!hass) return "";
    const monthly = (() => {
      const configured = this._config.entity;
      if (configured && hass.states[configured]) return configured;
      return Object.keys(hass.states).find(
        (id) => id.startsWith("sensor.") && id.endsWith("_havi_bejarasi_jogosultsag")
      ) || null;
    })();
    if (!monthly) return "no-monthly";

    const objectId = monthly.split(".", 2)[1] || "";
    const suffix = "_havi_bejarasi_jogosultsag";
    const prefix = objectId.endsWith(suffix) ? objectId.slice(0, -suffix.length) : objectId;

    return Object.entries(hass.states)
      .filter(([id]) => {
        const oid = id.split(".", 2)[1] || "";
        return oid === prefix || oid.startsWith(`${prefix}_`);
      })
      .map(([id, st]) => `${id}:${st?.last_updated || st?.last_changed || st?.state || ""}`)
      .sort()
      .join("|");
  }

  _lockInteractive() {
    this._interactionHover = true;
    if (this._interactionReleaseTimer) {
      window.clearTimeout(this._interactionReleaseTimer);
      this._interactionReleaseTimer = null;
    }
  }

  _unlockInteractiveDelayed() {
    if (this._interactionReleaseTimer) {
      window.clearTimeout(this._interactionReleaseTimer);
    }
    this._interactionReleaseTimer = window.setTimeout(() => {
      this._interactionReleaseTimer = null;
      this._interactionHover = false;
      if (this._pendingRender && !this._manualEditing && !this._actionsHover) {
        this._pendingRender = false;
        this._render();
      }
    }, 180);
  }

  set hass(hass) {
    this._hass = hass;

    const signature = this._relevantSignature(hass);
    if (signature === this._lastRelevantSignature) {
      return;
    }
    this._lastRelevantSignature = signature;

    if (this._manualEditing || this._actionsHover || this._interactionHover) {
      this._pendingRender = true;
      return;
    }

    this._render();
  }

  getCardSize() {
    return 12;
  }

  _findMonthly() {
    if (!this._hass) return null;
    const configured = this._config.entity;
    if (configured && this._hass.states[configured]) return configured;
    return Object.keys(this._hass.states).find(
      (id) => id.startsWith("sensor.") && id.endsWith("_havi_bejarasi_jogosultsag")
    ) || null;
  }

  _prefix(monthly) {
    if (!monthly) return null;
    const objectId = monthly.split(".", 2)[1] || "";
    const suffix = "_havi_bejarasi_jogosultsag";
    return objectId.endsWith(suffix) ? objectId.slice(0, -suffix.length) : null;
  }

  _id(domain, prefix, suffix) {
    return prefix ? `${domain}.${prefix}_${suffix}` : null;
  }

  _findButton(prefix, suffixes = [], friendlyNeedle = "") {
    if (!this._hass || !prefix) return null;

    // First try known deterministic entity-id variants.
    for (const suffix of suffixes) {
      const id = this._id("button", prefix, suffix);
      if (id && this._hass.states[id]) return id;
    }

    // Fallback: scan this integration/device prefix. This also survives
    // Home Assistant entity-id slug changes such as "e-mail" -> "e_mail".
    const base = `button.${prefix}_`;
    const needle = String(friendlyNeedle || "").toLocaleLowerCase("hu-HU");
    for (const [id, state] of Object.entries(this._hass.states)) {
      if (!id.startsWith(base)) continue;
      const friendly = String(state?.attributes?.friendly_name || "").toLocaleLowerCase("hu-HU");
      if (needle && friendly.includes(needle)) return id;
    }
    return null;
  }

  _state(id) {
    return id && this._hass ? this._hass.states[id] : null;
  }

  _attr(state, key, fallback = null) {
    const value = state?.attributes?.[key];
    return value === undefined || value === null ? fallback : value;
  }

  _num(value, digits = 0) {
    if (value === null || value === undefined || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return new Intl.NumberFormat("hu-HU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  }

  _dateTime(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return new Intl.DateTimeFormat("hu-HU", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).format(d);
  }

  _time(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) {
      const s = String(value);
      return s.length >= 16 ? s.slice(11, 16) : s;
    }
    return new Intl.DateTimeFormat("hu-HU", { hour: "2-digit", minute: "2-digit" }).format(d);
  }

  _day(value) {
    if (!value) return "-";
    const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[2]}.${m[3]}.` : String(value);
  }

  _currentMonth() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  _shiftMonth(month, delta) {
    const match = String(month || "").match(/^(\d{4})-(\d{2})$/);
    if (!match) return this._currentMonth();
    const d = new Date(Number(match[1]), Number(match[2]) - 1 + delta, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  _monthLabel(month) {
    const match = String(month || "").match(/^(\d{4})-(\d{2})$/);
    if (!match) return String(month || "-");
    const d = new Date(Number(match[1]), Number(match[2]) - 1, 1);
    return new Intl.DateTimeFormat("hu-HU", {
      year: "numeric",
      month: "long",
    }).format(d);
  }

  async _selectMonth(month, entryId) {
    if (!this._hass) return;
    this._interactionHover = false;
    this._pendingRender = false;
    const current = this._currentMonth();
    if (month > current) return;

    this._busy = "month";
    this._message = null;
    this._render();
    try {
      const data = { month };
      if (entryId) data.entry_id = entryId;
      await this._hass.callService("utnyilvantartas", "set_month", data);
      this._message = {
        type: "success",
        text: `${this._monthLabel(month)} Kelio + GPS adatai betöltve.`,
      };
    } catch (err) {
      this._message = {
        type: "error",
        text: `A hónap betöltése sikertelen: ${err?.message || err}`,
      };
    } finally {
      this._busy = null;
      this._render();
    }
  }

  _nightEvidence(value) {
    const labels = {
      direct_point: "Közvetlen GPS-pont az éjszakai ablakban",
      exact_playback_snapshot_home: "Pontos 18:00–06:00 lejátszási állapotpont: OTTHON",
      exact_direct_home_point: "Pontos 18:00–06:00 GPS-pont: OTTHON",
      exact_playback_snapshots_outside: "18:00 és 00:00 lejátszási állapotpont: NEM OTTHON",
      exact_night_observed_outside: "A teljes pontos éjszakai ablak: NEM OTTHON",
      no_exact_night_samples: "NINCS PONT A PONTOS ÉJSZAKAI LEKÉRÉSBEN",
      incomplete_exact_night_playback: "NINCS ELÉG PONTOS ÉJSZAKAI LEJÁTSZÁSI ADAT",
      playback_snapshot_home: "Alapnyomkövetés lejátszási állapotpont: OTTHON",
      playback_snapshot_outside: "Alapnyomkövetés lejátszási állapotpont: NEM OTTHON",
      observed_outside_full_window: "GPS-idővonal teljesen KÍVÜL",
      no_playback_samples: "NINCS LEJÁTSZÁSI GPS-ÁLLAPOTPONT",
      insufficient_playback_coverage: "NINCS ELÉG LEJÁTSZÁSI ADAT",
      home_before_night: "Utolsó pont 18:00 előtt: OTTHON",
      home_after_night: "Első pont 06:00 után: OTTHON",
      outside_on_both_boundaries: "18:00 előtti és 06:00 utáni pont is KÍVÜL",
      insufficient_boundary_data: "NINCS ELÉG HATÁRPONT-ADAT",
      last_known_home_carried_forward: "Utolsó ismert otthoni helyzet továbbvezetve",
      observed_outside: "GPS-pontok alapján kívül",
      insufficient_data: "NINCS ELÉG GPS-ADAT",
      zone_unavailable: "Home zóna nem elérhető",
    };
    return labels[value] || value || "-";
  }

  _boolLabel(value) {
    if (value === true) return '<span class="detail-yes">IGEN</span>';
    if (value === false) return '<span class="detail-no">NEM</span>';
    return '<span class="detail-na">-</span>';
  }

  _distanceTime(distance, when, radius) {
    if (distance === null || distance === undefined || distance === "") return "-";
    const d = Number(distance);
    if (!Number.isFinite(d)) return "-";
    const inside = Number.isFinite(Number(radius)) && d <= Number(radius);
    const time = when ? this._timeSeconds(when) : "-";
    return `<b class="${inside ? "inside" : "outside"}">${this._num(d, 1)} m</b><span class="detail-time">${time}</span>`;
  }

  _timeSeconds(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) {
      const s = String(value);
      return s.length >= 19 ? s.slice(11, 19) : s;
    }
    return new Intl.DateTimeFormat("hu-HU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(d);
  }

  _duration(start, end) {
    if (!start || !end) return "-";
    const a = new Date(start);
    const b = new Date(end);
    if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime()) || b < a) return "-";
    let sec = Math.round((b.getTime() - a.getTime()) / 1000);
    const h = Math.floor(sec / 3600);
    sec -= h * 3600;
    const m = Math.floor(sec / 60);
    return `${h} ó ${String(m).padStart(2, "0")} p`;
  }

  _toggleDate(date) {
    if (this._expandedDates.has(date)) this._expandedDates.delete(date);
    else this._expandedDates.add(date);
    this._interactionHover = false;
    this._pendingRender = false;
    this._render();
  }

  _toggleDiagnostics(date) {
    if (this._expandedDiagnostics.has(date)) this._expandedDiagnostics.delete(date);
    else this._expandedDiagnostics.add(date);
    this._interactionHover = false;
    this._pendingRender = false;
    this._render();
  }

  _onOff(state) {
    if (!state) return { text: "Nincs adat", cls: "muted", icon: "mdi:help-circle-outline" };
    const on = state.state === "on" || state.state === "true";
    return on
      ? { text: "Igen", cls: "success", icon: "mdi:check-circle" }
      : { text: "Nem", cls: "muted", icon: "mdi:minus-circle-outline" };
  }

  _beginManualEdit() {
    this._manualEditing = true;
  }

  _syncManualFormFromDom() {
    const root = this.shadowRoot;
    if (!root) return;
    const dateEl = root.getElementById("manual-date");
    const morningEl = root.getElementById("manual-morning");
    const eveningEl = root.getElementById("manual-evening");
    const noteEl = root.getElementById("manual-note");

    if (dateEl) this._manualForm.date = dateEl.value || this._manualForm.date;
    if (morningEl) this._manualForm.morning = !!morningEl.checked;
    if (eveningEl) this._manualForm.evening = !!eveningEl.checked;
    if (noteEl) this._manualForm.note = noteEl.value;
  }

  _finishManualEdit({ renderPending = true } = {}) {
    this._syncManualFormFromDom();
    this._manualEditing = false;
    if (renderPending && this._pendingRender) {
      this._pendingRender = false;
      this._render();
    }
  }

  _todayIso() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  async _saveManual(entryId) {
    this._syncManualFormFromDom();
    if (!entryId) {
      this._message = { type: "error", text: "A config entry azonosító nem található." };
      this._render();
      return;
    }
    const date = String(this._manualForm.date || "").trim();
    if (!date) {
      this._message = { type: "error", text: "Adj meg dátumot a kézi kiegészítéshez." };
      this._render();
      return;
    }
    try {
      this._busy = "manual";
      await this._hass.callService("utnyilvantartas", "set_manual_day", {
        date,
        morning_eligible: !!this._manualForm.morning,
        evening_eligible: !!this._manualForm.evening,
        note: String(this._manualForm.note || ""),
        entry_id: entryId,
      });
      this._message = { type: "success", text: `Kézi kiegészítés mentve: ${date}.` };
    } catch (err) {
      this._message = { type: "error", text: `Kézi kiegészítés hiba: ${err?.message || err}` };
    } finally {
      this._busy = null;
      this._manualEditing = false;
      this._pendingRender = false;
      await new Promise(resolve => window.setTimeout(resolve, 120));
      this._render();
    }
  }

  async _removeManual(entryId) {
    this._syncManualFormFromDom();
    if (!entryId) return;
    const date = String(this._manualForm.date || "").trim();
    if (!date) {
      this._message = { type: "error", text: "Adj meg dátumot a törléshez." };
      this._render();
      return;
    }
    try {
      this._busy = "manual";
      await this._hass.callService("utnyilvantartas", "remove_manual_day", {
        date,
        entry_id: entryId,
      });
      this._message = { type: "success", text: `Kézi kiegészítés törölve: ${date}.` };
    } catch (err) {
      this._message = { type: "error", text: `Kézi kiegészítés törlési hiba: ${err?.message || err}` };
    } finally {
      this._busy = null;
      this._manualEditing = false;
      this._pendingRender = false;
      await new Promise(resolve => window.setTimeout(resolve, 120));
      this._render();
    }
  }

  async _press(entityId, busyKey, successText) {
    if (!entityId || !this._state(entityId) || !this._hass) {
      this._message = { type: "error", text: "A szükséges Home Assistant gomb entitás nem található." };
      this._render();
      return;
    }
    this._busy = busyKey;
    this._message = null;
    this._actionsHover = false;
    this._interactionHover = false;
    this._render();
    try {
      await this._hass.callService("button", "press", { entity_id: entityId });
      this._message = { type: "success", text: successText };
    } catch (err) {
      this._message = { type: "error", text: `Hiba: ${err?.message || err}` };
    } finally {
      this._busy = null;
      this._actionsHover = false;
      this._interactionHover = false;
      this._pendingRender = false;
      this._render();
    }
  }

  _previewPdf(url) {
    if (!url) return;
    this._pdfPreviewUrl = url;
    this._interactionHover = false;
    this._pendingRender = false;
    this._render();
  }

  _closePdfPreview() {
    this._pdfPreviewUrl = null;
    this._interactionHover = false;
    this._pendingRender = false;
    this._render();
  }

  _printPdf(url) {
    if (!url) return;
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (!win) {
      this._message = { type: "error", text: "A böngésző letiltotta a PDF megnyitását." };
      this._render();
      return;
    }
    // Best effort: Chrome usually opens the native PDF viewer; if scripted
    // print is blocked, the PDF still stays open with its own Print button.
    window.setTimeout(() => {
      try { win.focus(); win.print(); } catch (_) {}
    }, 1200);
  }

  _openPdf(pdfState, month) {
    const pdfMonth = pdfState?.state || null;
    let url = pdfMonth === month ? this._attr(pdfState, "url", null) : null;
    if (!url && month) {
      // Predictable fallback URL: useful after a HA/browser reload when the PDF
      // still exists under /config/www but the transient sensor URL is empty.
      url = `/local/utnyilvantartas/utnyilvantartas_${month}.pdf`;
    }
    if (!url) {
      this._message = { type: "error", text: "Még nincs megnyitható PDF. Előbb generáld le." };
      this._render();
      return;
    }
    try {
      const absolute = new URL(url, window.location.origin).href;
      const opened = window.open(absolute, "_blank");
      if (opened) {
        try { opened.opener = null; } catch (_) {}
      } else {
        window.location.href = absolute;
      }
    } catch (err) {
      this._message = { type: "error", text: `A PDF nem nyitható meg: ${err?.message || err}` };
      this._render();
    }
  }

  _routeStart(car) {
    if (!car) return "-";
    if (car.started_home) return "Otthon";
    if (car.started_work) return "Munkahely";
    return "Egyéb";
  }

  _routeEnd(car) {
    if (!car) return "-";
    if (car.ended_home) return "Otthon";
    if (car.ended_work) return "Munkahely";
    return "Egyéb";
  }

  _render() {
    if (!this.shadowRoot) return;
    if (!this._hass) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:24px">Útnyilvántartás betöltése…</div></ha-card>`;
      return;
    }

    const monthlyId = this._findMonthly();
    const prefix = this._prefix(monthlyId);
    const monthly = this._state(monthlyId);
    if (!monthly || !prefix) {
      this.shadowRoot.innerHTML = `
        <ha-card><div class="fatal">Nem található a <b>Havi bejárási jogosultság</b> szenzor.<br>
        A kártyán opcionálisan megadható: <code>entity: sensor.…_havi_bejarasi_jogosultsag</code></div></ha-card>
        <style>.fatal{padding:24px;color:var(--error-color,#db4437)}</style>`;
      return;
    }

    const ids = {
      refresh: this._findButton(prefix, ["havi_adatok_ujraszamitasa"], "havi adatok"),
      pdfGenerate: this._findButton(prefix, ["havi_pdf_generalasa"], "pdf generálása"),
      emailSend: this._findButton(
        prefix,
        [
          "havi_pdf_e_mail_kuldese",
          "havi_pdf_email_kuldese",
          "havi_pdf_e_mail_kuldes",
          "havi_pdf_email_kuldes"
        ],
        "e-mail"
      ),
      pdf: this._id("sensor", prefix, "havi_pdf"),
      dailyDistance: this._id("sensor", prefix, "ceges_auto_napi_tavolsag"),
      maxSpeed: this._id("sensor", prefix, "ceges_auto_napi_max_sebesseg"),
      firstMove: this._id("sensor", prefix, "ceges_auto_elso_mozgas"),
      lastMove: this._id("sensor", prefix, "ceges_auto_utolso_mozgas"),
      gpsPoints: this._id("sensor", prefix, "ceges_auto_gps_pontok"),
      kelioToday: this._id("binary_sensor", prefix, "kelio_jelenlet_ma"),
      eligibleToday: this._id("binary_sensor", prefix, "bejarasi_hozzajarulas_jar"),
      movedToday: this._id("binary_sensor", prefix, "ceges_auto_mozgott_ma"),
      startedHome: this._id("binary_sensor", prefix, "ceges_auto_otthonrol_indult"),
      endedHome: this._id("binary_sensor", prefix, "ceges_auto_otthon_erkezett"),
      startedWork: this._id("binary_sensor", prefix, "ceges_auto_munkahelyrol_indult"),
      endedWork: this._id("binary_sensor", prefix, "ceges_auto_munkahelyre_erkezett"),
    };

    const pdfState = this._state(ids.pdf);
    const month = this._attr(monthly, "month", monthly.state || "-");
    const entryId = this._attr(monthly, "config_entry_id", null);
    const currentMonth = this._currentMonth();
    const previousMonth = this._shiftMonth(month, -1);
    const nextMonth = this._shiftMonth(month, 1);
    const canGoNext = nextMonth <= currentMonth;
    const monthIsCurrent = month === currentMonth;
    const presence = Number(this._attr(monthly, "presence_days", 0));
    const eligible = Number(this._attr(monthly, "eligible_days", Number(monthly.state) || 0));
    const ineligible = Number(this._attr(monthly, "ineligible_presence_days", 0));
    const eligibleLegs = Number(this._attr(monthly, "eligible_legs", Math.round(eligible * 2)));
    const ineligibleLegs = Number(this._attr(monthly, "ineligible_legs", Math.round(ineligible * 2)));
    const unknownLegs = Number(this._attr(monthly, "unknown_legs", 0));
    const companyKm = Number(this._attr(monthly, "company_distance_km", 0));
    const privateKm = Number(this._attr(monthly, "private_commute_km", 0));
    const reimbursement = Number(this._attr(monthly, "reimbursement_huf", 0));
    const odoStart = Number(this._attr(monthly, "odometer_start_km", 0));
    const odoEnd = Number(this._attr(monthly, "odometer_end_km", odoStart + privateKm));
    const updated = this._attr(monthly, "updated_at", null);
    const records = Array.isArray(this._attr(monthly, "records", [])) ? this._attr(monthly, "records", []) : [];
    const manualDates = Array.isArray(this._attr(monthly, "manual_presence_dates", [])) ? this._attr(monthly, "manual_presence_dates", []) : [];
    const kelioPresenceCount = Math.max(0, presence - manualDates.length);

    if (!this._manualForm.date || !this._manualForm.date.startsWith(`${month}-`)) {
      this._manualForm.date = manualDates[0] || (month === currentMonth ? this._todayIso() : `${month}-01`);
    }

    const savedPdfs = Array.isArray(this._attr(pdfState, "saved_pdfs", []))
      ? this._attr(pdfState, "saved_pdfs", [])
      : [];
    const pdfMatchesMonth = pdfState?.state === month;
    const pdfGenerated = pdfMatchesMonth ? this._attr(pdfState, "generated_at", null) : null;
    const pdfError = pdfMatchesMonth ? this._attr(pdfState, "error", null) : null;
    const pdfUrl = pdfMatchesMonth ? this._attr(pdfState, "url", null) : null;

    const todayItems = [
      ["Kelio ma", this._onOff(this._state(ids.kelioToday))],
      ["Bejárás jár", this._onOff(this._state(ids.eligibleToday))],
      ["Céges autó mozgott", this._onOff(this._state(ids.movedToday))],
      ["Otthonról indult", this._onOff(this._state(ids.startedHome))],
      ["Otthon érkezett", this._onOff(this._state(ids.endedHome))],
      ["Munkahelyről indult", this._onOff(this._state(ids.startedWork))],
      ["Munkahelyre érkezett", this._onOff(this._state(ids.endedWork))],
    ];

    const rows = records.map((r) => {
      const car = r.company_car || null;
      const present = !!r.kelio_present;
      const manual = !!r.manual_override;
      const morning = r.morning_commute_eligible;
      const evening = r.evening_commute_eligible;
      const legCount = Number(r.eligible_legs ?? ([morning, evening].filter(v => v === true).length));
      const unknownCount = Number(r.unknown_legs ?? ([morning, evening].filter(v => v === null || v === undefined).length));
      const full = present && legCount === 2 && unknownCount === 0;
      const partial = present && legCount === 1;
      const unresolved = present && unknownCount > 0;
      const expanded = this._expandedDates.has(r.date);
      const rowClass = !present ? "off" : full ? "eligible" : (partial || unresolved) ? "warn" : "ineligible";
      const badge = !present ? `<span class="pill danger">NEM JÁR</span><span class="sub">Kelio nincs</span>`
        : full ? `<span class="pill success">JÁR · 2/2 ÚT</span>`
        : partial ? `<span class="pill warn">1 ÚT JÁR</span><span class="sub">${morning === true ? "reggel" : "este"}</span>`
        : unresolved ? `<span class="pill warn">?</span>`
        : `<span class="pill danger">NEM JÁR · 0/2</span>`;
      const toggle = car
        ? `<button class="row-toggle" data-date="${r.date}" title="Részletek">
             <ha-icon icon="mdi:${expanded ? "chevron-up" : "chevron-down"}"></ha-icon>
           </button>`
        : "";

      let detailRow = "";
      if (expanded && car) {
        const homeRadius = Number(car.home_radius_m);
        const workRadius = Number(car.work_radius_m);
        const routeSegments = Array.isArray(car.route_segments) ? car.route_segments : [];
        const diagnosticsExpanded = this._expandedDiagnostics.has(r.date);
        const routeSegmentRows = routeSegments.map((seg) => `
          <tr>
            <td class="route-id">${seg.route_id || "-"}</td>
            <td class="route-datetime"><b>${(seg.from_date || "-").replace(/^(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2}:\d{2})$/, "$1<br>$2")}</b></td>
            <td class="route-address">${seg.from_address || "-"}</td>
            <td class="route-datetime"><b>${(seg.to_date || "-").replace(/^(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2}:\d{2})$/, "$1<br>$2")}</b></td>
            <td class="route-address">${seg.to_address || "-"}</td>
            <td>${seg.travel_time || "-"}</td>
            <td class="num">${seg.distance || "-"}</td>
            <td class="num">${seg.average_speed || "-"}</td>
            <td class="num">${seg.max_speed || "-"}</td>
            <td>${seg.stop_time || "-"}</td>
          </tr>`).join("");
        detailRow = `
          <tr class="detail-row ${rowClass}">
            <td colspan="8">
              <div class="detail-wrap">
                <div class="detail-head">
                  <div>
                    <span class="detail-date">${this._day(r.date)}</span>
                    <b>${full ? "BEJÁRÁS JÁR · 2/2 ÚT" : partial ? "RÉSZBEN JÁR · 1/2 ÚT" : unresolved ? "NEM DÖNTHETŐ EL" : "BEJÁRÁS NEM JÁR · 0/2 ÚT"}</b>
                  </div>
                  <div class="detail-reason">${r.reason || "-"}</div>
                </div>

                <div class="essential-grid">
                  <div class="detail-card essential-card commute-card">
                    <h4><ha-icon icon="mdi:swap-horizontal"></ha-icon> Bejárási utak</h4>
                    <div class="detail-line"><span>Reggel · Otthon → Munkahely</span>${this._boolLabel(morning)}</div>
                    <div class="detail-line"><span>Este · Munkahely → Otthon</span>${this._boolLabel(evening)}</div>
                    <div class="detail-line"><span>Elszámolható utak</span><b>${legCount}/2</b></div>
                  </div>

                  <div class="detail-card essential-card">
                    <h4><ha-icon icon="mdi:car-clock"></ha-icon> Napi céges autó</h4>
                    <div class="detail-line"><span>Első mozgás</span><b>${this._timeSeconds(car.first_time)}</b></div>
                    <div class="detail-line"><span>Utolsó mozgás</span><b>${this._timeSeconds(car.last_time)}</b></div>
                    <div class="detail-line"><span>Menet időtartama</span><b>${this._duration(car.first_time, car.last_time)}</b></div>
                    <div class="detail-line"><span>Napi céges út</span><b>${this._num(car.distance_km, 2)} km</b></div>
                    <div class="detail-line"><span>Max. sebesség</span><b>${this._num(car.max_speed_kmh)} km/h</b></div>
                  </div>

                  <div class="detail-card essential-card">
                    <h4><ha-icon icon="mdi:map-marker-path"></ha-icon> Útvonal és export</h4>
                    <div class="route-export-actions compact-actions">
                      <a class="alap-history-link route-open"
                         href="/local/utnyilvantartas/routes/utvonal_${r.date}.html"
                         target="_blank"
                         rel="noopener noreferrer">
                        <ha-icon icon="mdi:map-marker-path"></ha-icon>
                        <span>Aznapi útvonal</span>
                      </a>
                      <a class="alap-history-link route-download"
                         href="/local/utnyilvantartas/routes/utvonal_${r.date}.gpx"
                         download="utvonal_${r.date}.gpx">
                        <ha-icon icon="mdi:download"></ha-icon>
                        <span>GPX</span>
                      </a>
                      <a class="alap-history-link provider-open"
                         href="https://service.alapnyomkovetes.hu/statistics/route"
                         target="_blank"
                         rel="noopener noreferrer">
                        <ha-icon icon="mdi:open-in-new"></ha-icon>
                        <span>Alapnyomkövetés</span>
                      </a>
                    </div>
                  </div>
                </div>

                <div class="route-stats-card">
                  <h4><ha-icon icon="mdi:routes"></ha-icon> Alapnyomkövetés · Kiértékelés</h4>
                  <div class="route-stats-summary">
                    <span>Az Alapnyomkövetés saját szerveroldali út-szakaszai.</span>
                    <div class="route-stats-summary-actions">
                      <b>${routeSegments.length} szakasz</b>
                      <a class="route-print-button"
                         href="/local/utnyilvantartas/routes/kiertekeles_${r.date}.html"
                         target="_blank"
                         rel="noopener noreferrer"
                         title="Térkép és Kiértékelés nyomtatható nézetben">
                        <ha-icon icon="mdi:printer"></ha-icon>
                        <span>Térkép + Kiértékelés nyomtatása</span>
                      </a>
                    </div>
                  </div>
                  ${routeSegments.length ? `
                    <div class="route-stats-scroll">
                      <table class="route-stats-table">
                        <thead>
                          <tr>
                            <th>Sorszám</th>
                            <th>Indulás</th>
                            <th>Honnan</th>
                            <th>Érkezés</th>
                            <th>Hova</th>
                            <th>Menetidő</th>
                            <th class="num">Távolság</th>
                            <th class="num">Átlag seb.</th>
                            <th class="num">Max. seb.</th>
                            <th>Állásidő</th>
                          </tr>
                        </thead>
                        <tbody>${routeSegmentRows}</tbody>
                      </table>
                    </div>` :
                    `<div class="route-stats-empty">${car.route_stats_available ? "Az Alapnyomkövetés erre a napra nem adott vissza kiértékelési szakaszt." : "A Statistics/Route lekérés ehhez a naphoz nem érhető el."}</div>`
                  }
                </div>

                <div class="diagnostics-bar">
                  <button class="diagnostics-toggle" data-date="${r.date}">
                    <ha-icon icon="mdi:${diagnosticsExpanded ? "chevron-up" : "tune-variant"}"></ha-icon>
                    <span>${diagnosticsExpanded ? "Diagnosztika elrejtése" : "Technikai részletek / diagnosztika"}</span>
                  </button>
                  <span>Csak hibakereséshez szükséges.</span>
                </div>

                ${diagnosticsExpanded ? `
                <div class="detail-grid diagnostics-grid">
                  <div class="detail-card">
                    <h4><ha-icon icon="mdi:home-map-marker"></ha-icon> Otthon · diagnosztika</h4>
                    <div class="detail-line"><span>Otthonról indult</span>${this._boolLabel(car.started_home)}</div>
                    <div class="detail-line"><span>Otthon érkezett</span>${this._boolLabel(car.ended_home)}</div>
                    <div class="detail-line"><span>Otthont érintette</span>${this._boolLabel(car.touched_home)}</div>
                    <div class="detail-line zone"><span>Indulási ablak legközelebb</span><span>${this._distanceTime(car.start_home_min_distance_m, car.start_home_min_time, homeRadius)}</span></div>
                    <div class="detail-line zone"><span>Érkezési ablak legközelebb</span><span>${this._distanceTime(car.end_home_min_distance_m, car.end_home_min_time, homeRadius)}</span></div>
                    <div class="detail-foot">Felismerési sugár: ${this._num(homeRadius)} m</div>
                  </div>

                  <div class="detail-card">
                    <h4><ha-icon icon="mdi:office-building-marker"></ha-icon> Munkahely · diagnosztika</h4>
                    <div class="detail-line"><span>Munkahelyről indult</span>${this._boolLabel(car.started_work)}</div>
                    <div class="detail-line"><span>Munkahelyre érkezett</span>${this._boolLabel(car.ended_work)}</div>
                    <div class="detail-line"><span>Munkahelyet érintette</span>${this._boolLabel(car.touched_work)}</div>
                    <div class="detail-line zone"><span>Indulási ablak legközelebb</span><span>${this._distanceTime(car.start_work_min_distance_m, car.start_work_min_time, workRadius)}</span></div>
                    <div class="detail-line zone"><span>Érkezési ablak legközelebb</span><span>${this._distanceTime(car.end_work_min_distance_m, car.end_work_min_time, workRadius)}</span></div>
                    <div class="detail-foot">Felismerési sugár: ${this._num(workRadius)} m</div>
                  </div>

                  <div class="detail-card night-card">
                    <h4><ha-icon icon="mdi:weather-night"></ha-icon> Éjszakai Otthon-ellenőrzés · EZ DÖNT</h4>
                    <div class="decision-rule">A céges autóra vonatkozó JÁR / NEM JÁR döntés ebből a két 18:00–06:00 időablakból készül.</div>
                    <div class="detail-line"><span>Előző este 18:00 → reggel 06:00</span>${this._boolLabel(car.overnight_before_home)}</div>
                    <div class="detail-line"><span>Első Otthon-zóna pont</span><b>${this._timeSeconds(car.overnight_before_first_home_time)}</b></div>
                    <div class="detail-line"><span>Utolsó Otthon-zóna pont</span><b>${this._timeSeconds(car.overnight_before_last_home_time)}</b></div>
                    <div class="detail-line zone"><span>Legközelebbi pont</span><span>${this._distanceTime(car.overnight_before_min_distance_m, car.overnight_before_min_time, car.overnight_home_radius_m)}</span></div>
                    <div class="detail-line"><span>Zónán belüli GPS-pont</span><b>${this._num(car.overnight_before_points_inside)}</b></div>
                    <div class="detail-line"><span>Döntési bizonyíték</span><b>${this._nightEvidence(car.overnight_before_evidence)}</b></div>
                    <div class="detail-line zone"><span>Utolsó ismert helyzet 18:00 előtt</span><span>${this._distanceTime(car.overnight_before_anchor_distance_m, car.overnight_before_anchor_time, car.overnight_home_radius_m)}</span></div>
                    <div class="night-separator"></div>
                    <div class="detail-line"><span>18:00 → másnap 06:00</span>${this._boolLabel(car.overnight_after_home)}</div>
                    <div class="detail-line"><span>Első Otthon-zóna pont</span><b>${this._timeSeconds(car.overnight_after_first_home_time)}</b></div>
                    <div class="detail-line"><span>Utolsó Otthon-zóna pont</span><b>${this._timeSeconds(car.overnight_after_last_home_time)}</b></div>
                    <div class="detail-line zone"><span>Legközelebbi pont</span><span>${this._distanceTime(car.overnight_after_min_distance_m, car.overnight_after_min_time, car.overnight_home_radius_m)}</span></div>
                    <div class="detail-line"><span>Zónán belüli GPS-pont</span><b>${this._num(car.overnight_after_points_inside)}</b></div>
                    <div class="detail-line"><span>Döntési bizonyíték</span><b>${this._nightEvidence(car.overnight_after_evidence)}</b></div>
                    <div class="detail-line zone"><span>Utolsó ismert helyzet 18:00 előtt</span><span>${this._distanceTime(car.overnight_after_anchor_distance_m, car.overnight_after_anchor_time, car.overnight_home_radius_m)}</span></div>
                    <div class="detail-foot">Tényleges zone.home sugár: ${this._num(car.overnight_home_radius_m)} m · vizsgálat: 18:00–06:00.</div>
                  </div>

                  <div class="detail-card">
                    <h4><ha-icon icon="mdi:crosshairs-gps"></ha-icon> GPS diagnosztika</h4>
                    <div class="detail-line"><span>Indulás/érkezés vizsgált ablak</span><b>${this._num(car.endpoint_window_km, 1)} km</b></div>
                    <div class="detail-line"><span>Összes tracker rekord</span><b>${this._num(car.total_records)}</b></div>
                    <div class="detail-line"><span>Érvényes GPS-pont</span><b>${this._num(car.valid_points)}</b></div>
                    <div class="detail-line"><span>Egyedi GPS-pont</span><b>${this._num(car.unique_points)}</b></div>
                    <div class="detail-line"><span>Első koordináta</span><b class="coord">${car.first_point ? `${Number(car.first_point.latitude).toFixed(6)}, ${Number(car.first_point.longitude).toFixed(6)}` : "-"}</b></div>
                    <div class="detail-line"><span>Utolsó koordináta</span><b class="coord">${car.last_point ? `${Number(car.last_point.latitude).toFixed(6)}, ${Number(car.last_point.longitude).toFixed(6)}` : "-"}</b></div>
                  </div>
                </div>` : ""}
              </div>
            </td>
          </tr>`;
      }

      return `
        <tr class="${rowClass} main-row">
          <td class="expand-cell">${toggle}</td>
          <td class="date">${this._day(r.date)}</td>
          <td>${manual ? '<span class="manual-mark">KÉZI</span>' : present ? '<span class="check">✓</span>' : '<span class="dash">-</span>'}</td>
          <td>${badge}</td>
          <td class="num">${car ? `${this._num(car.distance_km, 2)} km` : "-"}</td>
          <td>${car ? this._routeStart(car) : "-"}<span class="sub">${car ? this._timeSeconds(car.first_time) : ""}</span></td>
          <td>${car ? this._routeEnd(car) : "-"}<span class="sub">${car ? this._timeSeconds(car.last_time) : ""}</span></td>
          <td class="reason">${r.reason || "-"}</td>
        </tr>${detailRow}`;
    }).join("");

    const savedPdfRows = savedPdfs.map((item) => {
      const modified = item.modified_ts ? this._dateTime(new Date(Number(item.modified_ts) * 1000).toISOString()) : "-";
      const sizeKb = item.size_bytes ? `${this._num(Number(item.size_bytes) / 1024, 0)} kB` : "-";
      const url = item.url || `/local/utnyilvantartas/${item.filename}`;
      return `
        <div class="saved-pdf-row">
          <div class="saved-pdf-main">
            <ha-icon icon="mdi:file-pdf-box"></ha-icon>
            <div><b>${item.month || item.filename}</b><span>${item.filename || ""} · ${modified} · ${sizeKb}</span></div>
          </div>
          <div class="saved-pdf-actions">
            <button class="saved-pdf-preview" data-url="${url}"><ha-icon icon="mdi:eye-outline"></ha-icon><span>Megjelenítés</span></button>
            <a href="${url}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon><span>Új lap</span></a>
            <button class="saved-pdf-print" data-url="${url}"><ha-icon icon="mdi:printer-outline"></ha-icon><span>Nyomtatás</span></button>
          </div>
        </div>`;
    }).join("");

    const message = this._message
      ? `<div class="notice ${this._message.type}">${this._message.text}</div>`
      : "";

    const missingDaily = [ids.dailyDistance, ids.maxSpeed, ids.firstMove, ids.lastMove]
      .filter((id) => !this._state(id)).length;

    this.shadowRoot.innerHTML = `
      <ha-card class="shell">
        <div class="hero">
          <div class="hero-main">
            <div class="eyebrow">HOME ASSISTANT · ÚTNYILVÁNTARTÁS</div>
            <div class="month-nav">
              <button id="month-prev" class="month-arrow" title="Előző hónap" ${this._busy ? "disabled" : ""}>
                <ha-icon icon="mdi:chevron-left"></ha-icon>
              </button>
              <div class="month-title-wrap">
                <div class="title">${this._monthLabel(month)}</div>
                <div class="month-code">${month}</div>
              </div>
              <button id="month-next" class="month-arrow" title="Következő hónap" ${(!canGoNext || this._busy) ? "disabled" : ""}>
                <ha-icon icon="mdi:chevron-right"></ha-icon>
              </button>
              ${!monthIsCurrent ? `
                <button id="month-current" class="current-month" ${this._busy ? "disabled" : ""}>
                  <ha-icon icon="mdi:calendar-today"></ha-icon>
                  Aktuális hónap
                </button>` : ""}
            </div>
            <div class="updated">${this._busy === "month" ? "Hónap betöltése folyamatban…" : `Utolsó havi frissítés: ${this._dateTime(updated)}`}</div>
          </div>
          <div class="hero-odo">
            <div><span>Kezdő óraállás</span><b>${this._num(odoStart)} km</b></div>
            <div class="arrow">→</div>
            <div><span>Befejező óraállás</span><b>${this._num(odoEnd)} km</b></div>
          </div>
        </div>

        ${message}

        <div class="content">
          <section class="stats">
            <div class="stat"><div class="icon kelio"><ha-icon icon="mdi:account-check"></ha-icon></div><div><span>Jelenlét</span><b>${presence} nap</b><small>Kelio: ${kelioPresenceCount} · kézi: ${manualDates.length}</small></div></div>
            <div class="stat"><div class="icon success"><ha-icon icon="mdi:cash-check"></ha-icon></div><div><span>Bejárás jár</span><b>${this._num(eligible, eligible % 1 ? 1 : 0)} nap</b><small>${eligibleLegs} egyirányú út</small></div></div>
            <div class="stat"><div class="icon danger"><ha-icon icon="mdi:cash-remove"></ha-icon></div><div><span>Nem jár</span><b>${this._num(ineligible, ineligible % 1 ? 1 : 0)} nap</b><small>${ineligibleLegs} egyirányú út${unknownLegs ? ` · ${unknownLegs} függőben` : ""}</small></div></div>
            <div class="stat"><div class="icon private"><ha-icon icon="mdi:car"></ha-icon></div><div><span>Saját autó ebben a hónapban</span><b>${this._num(privateKm, privateKm % 1 ? 1 : 0)} km</b></div></div>
            <div class="stat"><div class="icon money"><ha-icon icon="mdi:cash-multiple"></ha-icon></div><div><span>Bejárási térítés</span><b>${this._num(reimbursement)} Ft</b></div></div>
            <div class="stat"><div class="icon company"><ha-icon icon="mdi:car-connected"></ha-icon></div><div><span>Céges autó vizsgált útjai</span><b>${this._num(companyKm, 2)} km</b></div></div>
          </section>

          <section class="action-panel" id="action-panel">
            <div class="section-title"><ha-icon icon="mdi:cog-sync-outline"></ha-icon><span>Műveletek</span></div>
            <div class="actions">
              <button id="refresh" class="action primary" ${this._busy ? "disabled" : ""}>
                <ha-icon icon="mdi:calendar-sync"></ha-icon>
                <span>${this._busy === "refresh" ? "Frissítés folyamatban…" : "Havi adatok frissítése"}</span>
              </button>
              <button id="generate" class="action pdf" ${this._busy ? "disabled" : ""}>
                <ha-icon icon="mdi:file-pdf-box"></ha-icon>
                <span>${this._busy === "pdf" ? "PDF készül…" : "PDF generálása"}</span>
              </button>
              <button id="email" class="action email" ${this._busy ? "disabled" : ""}>
                <ha-icon icon="mdi:email-fast-outline"></ha-icon>
                <span>${this._busy === "email" ? "E-mail küldése…" : "PDF küldése e-mailben"}</span>
              </button>
              <button id="open" class="action secondary" ${this._busy ? "disabled" : ""}>
                <ha-icon icon="mdi:open-in-new"></ha-icon>
                <span>PDF megnyitása</span>
              </button>
            </div>
            <div class="pdf-status ${pdfError ? "bad" : ""}">
              <ha-icon icon="${pdfError ? "mdi:alert-circle" : pdfGenerated ? "mdi:file-check" : "mdi:file-clock-outline"}"></ha-icon>
              <div>
                <b>${pdfError ? "PDF hiba" : pdfGenerated ? "PDF elkészült" : "Még nincs ebben a munkamenetben generált PDF"}</b>
                <span>${pdfError || (pdfGenerated ? `${this._dateTime(pdfGenerated)}${pdfUrl ? " · megnyitható" : ""}` : `A megnyitás a ${month} havi fájlt keresi.`)}</span>
              </div>
            </div>
          </section>

          <section class="panel pdf-library">
            <div class="section-title pdf-library-title">
              <ha-icon icon="mdi:archive-eye-outline"></ha-icon>
              <span>Mentett PDF-ek</span>
              <b>${savedPdfs.length} db</b>
            </div>
            <div class="pdf-library-note">A <code>/config/www/utnyilvantartas/</code> mappában mentett havi PDF-ek.</div>
            <div class="saved-pdf-list">
              ${savedPdfRows || '<div class="saved-pdf-empty">Még nincs mentett havi PDF.</div>'}
            </div>
            ${this._pdfPreviewUrl ? `
              <div class="pdf-preview-wrap">
                <div class="pdf-preview-head"><b>PDF előnézet</b><button id="pdf-preview-close"><ha-icon icon="mdi:close"></ha-icon> Bezárás</button></div>
                <iframe class="pdf-preview-frame" src="${this._pdfPreviewUrl}" title="Mentett PDF előnézet"></iframe>
              </div>` : ""}
          </section>

          <section class="panel manual-panel">
            <div class="section-title">
              <ha-icon icon="mdi:calendar-edit"></ha-icon>
              <span>Kézi kiegészítés · hónap végi leadáshoz</span>
            </div>
            <div class="manual-explain">
              Ha az utolsó munkanapon már le kell adnod a PDF-et, de a Kelio aznapi
              bejegyzése még nem érhető el, itt kézzel hozzáadhatod a reggeli/esti bejárási utat.
              Amikor később megérkezik a valódi Kelio adat, az automatikusan felülírja és törli a kézi kiegészítést.
            </div>
            <div class="manual-form" id="manual-form">
              <label>
                <span>Dátum</span>
                <input id="manual-date" type="date" value="${this._manualForm.date}" max="${this._todayIso()}" autocomplete="off">
              </label>
              <label class="manual-check">
                <input id="manual-morning" type="checkbox" ${this._manualForm.morning ? "checked" : ""}>
                <span>Reggeli út jár<br><small>Otthon → Munkahely</small></span>
              </label>
              <label class="manual-check">
                <input id="manual-evening" type="checkbox" ${this._manualForm.evening ? "checked" : ""}>
                <span>Esti út jár<br><small>Munkahely → Otthon</small></span>
              </label>
              <label class="manual-note">
                <span>Megjegyzés</span>
                <textarea id="manual-note" rows="2">${String(this._manualForm.note || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</textarea>
              </label>
              <div class="manual-actions">
                <button id="manual-save" class="manual-save" ${this._busy ? "disabled" : ""}>
                  <ha-icon icon="mdi:content-save-check"></ha-icon>
                  ${this._busy === "manual" ? "Mentés…" : "Mentés / felülírás"}
                </button>
                <button id="manual-remove" class="manual-remove" ${this._busy ? "disabled" : ""}>
                  <ha-icon icon="mdi:delete-outline"></ha-icon>
                  Törlés
                </button>
              </div>
            </div>
            <div class="manual-active">
              <b>Aktív kézi napok ebben a hónapban:</b>
              ${manualDates.length ? manualDates.map(d => `<span>${this._day(d)}</span>`).join("") : "<em>nincs</em>"}
            </div>
          </section>

          <div class="two-col">
            <section class="panel">
              <div class="section-title"><ha-icon icon="mdi:calendar-today"></ha-icon><span>Mai állapot${monthIsCurrent ? "" : " · aktuális nap"}</span></div>
              <div class="today-grid">
                ${todayItems.map(([label, value]) => `<div class="today"><span>${label}</span><b class="${value.cls}"><ha-icon icon="${value.icon}"></ha-icon>${value.text}</b></div>`).join("")}
              </div>
              <div class="daily-metrics">
                <div><span>Mai céges km</span><b>${this._state(ids.dailyDistance) ? `${this._num(this._state(ids.dailyDistance).state, 2)} km` : "-"}</b></div>
                <div><span>Max. sebesség</span><b>${this._state(ids.maxSpeed) ? `${this._num(this._state(ids.maxSpeed).state)} km/h` : "-"}</b></div>
                <div><span>Első mozgás</span><b>${this._time(this._state(ids.firstMove)?.state)}</b></div>
                <div><span>Utolsó mozgás</span><b>${this._time(this._state(ids.lastMove)?.state)}</b></div>
                <div><span>GPS pontok</span><b>${this._state(ids.gpsPoints)?.state ?? "-"}</b></div>
              </div>
              ${missingDaily ? `<div class="hint">${missingDaily} mai entitás jelenleg nem található; a havi rész ettől még működik.</div>` : ""}
            </section>

            <section class="panel odometer-panel">
              <div class="section-title"><ha-icon icon="mdi:counter"></ha-icon><span>Óraállás és elszámolás</span></div>
              <div class="odo-line"><span>Kezdő óraállás</span><b>${this._num(odoStart)} km</b></div>
              <div class="odo-add"><span>+ havi elszámolt saját autó</span><b>+ ${this._num(privateKm, privateKm % 1 ? 1 : 0)} km</b></div>
              <div class="odo-line total"><span>Befejező óraállás</span><b>${this._num(odoEnd)} km</b></div>
              <div class="divider"></div>
              <div class="money-line"><span>Egységár</span><b>${this._num(this._attr(monthly, "reimbursement_huf_per_km", 0))} Ft/km</b></div>
              <div class="money-line strong"><span>Havi térítés</span><b>${this._num(reimbursement)} Ft</b></div>
              <div class="hint">Az óraállás hónapról hónapra folyamatos. Ha egy korábbi hónapot utólag töltesz vissza, az utána következő hónapok kezdő/befejező óraállása is ehhez igazodik.</div>
            </section>
          </div>

          <section class="panel table-panel">
            <div class="section-title row-title">
              <div><ha-icon icon="mdi:table-large"></ha-icon><span>Havi napi bontás</span><small class="row-hint">A nyíllal lenyitható a döntés teljes GPS- és időpont-diagnosztikája.</small></div>
              <div class="legend"><span class="dot green"></span>2/2 út jár <span class="dot orange"></span>1/2 út jár / függő <span class="dot red"></span>0/2 út jár <span class="manual-mark">KÉZI</span> kézi kiegészítés <span class="dot gray"></span>Nincs jelenlét</div>
            </div>
            <div class="table-scroll">
              <table>
                <thead><tr><th class="expand-cell"></th><th>Dátum</th><th>Kelio</th><th>Bejárás</th><th class="num">Céges km</th><th>Indulás</th><th>Érkezés</th><th>Indok</th></tr></thead>
                <tbody>${rows || `<tr><td colspan="8" class="empty">Még nincs havi adat.</td></tr>`}</tbody>
              </table>
            </div>
          </section>
        </div>
      </ha-card>

      <style>
        :host { display:block; --ok: var(--success-color,#43a047); --bad: var(--error-color,#e53935); --warn:#f9a825; }
        * { box-sizing:border-box; }
        button,a,input,select,textarea { transition:none !important; }
        .panel,.action-panel,.stat,.detail-card,.pdf-status,.section-title { min-width:0; }
        .section-title span,.stat span,.stat small,.pdf-status span,.detail-reason,.manual-explain { overflow-wrap:anywhere; word-break:normal; }
        .shell { overflow:hidden; border-radius:20px; }
        .hero { padding:24px 28px; background:linear-gradient(135deg,var(--primary-color,#1976d2),#5068a9); color:white; display:flex; justify-content:space-between; align-items:center; gap:24px; }
        .eyebrow { font-size:11px; font-weight:800; opacity:.82; letter-spacing:.12em; }
        .title { font-size:30px; line-height:1.05; font-weight:800; text-transform:capitalize; }
        .hero-main { min-width:0; }
        .month-nav { display:flex; align-items:center; gap:10px; margin-top:7px; flex-wrap:wrap; }
        .month-title-wrap { min-width:220px; }
        .month-code { font-size:11px; opacity:.72; margin-top:3px; letter-spacing:.06em; }
        .month-arrow,.current-month { appearance:none; border:1px solid rgba(255,255,255,.24); background:rgba(255,255,255,.13); color:white; border-radius:11px; min-width:42px; height:42px; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; font:inherit; font-weight:700; }
        .month-arrow:hover:not(:disabled),.current-month:hover:not(:disabled) { background:rgba(255,255,255,.13); box-shadow:0 0 0 2px rgba(255,255,255,.24) inset; }
        .month-arrow:disabled,.current-month:disabled { opacity:.38; cursor:not-allowed; }
        .current-month { padding:0 12px; gap:6px; white-space:nowrap; font-size:12px; }
        .updated { font-size:12px; opacity:.85; margin-top:7px; }
        .hero-odo { display:flex; align-items:center; gap:18px; background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.18); border-radius:16px; padding:12px 16px; }
        .hero-odo div:not(.arrow) { display:flex; flex-direction:column; gap:2px; white-space:nowrap; }
        .hero-odo span { font-size:11px; opacity:.8; }
        .hero-odo b { font-size:17px; }
        .arrow { font-size:22px; opacity:.6; }
        .content { padding:18px; display:flex; flex-direction:column; gap:16px; }
        .stats { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; }
        .stat { min-height:84px; padding:13px; background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:15px; display:flex; align-items:center; gap:11px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
        .stat .icon { width:38px; height:38px; border-radius:11px; display:grid; place-items:center; flex:0 0 38px; background:color-mix(in srgb,var(--primary-color) 13%,var(--card-background-color)); color:var(--primary-color); }
        .stat .icon.success { background:color-mix(in srgb,var(--ok) 13%,var(--card-background-color)); color:var(--ok); }
        .stat .icon.danger { background:color-mix(in srgb,var(--bad) 12%,var(--card-background-color)); color:var(--bad); }
        .stat .icon.money { background:color-mix(in srgb,#8e24aa 12%,var(--card-background-color)); color:#8e24aa; }
        .stat small { display:block; margin-top:2px; font-size:9px; color:var(--secondary-text-color); }
        .stat .icon.company { background:color-mix(in srgb,#546e7a 14%,var(--card-background-color)); color:#546e7a; }
        .stat .icon.private { background:color-mix(in srgb,#00897b 14%,var(--card-background-color)); color:#00897b; }
        .stat span { display:block; font-size:11px; color:var(--secondary-text-color); line-height:1.2; margin-bottom:4px; }
        .stat b { font-size:17px; color:var(--primary-text-color); white-space:nowrap; }
        .panel,.action-panel { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:16px; padding:16px; }
        .section-title { display:flex; align-items:center; gap:8px; font-size:15px; font-weight:750; margin-bottom:13px; color:var(--primary-text-color); }
        .section-title ha-icon { color:var(--primary-color); }
        .actions { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }
        button.action { appearance:none; border:0; border-radius:12px; min-height:48px; padding:10px 14px; display:flex; align-items:center; justify-content:center; gap:8px; font:inherit; font-weight:700; cursor:pointer; transition:none; transform:none; filter:none; }
        button.action:hover:not(:disabled) { transform:none; filter:none; box-shadow:0 0 0 2px rgba(255,255,255,.28) inset; }
        button.secondary:hover:not(:disabled) { box-shadow:0 0 0 2px color-mix(in srgb,var(--primary-color) 30%,transparent) inset; }
        button.action:active:not(:disabled) { transform:none; filter:none; }
        button.action:disabled { opacity:.55; cursor:wait; }
        button.primary { background:var(--primary-color); color:white; }
        button.pdf { background:#d32f2f; color:white; }
        button.email { background:#00897b; color:white; }
        button.secondary { background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color)); color:var(--primary-color); border:1px solid color-mix(in srgb,var(--primary-color) 28%,var(--divider-color)); }
        .pdf-status { display:flex; align-items:center; gap:10px; padding:11px 12px; border-radius:12px; margin-top:10px; background:color-mix(in srgb,var(--ok) 8%,var(--card-background-color)); }
        .pdf-status.bad { background:color-mix(in srgb,var(--bad) 9%,var(--card-background-color)); color:var(--bad); }
        .pdf-status div { display:flex; flex-direction:column; gap:2px; }
        .pdf-status b { font-size:12px; }
        .manual-panel { border-color:color-mix(in srgb,#f9a825 38%,var(--divider-color)); }
        .manual-explain { margin:-3px 0 12px; padding:9px 11px; border-radius:10px; background:color-mix(in srgb,#f9a825 10%,var(--card-background-color)); color:var(--secondary-text-color); font-size:10.5px; line-height:1.45; }
        .manual-form { display:grid; grid-template-columns:minmax(145px,170px) minmax(160px,1fr) minmax(160px,1fr) minmax(240px,1.6fr); gap:9px; align-items:end; min-width:0; }
        .manual-form>label { display:flex; flex-direction:column; gap:5px; min-width:0; font-size:10px; color:var(--secondary-text-color); }
        .manual-form input[type="date"],.manual-form input[type="text"],.manual-form textarea { width:100%; max-width:100%; min-width:0; min-height:40px; padding:9px 10px; border:1px solid var(--divider-color); border-radius:9px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; font-size:11px; line-height:1.35; overflow-wrap:anywhere; word-break:break-word; }
        .manual-form textarea { resize:vertical; min-height:56px; white-space:pre-wrap; }
        .manual-form .manual-check { min-height:40px; flex-direction:row; align-items:center; gap:8px; padding:6px 9px; border:1px solid var(--divider-color); border-radius:9px; background:var(--secondary-background-color); color:var(--primary-text-color); }
        .manual-check input { width:18px; height:18px; }
        .manual-check small { color:var(--secondary-text-color); }
        .manual-actions { grid-column:1/-1; display:flex; justify-content:flex-end; gap:9px; min-width:0; }
        .manual-save,.manual-remove { appearance:none; min-height:40px; max-width:100%; padding:0 14px; border-radius:9px; border:0; display:flex; align-items:center; justify-content:center; gap:6px; font:inherit; font-size:10.5px; font-weight:750; cursor:pointer; white-space:normal; text-align:center; overflow-wrap:anywhere; }
        .manual-save { background:#f9a825; color:#111; }
        .manual-remove { background:color-mix(in srgb,var(--bad) 11%,var(--card-background-color)); color:var(--bad); border:1px solid color-mix(in srgb,var(--bad) 30%,var(--divider-color)); }
        .manual-save:disabled,.manual-remove:disabled { opacity:.55; cursor:wait; }
        .manual-active { display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin-top:10px; font-size:10px; color:var(--secondary-text-color); }
        .manual-active span,.manual-mark { display:inline-flex; align-items:center; justify-content:center; border-radius:999px; padding:3px 7px; background:color-mix(in srgb,#f9a825 18%,var(--card-background-color)); color:#9a6500; border:1px solid color-mix(in srgb,#f9a825 42%,var(--divider-color)); font-size:9px; font-weight:800; white-space:nowrap; }
        .manual-active em { opacity:.75; }
        .pdf-status span { font-size:11px; color:var(--secondary-text-color); }
        .two-col { display:grid; grid-template-columns:1.15fr .85fr; gap:16px; }
        .today-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
        .today { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:9px 10px; border-radius:10px; background:var(--secondary-background-color); }
        .today>span { font-size:12px; color:var(--secondary-text-color); }
        .today b { display:flex; align-items:center; gap:4px; font-size:12px; white-space:nowrap; }
        .today b.success { color:var(--ok); }.today b.muted { color:var(--secondary-text-color); }
        .today ha-icon { --mdc-icon-size:17px; }
        .daily-metrics { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:7px; margin-top:9px; }
        .daily-metrics div { padding:9px; border:1px solid var(--divider-color); border-radius:10px; }
        .daily-metrics span { display:block; font-size:10px; color:var(--secondary-text-color); margin-bottom:3px; }
        .daily-metrics b { font-size:12px; }
        .odo-line,.odo-add,.money-line { display:flex; justify-content:space-between; align-items:center; padding:9px 3px; gap:12px; }
        .odo-line span,.odo-add span,.money-line span { font-size:12px; color:var(--secondary-text-color); }
        .odo-line b,.odo-add b,.money-line b { font-size:15px; white-space:nowrap; }
        .odo-add { color:#00897b; }.odo-add span { color:#00897b; }
        .odo-line.total { border-top:1px solid var(--divider-color); margin-top:3px; padding-top:12px; }
        .odo-line.total b { font-size:19px; color:var(--primary-color); }
        .divider { height:1px; background:var(--divider-color); margin:8px 0; }
        .money-line.strong b { color:var(--ok); font-size:20px; }
        .hint { font-size:10px; line-height:1.35; color:var(--secondary-text-color); margin-top:9px; }
        .row-title { justify-content:space-between; }
        .row-title>div:first-child { display:flex; align-items:center; gap:8px; }
        .legend { font-size:10px; font-weight:500; color:var(--secondary-text-color); display:flex; align-items:center; gap:5px; }
        .dot { width:8px;height:8px;border-radius:50%;display:inline-block;margin-left:6px; }.dot.green{background:var(--ok)}.dot.red{background:var(--bad)}.dot.gray{background:#9e9e9e}
        .table-scroll { overflow:auto; border:1px solid var(--divider-color); border-radius:12px; }
        table { width:100%; border-collapse:collapse; min-width:900px; font-size:12px; }
        th { text-align:left; padding:10px 9px; color:var(--secondary-text-color); background:var(--secondary-background-color); position:sticky; top:0; z-index:1; font-size:10px; text-transform:uppercase; letter-spacing:.035em; }
        td { padding:10px 9px; border-top:1px solid var(--divider-color); vertical-align:middle; }
        td.num,th.num { text-align:right; white-space:nowrap; }
        td.date { font-weight:750; white-space:nowrap; }
        tr.eligible td:first-child { box-shadow:inset 3px 0 0 var(--ok); }
        tr.ineligible td:first-child { box-shadow:inset 3px 0 0 var(--bad); }
        tr.off { opacity:.62; }
        .pill { display:inline-flex; padding:3px 7px; border-radius:999px; font-weight:800; font-size:9px; white-space:nowrap; }
        .pill.success { background:color-mix(in srgb,var(--ok) 14%,transparent); color:var(--ok); }
        .pill.danger { background:color-mix(in srgb,var(--bad) 13%,transparent); color:var(--bad); }
        .pill.warn { background:color-mix(in srgb,var(--warn) 15%,transparent); color:var(--warn); }
        .pill.muted { background:var(--secondary-background-color); color:var(--secondary-text-color); }
        .check { color:var(--ok); font-weight:900; }.dash{color:var(--secondary-text-color)}
        .row-toggle { appearance:none; width:34px; height:34px; border:1px solid var(--divider-color); border-radius:9px; background:var(--secondary-background-color); color:var(--primary-text-color); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; padding:0; transition:none; transform:none; filter:none; }
        .row-toggle:hover,.row-toggle:active { background:var(--secondary-background-color); transform:none; filter:none; box-shadow:0 0 0 2px color-mix(in srgb,var(--primary-color) 22%,transparent) inset; }
        .row-toggle ha-icon { --mdc-icon-size:19px; pointer-events:none; }
        .sub { display:block; color:var(--secondary-text-color); font-size:10px; margin-top:2px; }
        .reason { max-width:420px; line-height:1.3; }
        .empty { text-align:center; color:var(--secondary-text-color); padding:24px; }
        .notice { margin:14px 18px 0; padding:11px 13px; border-radius:12px; font-size:12px; font-weight:650; }
        .notice.success { background:color-mix(in srgb,var(--ok) 12%,var(--card-background-color)); color:var(--ok); }
        .notice.error { background:color-mix(in srgb,var(--bad) 12%,var(--card-background-color)); color:var(--bad); }
        .detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(235px,1fr)); gap:10px; }
        .essential-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:12px 0 4px; }
        .essential-card { min-width:0; }
        .compact-actions { grid-template-columns:1.35fr .65fr 1fr; margin-top:4px; }
        .compact-actions .alap-history-link { min-height:38px; padding:8px 9px; }
        .diagnostics-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:12px 0 2px; padding:9px 10px; border-radius:10px; background:var(--secondary-background-color); border:1px solid var(--divider-color); }
        .diagnostics-bar>span { color:var(--secondary-text-color); font-size:10px; }
        .diagnostics-toggle { appearance:none; border:0; background:transparent; color:var(--primary-color); cursor:pointer; display:inline-flex; align-items:center; gap:7px; padding:5px 6px; font:inherit; font-size:11px; font-weight:750; transition:none; transform:none; filter:none; }
        .diagnostics-toggle:hover,.diagnostics-toggle:active { background:transparent; transform:none; filter:none; text-decoration:underline; }
        .diagnostics-toggle ha-icon { --mdc-icon-size:17px; }
        .diagnostics-grid { margin-top:10px; padding-top:10px; border-top:1px dashed var(--divider-color); }
        .route-export-actions { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,.8fr) minmax(0,1fr); gap:7px; margin-top:12px; }
        .alap-history-link { min-height:42px; padding:9px 11px; border-radius:10px; display:flex; align-items:center; gap:8px; text-decoration:none; color:var(--primary-color); background:color-mix(in srgb,var(--primary-color) 9%,var(--card-background-color)); border:1px solid color-mix(in srgb,var(--primary-color) 28%,var(--divider-color)); font-size:11px; font-weight:750; }
        .alap-history-link:hover { filter:none; box-shadow:0 0 0 2px color-mix(in srgb,currentColor 20%,transparent) inset; }
        .alap-history-link ha-icon { --mdc-icon-size:17px; flex:0 0 auto; }
        .alap-history-link span { flex:1; }
        .alap-history-link b { white-space:nowrap; font-size:10px; color:var(--secondary-text-color); }
        .route-download { color:#2e7d32; background:color-mix(in srgb,#2e7d32 9%,var(--card-background-color)); border-color:color-mix(in srgb,#2e7d32 28%,var(--divider-color)); }
        .provider-open { color:#546e7a; background:color-mix(in srgb,#546e7a 8%,var(--card-background-color)); border-color:color-mix(in srgb,#546e7a 25%,var(--divider-color)); }
        .alap-note { margin-top:7px; }
        .route-stats-card { min-width:0; margin:12px 0 14px; background:var(--card-background-color); border:2px solid color-mix(in srgb,var(--primary-color) 28%,var(--divider-color)); border-radius:14px; padding:13px; box-shadow:0 2px 8px rgba(0,0,0,.05); }
        .route-stats-card h4 { display:flex; align-items:center; gap:7px; margin:0 0 10px; font-size:14px; }
        .route-stats-card h4 ha-icon { color:var(--primary-color); }
        .route-stats-summary { display:flex; justify-content:space-between; gap:12px; align-items:center; margin:0 0 10px; padding:9px 11px; border-radius:9px; background:color-mix(in srgb,var(--primary-color) 10%,transparent); font-size:10px; color:var(--secondary-text-color); }
        .route-stats-summary>b { color:var(--primary-text-color); white-space:nowrap; font-size:11px; }
        .route-stats-summary-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
        .route-stats-summary-actions>b { color:var(--primary-text-color); white-space:nowrap; font-size:11px; }
        .route-print-button { display:inline-flex; align-items:center; gap:6px; min-height:32px; padding:6px 9px; border-radius:8px; text-decoration:none; color:#fff; background:var(--primary-color); font-size:10px; font-weight:750; white-space:nowrap; }
        .route-print-button:hover { filter:none; box-shadow:0 0 0 2px rgba(255,255,255,.25) inset; }
        .route-print-button ha-icon { --mdc-icon-size:15px; }
        .route-stats-scroll { overflow:visible; border:1px solid var(--divider-color); border-radius:10px; background:var(--card-background-color); }
        .route-stats-table { width:100%; min-width:0; table-layout:fixed; border-collapse:collapse; font-size:9.5px; }
        .route-stats-table th { position:sticky; top:0; z-index:1; padding:8px 5px; font-size:8.5px; background:var(--secondary-background-color); text-transform:uppercase; line-height:1.2; white-space:normal; overflow-wrap:anywhere; }
        .route-stats-table td { padding:8px 5px; border-top:1px solid var(--divider-color); vertical-align:top; line-height:1.35; overflow-wrap:anywhere; word-break:normal; }
        .route-stats-table tbody tr:nth-child(odd) { background:color-mix(in srgb,var(--primary-color) 3%,transparent); }
        .route-stats-table .route-id { font-weight:800; white-space:nowrap; }
        .route-stats-table .route-address { line-height:1.35; white-space:normal; overflow-wrap:anywhere; }
        .route-stats-table .route-datetime { white-space:normal; line-height:1.35; }
        .route-stats-table .num { text-align:right; white-space:normal; }

        /* Desktop: minden oszlop kifér a panelbe, nincs belső vízszintes csúszka. */
        .route-stats-table th:nth-child(1), .route-stats-table td:nth-child(1) { width:5%; }
        .route-stats-table th:nth-child(2), .route-stats-table td:nth-child(2) { width:10%; }
        .route-stats-table th:nth-child(3), .route-stats-table td:nth-child(3) { width:17%; }
        .route-stats-table th:nth-child(4), .route-stats-table td:nth-child(4) { width:10%; }
        .route-stats-table th:nth-child(5), .route-stats-table td:nth-child(5) { width:17%; }
        .route-stats-table th:nth-child(6), .route-stats-table td:nth-child(6) { width:8%; }
        .route-stats-table th:nth-child(7), .route-stats-table td:nth-child(7) { width:8%; }
        .route-stats-table th:nth-child(8), .route-stats-table td:nth-child(8) { width:8%; }
        .route-stats-table th:nth-child(9), .route-stats-table td:nth-child(9) { width:8%; }
        .route-stats-table th:nth-child(10), .route-stats-table td:nth-child(10) { width:9%; }

        .route-stats-empty { padding:16px; border:1px dashed var(--divider-color); border-radius:10px; text-align:center; color:var(--secondary-text-color); font-size:11px; }

        /* Telefonon/tableten inkább legyen valódi, érintéssel görgethető tábla. */
        @media (max-width:900px) {
          .route-export-actions { grid-template-columns:1fr; }
          .route-stats-scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }
          .route-stats-table { min-width:980px; table-layout:auto; }
          .route-stats-table th, .route-stats-table td { padding:8px 7px; }
          .route-stats-table .route-address { min-width:165px; }
          .route-stats-table .route-datetime { min-width:112px; }
          .route-stats-summary { align-items:flex-start; flex-direction:column; }
          .route-stats-summary-actions { width:100%; justify-content:space-between; }
          .route-print-button { white-space:normal; }
        }
        .pdf-library { overflow:hidden; }
        .pdf-library-title { display:flex; align-items:center; gap:8px; }
        .pdf-library-title b { margin-left:auto; font-size:10px; color:var(--secondary-text-color); }
        .pdf-library-note { margin:-3px 0 10px; color:var(--secondary-text-color); font-size:10px; overflow-wrap:anywhere; }
        .saved-pdf-list { display:grid; gap:7px; }
        .saved-pdf-row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 10px; border:1px solid var(--divider-color); border-radius:10px; background:var(--secondary-background-color); min-width:0; }
        .saved-pdf-main { display:flex; align-items:center; gap:9px; min-width:0; }
        .saved-pdf-main>ha-icon { color:#d32f2f; flex:0 0 auto; }
        .saved-pdf-main>div { min-width:0; }
        .saved-pdf-main b,.saved-pdf-main span { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .saved-pdf-main b { font-size:11px; }
        .saved-pdf-main span { color:var(--secondary-text-color); font-size:9.5px; margin-top:2px; }
        .saved-pdf-actions { display:flex; gap:6px; flex:0 0 auto; }
        .saved-pdf-actions button,.saved-pdf-actions a,.pdf-preview-head button { min-height:34px; padding:0 9px; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); display:inline-flex; align-items:center; justify-content:center; gap:5px; font:inherit; font-size:9.5px; font-weight:700; text-decoration:none; cursor:pointer; white-space:nowrap; }
        .saved-pdf-actions ha-icon,.pdf-preview-head ha-icon { --mdc-icon-size:16px; }
        .saved-pdf-empty { padding:14px; border:1px dashed var(--divider-color); border-radius:9px; color:var(--secondary-text-color); text-align:center; font-size:10px; }
        .pdf-preview-wrap { margin-top:12px; border:1px solid var(--divider-color); border-radius:10px; overflow:hidden; background:var(--card-background-color); }
        .pdf-preview-head { min-height:42px; padding:6px 9px; display:flex; align-items:center; justify-content:space-between; gap:10px; background:var(--secondary-background-color); }
        .pdf-preview-frame { width:100%; height:72vh; min-height:520px; border:0; display:block; background:#525659; }
        @media (max-width:900px) { .saved-pdf-row{align-items:flex-start;flex-direction:column}.saved-pdf-actions{width:100%;flex-wrap:wrap}.saved-pdf-actions button,.saved-pdf-actions a{flex:1 1 120px}.saved-pdf-main b,.saved-pdf-main span{white-space:normal;overflow-wrap:anywhere}.pdf-preview-frame{height:60vh;min-height:420px} }
        .night-separator { height:1px; background:var(--divider-color); margin:9px 0; }
        .decision-rule { margin:-2px 0 10px; padding:8px 9px; border-radius:9px; background:color-mix(in srgb,var(--primary-color) 10%,transparent); color:var(--primary-text-color); font-size:10px; font-weight:700; line-height:1.35; }
        .night-card { border-color:color-mix(in srgb,var(--primary-color) 35%,var(--divider-color)); }
        @media (max-width:1200px) { .stats{grid-template-columns:repeat(3,1fr)} .daily-metrics{grid-template-columns:repeat(3,1fr)} .manual-form{grid-template-columns:150px 1fr 1fr;}.manual-note{grid-column:1/-1}.manual-actions{grid-column:1/-1}.manual-save,.manual-remove{min-width:150px} }
        @media (max-width:760px) {
          .hero{align-items:flex-start;flex-direction:column;padding:20px}.hero-odo{width:100%;justify-content:space-between}.title{font-size:24px}.month-title-wrap{min-width:165px}.current-month{height:38px}.month-arrow{height:38px;min-width:38px}
          .content{padding:12px;gap:12px}.stats{grid-template-columns:repeat(2,1fr)}.stat{min-height:76px;padding:10px}.stat b{font-size:15px}
          .actions{grid-template-columns:1fr}.manual-form{grid-template-columns:1fr}.two-col{grid-template-columns:1fr}.today-grid{grid-template-columns:1fr}.daily-metrics{grid-template-columns:repeat(2,1fr)}
          .row-title{align-items:flex-start;flex-direction:column}.legend{flex-wrap:wrap}.panel,.action-panel{padding:13px}
          .detail-grid,.essential-grid{grid-template-columns:1fr}.detail-head{flex-direction:column}.detail-reason{max-width:none;text-align:left}.row-hint{display:block;margin:4px 0 0}.diagnostics-bar{align-items:flex-start;flex-direction:column}.compact-actions{grid-template-columns:1fr}
        }
              .dot.orange { background:var(--warn); }
</style>
    `;

    const interactiveControls = this.shadowRoot.querySelectorAll(
      "button, a, input, select, textarea"
    );
    interactiveControls.forEach((control) => {
      control.addEventListener("pointerenter", () => this._lockInteractive());
      control.addEventListener("pointerleave", () => this._unlockInteractiveDelayed());
      control.addEventListener("focusin", () => this._lockInteractive());
      control.addEventListener("focusout", () => this._unlockInteractiveDelayed());
      control.addEventListener("pointerdown", () => this._lockInteractive());
    });

    this.shadowRoot.querySelectorAll(".saved-pdf-preview").forEach((button) => {
      button.addEventListener("click", () => this._previewPdf(button.dataset.url));
    });
    this.shadowRoot.querySelectorAll(".saved-pdf-print").forEach((button) => {
      button.addEventListener("click", () => this._printPdf(button.dataset.url));
    });
    this.shadowRoot.getElementById("pdf-preview-close")?.addEventListener("click", () => this._closePdfPreview());

    this.shadowRoot.querySelectorAll(".row-toggle").forEach((button) => {
      button.addEventListener("click", () => this._toggleDate(button.dataset.date));
    });

    this.shadowRoot.querySelectorAll(".diagnostics-toggle").forEach((button) => {
      button.addEventListener("click", () => this._toggleDiagnostics(button.dataset.date));
    });

    this.shadowRoot.getElementById("month-prev")?.addEventListener("click", () =>
      this._selectMonth(previousMonth, entryId));
    this.shadowRoot.getElementById("month-next")?.addEventListener("click", () => {
      if (canGoNext) this._selectMonth(nextMonth, entryId);
    });
    this.shadowRoot.getElementById("month-current")?.addEventListener("click", () =>
      this._selectMonth(currentMonth, entryId));

    const actionPanel = this.shadowRoot.getElementById("action-panel");
    actionPanel?.addEventListener("pointerenter", () => {
      this._actionsHover = true;
    });
    actionPanel?.addEventListener("pointerleave", () => {
      this._actionsHover = false;
      if (this._pendingRender && !this._manualEditing) {
        this._pendingRender = false;
        this._render();
      }
    });
    actionPanel?.querySelectorAll("button.action").forEach((button) => {
      button.addEventListener("pointerdown", () => {
        // Keep the button DOM stable from mouse-down through click.
        this._actionsHover = true;
      });
    });

    const manualForm = this.shadowRoot.getElementById("manual-form");
    const manualDate = this.shadowRoot.getElementById("manual-date");
    const manualMorning = this.shadowRoot.getElementById("manual-morning");
    const manualEvening = this.shadowRoot.getElementById("manual-evening");
    const manualNote = this.shadowRoot.getElementById("manual-note");

    // Keep the whole manual form as one edit session. Moving from the date
    // field to a checkbox must NOT trigger a full dashboard re-render.
    manualForm?.addEventListener("focusin", () => this._beginManualEdit());
    manualForm?.addEventListener("pointerdown", () => this._beginManualEdit());

    manualForm?.addEventListener("focusout", (event) => {
      const next = event.relatedTarget;
      if (next && manualForm.contains(next)) {
        // Focus only moved to another field/button inside the same form.
        this._syncManualFormFromDom();
        return;
      }

      // Native date pickers can temporarily report no relatedTarget.
      // Delay the check and only finish editing if focus really left the form.
      window.setTimeout(() => {
        const active = this.shadowRoot?.activeElement;
        if (active && manualForm.contains(active)) return;
        this._finishManualEdit();
      }, 180);
    });

    manualDate?.addEventListener("click", () => {
      this._beginManualEdit();
      if (typeof manualDate.showPicker === "function") {
        try { manualDate.showPicker(); } catch (_) {}
      }
    });

    // Chrome can emit input before change for <input type="date">.
    // Store both so the selected date survives any later render.
    manualDate?.addEventListener("input", () => {
      if (manualDate.value) this._manualForm.date = manualDate.value;
    });
    manualDate?.addEventListener("change", () => {
      if (manualDate.value) this._manualForm.date = manualDate.value;
    });
    manualMorning?.addEventListener("change", () => {
      this._manualForm.morning = manualMorning.checked;
      this._syncManualFormFromDom();
    });
    manualEvening?.addEventListener("change", () => {
      this._manualForm.evening = manualEvening.checked;
      this._syncManualFormFromDom();
    });
    manualNote?.addEventListener("input", () => {
      this._manualForm.note = manualNote.value;
    });
    this.shadowRoot.getElementById("manual-save")?.addEventListener("click", () => this._saveManual(entryId));
    this.shadowRoot.getElementById("manual-remove")?.addEventListener("click", () => this._removeManual(entryId));

    this.shadowRoot.getElementById("refresh")?.addEventListener("click", () =>
      this._press(ids.refresh, "refresh", "A Kelio és a havi GPS adatok frissítése elkészült."));
    this.shadowRoot.getElementById("generate")?.addEventListener("click", () =>
      this._press(ids.pdfGenerate, "pdf", "A havi PDF elkészült. Most megnyithatod."));
    this.shadowRoot.getElementById("email")?.addEventListener("click", () =>
      this._press(ids.emailSend, "email", "A havi PDF e-mailben elküldve."));
    this.shadowRoot.getElementById("open")?.addEventListener("click", () => this._openPdf(pdfState, month));
  }
}

if (!customElements.get("utnyilvantartas-card")) {
  customElements.define("utnyilvantartas-card", UtnyilvantartasCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "utnyilvantartas-card",
  name: "Útnyilvántartás",
  description: "Kelio + Alapnyomkövetés havi útnyilvántartás, PDF generálással.",
  preview: true,
});
