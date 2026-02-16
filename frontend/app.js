const uploadArea = document.getElementById("uploadArea");
const fileInput = document.getElementById("fileInput");
const spinner = document.getElementById("spinner");
const errorMsg = document.getElementById("errorMsg");
const successBadge = document.getElementById("successBadge");
const resultCard = document.getElementById("resultCard");
const resDate = document.getElementById("resDate");
const resEur = document.getElementById("resEur");
const resBgn = document.getElementById("resBgn");
const resCategory = document.getElementById("resCategory");
const resPayment = document.getElementById("resPayment");
const resNotes = document.getElementById("resNotes");
const btnUndo = document.getElementById("btnUndo");
const btnNew = document.getElementById("btnNew");

let currentRow = null;

// Load categories and payment methods from backend
async function loadConfig() {
  try {
    const resp = await fetch("/api/config");
    const config = await resp.json();

    config.categories.forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      resCategory.appendChild(opt);
    });

    // Add empty option first for payment
    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "—";
    resPayment.appendChild(emptyOpt);

    config.payment_methods.forEach((pm) => {
      const opt = document.createElement("option");
      opt.value = pm;
      opt.textContent = pm;
      resPayment.appendChild(opt);
    });
  } catch (e) {
    console.error("Failed to load config:", e);
  }
}

function showError(msg) {
  errorMsg.textContent = msg;
  errorMsg.classList.add("active");
  setTimeout(() => errorMsg.classList.remove("active"), 5000);
}

function resetUI() {
  resultCard.classList.remove("active");
  successBadge.classList.remove("active");
  errorMsg.classList.remove("active");
  uploadArea.style.display = "";
  fileInput.value = "";
  currentRow = null;
}

async function uploadFile(file) {
  uploadArea.style.display = "none";
  spinner.classList.add("active");
  errorMsg.classList.remove("active");
  successBadge.classList.remove("active");
  resultCard.classList.remove("active");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Upload failed");
    }

    const result = await resp.json();
    currentRow = result.row;

    // Populate result card
    resDate.textContent = result.data.date;
    resEur.textContent = result.data.total_eur.toFixed(2);
    resBgn.textContent = result.data.total_bgn.toFixed(2);
    resCategory.value = result.data.category;
    resPayment.value = result.data.payment_method || "";
    resNotes.value = result.data.notes;

    successBadge.classList.add("active");
    resultCard.classList.add("active");
  } catch (e) {
    showError(e.message);
    uploadArea.style.display = "";
  } finally {
    spinner.classList.remove("active");
  }
}

// File input change
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

// Inline editing: category
resCategory.addEventListener("change", async () => {
  if (!currentRow) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ column: "Категория", value: resCategory.value }),
    });
  } catch (e) {
    showError("Failed to update category");
  }
});

// Inline editing: payment
resPayment.addEventListener("change", async () => {
  if (!currentRow) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ column: "Плащане", value: resPayment.value }),
    });
  } catch (e) {
    showError("Failed to update payment method");
  }
});

// Inline editing: notes (on blur)
resNotes.addEventListener("blur", async () => {
  if (!currentRow) return;
  try {
    await fetch(`/api/entry/${currentRow}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ column: "Пояснения", value: resNotes.value }),
    });
  } catch (e) {
    showError("Failed to update notes");
  }
});

// Undo
btnUndo.addEventListener("click", async () => {
  if (!currentRow) return;
  try {
    const resp = await fetch(`/api/entry/${currentRow}`, { method: "DELETE" });
    if (!resp.ok) throw new Error("Delete failed");
    resetUI();
  } catch (e) {
    showError("Failed to undo");
  }
});

// New receipt
btnNew.addEventListener("click", resetUI);

// Init
loadConfig();
