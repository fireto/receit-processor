const loginScreen = document.getElementById("loginScreen");
const pinInput = document.getElementById("pinInput");
const btnLogin = document.getElementById("btnLogin");
const loginError = document.getElementById("loginError");
const providerBar = document.getElementById("providerBar");
const providerSelect = document.getElementById("providerSelect");
const actionButtons = document.getElementById("actionButtons");
const uploadArea = document.getElementById("uploadArea");
const fileInput = document.getElementById("fileInput");
const btnManual = document.getElementById("btnManual");
const spinner = document.getElementById("spinner");
const errorMsg = document.getElementById("errorMsg");
const successBadge = document.getElementById("successBadge");
const resultCard = document.getElementById("resultCard");
const resDate = document.getElementById("resDate");
const resDateInput = document.getElementById("resDateInput");
const resAmountDisplay = document.getElementById("resAmountDisplay");
const resEur = document.getElementById("resEur");
const resBgn = document.getElementById("resBgn");
const resEurInput = document.getElementById("resEurInput");
const resCategory = document.getElementById("resCategory");
const resPayment = document.getElementById("resPayment");
const resNotes = document.getElementById("resNotes");
const resBulstatRow = document.getElementById("resBulstatRow");
const resBulstat = document.getElementById("resBulstat");
const qrBadge = document.getElementById("qrBadge");
const btnUndo = document.getElementById("btnUndo");
const btnNew = document.getElementById("btnNew");
const btnRowScan = document.getElementById("btnRowScan");
const btnRowManual = document.getElementById("btnRowManual");
const btnCancel = document.getElementById("btnCancel");
const btnSave = document.getElementById("btnSave");

let currentRow = null;
let manualMode = false;

// --- Auth ---

function getToken() {
  return localStorage.getItem("auth_token") || "";
}

function authHeaders() {
  return { Authorization: `Bearer ${getToken()}` };
}

async function tryLogin(pin) {
  const resp = await fetch("/api/config", {
    headers: { Authorization: `Bearer ${pin}` },
  });
  if (resp.status === 401) return false;
  if (!resp.ok) return false;
  localStorage.setItem("auth_token", pin);
  return true;
}

function showApp() {
  loginScreen.classList.add("hidden");
  providerBar.style.display = "";
  actionButtons.style.display = "";
  loadConfig();
}

async function checkAuth() {
  const token = getToken();
  if (!token) return;
  try {
    const resp = await fetch("/api/config", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.ok) {
      showApp();
    } else {
      localStorage.removeItem("auth_token");
    }
  } catch {
    // Network error — show login screen
  }
}

btnLogin.addEventListener("click", async () => {
  const pin = pinInput.value.trim();
  if (!pin) return;

  btnLogin.disabled = true;
  try {
    const ok = await tryLogin(pin);
    if (ok) {
      showApp();
    } else {
      loginError.textContent = "Wrong PIN";
      loginError.classList.add("active");
      setTimeout(() => loginError.classList.remove("active"), 3000);
    }
  } catch {
    loginError.textContent = "Connection error";
    loginError.classList.add("active");
  } finally {
    btnLogin.disabled = false;
  }
});

pinInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") btnLogin.click();
});

// --- Config ---

async function loadConfig() {
  try {
    const resp = await fetch("/api/config", { headers: authHeaders() });
    const config = await resp.json();

    if (config.version) {
      document.getElementById("appVersion").textContent = "v" + config.version;
    }

    resCategory.innerHTML = "";
    resPayment.innerHTML = "";

    config.categories.forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      resCategory.appendChild(opt);
    });

    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "\u2014";
    resPayment.appendChild(emptyOpt);

    config.payment_methods.forEach((pm) => {
      const opt = document.createElement("option");
      opt.value = pm;
      opt.textContent = pm;
      resPayment.appendChild(opt);
    });

    // Populate provider selector
    providerSelect.innerHTML = "";
    (config.providers || []).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      providerSelect.appendChild(opt);
    });
    providerSelect.value = config.default_provider || "claude";
  } catch (e) {
    console.error("Failed to load config:", e);
  }
}

// --- UI helpers ---

function showError(msg) {
  errorMsg.textContent = msg;
  errorMsg.classList.add("active");
  setTimeout(() => errorMsg.classList.remove("active"), 5000);
}

function setManualFields(show) {
  const display = show ? "" : "none";
  const displayInverse = show ? "none" : "";
  resDateInput.style.display = display;
  resEurInput.style.display = display;
  resDate.style.display = displayInverse;
  resAmountDisplay.style.display = displayInverse;
  btnRowManual.style.display = display;
  btnRowScan.style.display = displayInverse;
}

function resetUI() {
  manualMode = false;
  setManualFields(false);
  resultCard.classList.remove("active");
  successBadge.classList.remove("active");
  errorMsg.classList.remove("active");
  resBulstatRow.style.display = "none";
  qrBadge.style.display = "none";
  actionButtons.style.display = "";
  fileInput.value = "";
  currentRow = null;
}

// --- Upload ---

async function uploadFile(file) {
  actionButtons.style.display = "none";
  spinner.classList.add("active");
  errorMsg.classList.remove("active");
  successBadge.classList.remove("active");
  resultCard.classList.remove("active");

  const formData = new FormData();
  formData.append("file", file);
  formData.append("provider", providerSelect.value);

  try {
    const resp = await fetch("/api/upload", {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });

    if (resp.status === 401) {
      localStorage.removeItem("auth_token");
      location.reload();
      return;
    }

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Upload failed");
    }

    const result = await resp.json();
    currentRow = result.row;

    resDate.textContent = result.data.date;
    resEur.textContent = result.data.total_eur.toFixed(2);
    resBgn.textContent = result.data.total_bgn.toFixed(2);
    resCategory.value = result.data.category;
    resPayment.value = result.data.payment_method || "";
    resNotes.value = result.data.notes;

    // БУЛСТАТ
    if (result.data.bulstat) {
      resBulstat.textContent = result.data.bulstat;
      resBulstatRow.style.display = "";
    } else {
      resBulstatRow.style.display = "none";
    }

    // QR validation badge
    if (result.qr) {
      const qrMatch = result.qr.amount !== null &&
        Math.abs(result.qr.amount - result.data.total_eur) < 0.02;
      qrBadge.textContent = qrMatch ? "QR verified" : "QR mismatch";
      qrBadge.className = "qr-badge " + (qrMatch ? "qr-ok" : "qr-warn");
      qrBadge.style.display = "";
    } else {
      qrBadge.style.display = "none";
    }

    successBadge.classList.add("active");
    resultCard.classList.add("active");
  } catch (e) {
    showError(e.message);
    actionButtons.style.display = "";
  } finally {
    spinner.classList.remove("active");
  }
}

fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) uploadFile(file);
});

// Drag and drop
uploadArea.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadArea.classList.add("dragover");
});
uploadArea.addEventListener("dragleave", () => {
  uploadArea.classList.remove("dragover");
});
uploadArea.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadArea.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith("image/")) uploadFile(file);
});

// --- Manual entry ---

function todayString() {
  const d = new Date();
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `${dd}.${mm}.${d.getFullYear()}`;
}

btnManual.addEventListener("click", () => {
  manualMode = true;
  actionButtons.style.display = "none";
  errorMsg.classList.remove("active");
  successBadge.classList.remove("active");

  // Show manual fields, hide scan fields
  setManualFields(true);

  // Set defaults
  resDateInput.value = todayString();
  resEurInput.value = "";
  resCategory.value = "\u0420\u0430\u0437\u043d\u0438";  // Разни
  resPayment.value = "Cash";
  resNotes.value = "";
  resBulstatRow.style.display = "none";
  qrBadge.style.display = "none";

  resultCard.classList.add("active");

  // Focus on amount
  setTimeout(() => resEurInput.focus(), 100);
});

btnCancel.addEventListener("click", resetUI);

btnSave.addEventListener("click", async () => {
  const date = resDateInput.value.trim();
  const eurStr = resEurInput.value.trim();
  const totalEur = parseFloat(eurStr);

  if (!date || !eurStr || isNaN(totalEur) || totalEur <= 0) {
    showError("Please enter a valid date and amount");
    return;
  }

  btnSave.disabled = true;
  spinner.classList.add("active");

  try {
    const resp = await fetch("/api/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        date: date,
        total_eur: totalEur,
        category: resCategory.value,
        payment_method: resPayment.value || null,
        notes: resNotes.value,
      }),
    });

    if (resp.status === 401) {
      localStorage.removeItem("auth_token");
      location.reload();
      return;
    }

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Save failed");
    }

    const result = await resp.json();
    currentRow = result.row;
    manualMode = false;

    // Switch to display mode
    setManualFields(false);
    resDate.textContent = result.data.date;
    resEur.textContent = result.data.total_eur.toFixed(2);
    resBgn.textContent = result.data.total_bgn.toFixed(2);
    resCategory.value = result.data.category;
    resPayment.value = result.data.payment_method || "";
    resNotes.value = result.data.notes;

    successBadge.classList.add("active");
  } catch (e) {
    showError(e.message);
  } finally {
    btnSave.disabled = false;
    spinner.classList.remove("active");
  }
});

// --- Inline editing ---

resCategory.addEventListener("change", async () => {
  if (!currentRow || manualMode) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ column: "\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f", value: resCategory.value }),
    });
  } catch (e) {
    showError("Failed to update category");
  }
});

resPayment.addEventListener("change", async () => {
  if (!currentRow || manualMode) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ column: "\u041f\u043b\u0430\u0449\u0430\u043d\u0435", value: resPayment.value }),
    });
  } catch (e) {
    showError("Failed to update payment method");
  }
});

resNotes.addEventListener("blur", async () => {
  if (!currentRow || manualMode) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ column: "\u041f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u044f", value: resNotes.value }),
    });
  } catch (e) {
    showError("Failed to update notes");
  }
});

// --- Undo ---

btnUndo.addEventListener("click", async () => {
  if (!currentRow) return;
  try {
    const resp = await fetch(`/api/entry/${currentRow}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!resp.ok) throw new Error("Delete failed");
    resetUI();
  } catch (e) {
    showError("Failed to undo");
  }
});

btnNew.addEventListener("click", resetUI);

// --- Init ---
checkAuth();
