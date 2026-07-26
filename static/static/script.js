const form = document.getElementById("reportForm");
const statusMsg = document.getElementById("statusMsg");
const historyPanel = document.getElementById("historyPanel");
const historyBody = document.getElementById("historyBody");
const historyEmpty = document.getElementById("historyEmpty");

const previewBody = document.getElementById("previewBody");
const previewStatus = document.getElementById("previewStatus");
const btnDownloadPreview = document.getElementById("btnDownloadPreview");

// ตั้งค่า worker ของ pdf.js ให้ชี้ไปที่ static/worker.min.js ที่แถมมาในโปรเจกต์
if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/worker.min.js";
}
let lastPreviewUrl = null;

function showStatus(message, type) {
  statusMsg.textContent = message;
  statusMsg.className = "status-msg " + type;
  statusMsg.hidden = false;
  setTimeout(() => { statusMsg.hidden = true; }, 4000);
}

function collectFormData() {
  const data = {};
  new FormData(form).forEach((value, key) => {
    data[key] = value;
  });
  return data;
}

async function saveReport() {
  const data = collectFormData();
  try {
    const res = await fetch("/api/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("บันทึกไม่สำเร็จ");
    const result = await res.json();
    showStatus("บันทึกรายงานเรียบร้อยแล้ว (รหัส #" + result.id + ")", "success");
    if (!historyPanel.hidden) await loadHistory();
    await refreshPreview();
  } catch (err) {
    showStatus("เกิดข้อผิดพลาด: " + err.message, "error");
  }
}

async function loadHistory() {
  try {
    const res = await fetch("/api/reports");
    if (!res.ok) throw new Error("โหลดข้อมูลไม่สำเร็จ");
    const items = await res.json();
    historyBody.innerHTML = "";
    historyEmpty.hidden = items.length > 0;
    items.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${item.reportDate || "-"}</td>
        <td>${item.workLocation || "-"}</td>
        <td>${item.created_at || "-"}</td>
        <td>
          <button type="button" data-action="view" data-id="${item.id}">ดูข้อมูล</button>
          <button type="button" data-action="pdf" data-id="${item.id}">ดาวน์โหลด PDF</button>
          <button type="button" class="danger" data-action="delete" data-id="${item.id}">ลบ</button>
        </td>
      `;
      historyBody.appendChild(tr);
    });
  } catch (err) {
    showStatus("เกิดข้อผิดพลาด: " + err.message, "error");
  }
}

async function viewReport(id) {
  try {
    const res = await fetch("/api/reports/" + id);
    if (!res.ok) throw new Error("ไม่พบรายงานนี้");
    const data = await res.json();
    Object.keys(data).forEach((key) => {
      const els = form.querySelectorAll(`[name="${key}"]`);
      els.forEach((el) => {
        if (el.type === "radio") {
          el.checked = el.value === data[key];
        } else {
          el.value = data[key] ?? "";
        }
      });
    });
    showStatus("โหลดข้อมูลรายงาน #" + id + " เข้าฟอร์มแล้ว", "success");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    showStatus("เกิดข้อผิดพลาด: " + err.message, "error");
  }
}

async function deleteReport(id) {
  if (!confirm("ต้องการลบรายงาน #" + id + " หรือไม่?")) return;
  try {
    const res = await fetch("/api/reports/" + id, { method: "DELETE" });
    if (!res.ok) throw new Error("ลบไม่สำเร็จ");
    showStatus("ลบรายงาน #" + id + " แล้ว", "success");
    await loadHistory();
  } catch (err) {
    showStatus("เกิดข้อผิดพลาด: " + err.message, "error");
  }
}

function downloadPdf(id) {
  window.location.href = "/api/reports/" + id + "/pdf";
}

async function renderPdfIntoPanel(pdfBlob) {
  if (lastPreviewUrl) {
    URL.revokeObjectURL(lastPreviewUrl);
  }
  lastPreviewUrl = URL.createObjectURL(pdfBlob);
  btnDownloadPreview.dataset.url = lastPreviewUrl;

  const arrayBuffer = await pdfBlob.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;

  previewBody.innerHTML = "";

  for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale: 1.3 });
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport }).promise;
    previewBody.appendChild(canvas);
  }
}

let previewRequestId = 0;
let hasRenderedOnce = false;

function setPreviewStatus(text, isError) {
  previewStatus.textContent = text || "";
  previewStatus.className = "preview-status" + (isError ? " error" : "");
}

async function refreshPreview(opts) {
  const auto = (opts && opts.auto) || false;
  const requestId = ++previewRequestId;

  // ครั้งแรกที่ยังไม่เคย render เลย ให้โชว์ข้อความโหลดตรงกลาง
  // ครั้งต่อๆ ไป (โดยเฉพาะตอนพิมพ์อัตโนมัติ) ให้คงตัวอย่างเดิมไว้ ไม่กระพริบ แค่ขึ้นสถานะเล็กๆ แทน
  if (!hasRenderedOnce) {
    previewBody.innerHTML = "";
    const loadingMsg = document.createElement("p");
    loadingMsg.className = "note";
    loadingMsg.textContent = "กำลังสร้างตัวอย่าง...";
    previewBody.appendChild(loadingMsg);
  } else {
    setPreviewStatus(auto ? "กำลังอัปเดตอัตโนมัติ..." : "กำลังอัปเดต...");
  }

  try {
    const data = collectFormData();
    const res = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("สร้างตัวอย่างไม่สำเร็จ");
    const blob = await res.blob();

    // ถ้ามีการพิมพ์ต่อจนเกิดคำขอใหม่ระหว่างรอ ให้ทิ้งผลลัพธ์เก่านี้ไป
    if (requestId !== previewRequestId) return;

    await renderPdfIntoPanel(blob);
    hasRenderedOnce = true;
    setPreviewStatus("");
  } catch (err) {
    if (requestId !== previewRequestId) return;
    if (!hasRenderedOnce) {
      previewBody.innerHTML = "";
      const errMsg = document.createElement("p");
      errMsg.className = "note";
      errMsg.textContent = "เกิดข้อผิดพลาด: " + err.message;
      previewBody.appendChild(errMsg);
    } else {
      setPreviewStatus("อัปเดตไม่สำเร็จ: " + err.message, true);
    }
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const autoRefreshPreview = debounce(() => refreshPreview({ auto: true }), 700);

// อัปเดตพรีวิวอัตโนมัติทุกครั้งที่พิมพ์/เลือกค่าในฟอร์ม (มี debounce กันยิงถี่เกินไป)
form.addEventListener("input", autoRefreshPreview);
form.addEventListener("change", autoRefreshPreview);

document.getElementById("btnRefreshPreview").addEventListener("click", () => refreshPreview());
btnDownloadPreview.addEventListener("click", () => {
  if (!btnDownloadPreview.dataset.url) {
    refreshPreview();
    return;
  }
  const a = document.createElement("a");
  a.href = btnDownloadPreview.dataset.url;
  a.download = "preview.pdf";
  document.body.appendChild(a);
  a.click();
  a.remove();
});

document.getElementById("btnSave").addEventListener("click", saveReport);

document.getElementById("btnClear").addEventListener("click", () => {
  if (confirm("ต้องการล้างข้อมูลทั้งหมดในแบบฟอร์มหรือไม่?")) {
    form.reset();
    refreshPreview();
  }
});

document.getElementById("btnToggleHistory").addEventListener("click", async () => {
  historyPanel.hidden = !historyPanel.hidden;
  if (!historyPanel.hidden) await loadHistory();
});

historyBody.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;
  if (action === "view") viewReport(id);
  else if (action === "delete") deleteReport(id);
  else if (action === "pdf") downloadPdf(id);
});

// สร้างตัวอย่าง PDF ครั้งแรกทันทีที่เปิดหน้า (จากค่าดีฟอลต์ในฟอร์ม)
refreshPreview();
