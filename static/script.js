"use strict";
const form = document.getElementById("reportForm");
const statusMsg = document.getElementById("statusMsg");
const historyPanel = document.getElementById("historyPanel");
const historyBody = document.getElementById("historyBody");
const historyEmpty = document.getElementById("historyEmpty");
const previewBody = document.getElementById("previewBody");
const previewStatus = document.getElementById("previewStatus");
const btnDownloadPreview = document.getElementById("btnDownloadPreview");
const months = ["มกราคม","กุมภาพันธ์","มีนาคม","เมษายน","พฤษภาคม","มิถุนายน","กรกฎาคม","สิงหาคม","กันยายน","ตุลาคม","พฤศจิกายน","ธันวาคม"];
let currentReportId = null, saving = false, baseline = "", historyItems = [];
let previewSequence = 0, previewController = null, previewUrl = null, previewSnapshot = "", previewTimer;
if (window.pdfjsLib) pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/worker.min.js";
const field = name => form.elements.namedItem(name);
function showStatus(message, error = false) {
  statusMsg.textContent = message;
  statusMsg.className = "status-msg " + (error ? "error" : "success");
  statusMsg.hidden = false;
}
function calcRemainingDays() {
  field("remainingDays").readOnly = field("remainingMode").value === "auto";
  const year = Number(field("reportYear").value)-543, month = months.indexOf(field("reportMonth").value), day = Number(field("reportDay").value);
  const start = Date.parse(field("contractStartDate").value + "T00:00:00Z");
  const date = new Date(Date.UTC(year, month, day));
  const valid = month >= 0 && day && date.getUTCDate() === day && date.getUTCMonth() === month && date.getUTCFullYear() === year;
  field("reportDay").setCustomValidity(day && month >= 0 && field("reportYear").value && !valid ? "วันที่นี้ไม่มีในปฏิทิน" : "");
  if (!field("remainingDays").readOnly) return;
  field("remainingDays").value = valid && Number.isFinite(start) && field("totalDays").value !== "" ? Number(field("totalDays").value) - Math.round((date.getTime()-start)/86400000) : "";
}
function updateCaptions() {
  document.querySelectorAll("[data-caption]").forEach(el => { el.textContent = field(el.dataset.caption).value; });
}
function collectFormData() {
  const extra = [];
  form.querySelectorAll(".extra-row").forEach(row => extra.push({group:Number(row.parentElement.dataset.group), label:row.querySelector("[data-label-input]").value, count:row.querySelector("[data-count-input]").value}));
  field("extraRows").value = JSON.stringify(extra);
  return Object.fromEntries(new FormData(form));
}
const snapshot = () => JSON.stringify(collectFormData());
const dirty = () => baseline !== snapshot();
function mayDiscard() { return !dirty() || confirm("มีข้อมูลที่ยังไม่บันทึก ต้องการดำเนินการต่อและทิ้งการแก้ไขหรือไม่?"); }
function setEditingState(id) {
  currentReportId = id;
  document.getElementById("btnSave").textContent = id ? "บันทึกการแก้ไข (#" + id + ")" : "บันทึกข้อมูล";
  document.getElementById("btnSaveAsNew").hidden = !id;
}
async function responseError(response) {
  try { const data = await response.json(); return data.error || "ดำเนินการไม่สำเร็จ"; } catch { return "ดำเนินการไม่สำเร็จ ("+response.status+")"; }
}
async function saveReport(forceNew = false) {
  if (saving || !form.reportValidity()) return;
  calcRemainingDays();
  const data = collectFormData(), submitted = JSON.stringify(data), id = forceNew ? null : currentReportId;
  saving = true;
  ["btnSave","btnSaveAsNew","btnClear"].forEach(key => document.getElementById(key).disabled = true);
  try {
    const response = await fetch(id ? "/api/reports/"+id : "/api/reports", {method:id ? "PUT" : "POST", headers:{"Content-Type":"application/json"}, body:submitted});
    if (!response.ok) throw new Error(await responseError(response));
    const result = await response.json();
    setEditingState(result.id); baseline = submitted;
    showStatus("บันทึกเอกสาร #"+result.id+" เรียบร้อยแล้ว");
    if (!historyPanel.hidden) await loadHistory();
  } catch (error) { showStatus(error.message,true); }
  finally { saving = false; ["btnSave","btnSaveAsNew","btnClear"].forEach(key => document.getElementById(key).disabled = false); }
}
function addExtra(group, label = "", count = "") {
  const container = document.querySelector('.extra-rows[data-group="'+group+'"]');
  const row = document.createElement("div"); row.className = "personnel-row extra-row";
  for (const [caption,type,value,marker] of [["ประเภท","text",label,"labelInput"],["จำนวน","number",count,"countInput"]]) {
    const wrapper = document.createElement("label"); wrapper.className = "field"; wrapper.textContent = caption;
    const input = document.createElement("input"); input.type = type; input.value = value; input.dataset[marker] = "";
    if (type === "number") {input.min = "0"; input.max = "9999999";} else input.maxLength = 300;
    wrapper.appendChild(input); row.appendChild(wrapper);
  }
  const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "ลบรายการ";
  button.addEventListener("click", () => { row.remove(); schedulePreview(); }); row.appendChild(button); container.appendChild(row);
}
function resetForm() {
  form.reset(); form.querySelectorAll(".extra-row").forEach(row => row.remove());
  field("reportDay").setCustomValidity(""); setEditingState(null); calcRemainingDays(); updateCaptions();
}
async function viewReport(id) {
  if (saving || !mayDiscard()) return;
  try {
    const response = await fetch("/api/reports/"+id);
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json(); resetForm();
    for (const [key,value] of Object.entries(data)) {
      const input = field(key); if (input && value !== null && key !== "extraRows") input.value = value;
    }
    for (const row of JSON.parse(data.extraRows || "[]")) addExtra(row.group,row.label,row.count);
    setEditingState(Number(id)); calcRemainingDays(); updateCaptions(); baseline = snapshot();
    setView("form"); window.scrollTo({top:0,behavior:"smooth"}); schedulePreview();
    showStatus("เปิดเอกสาร #"+id+" แล้ว แก้ไขและกดบันทึกการแก้ไขได้");
  } catch(error) { showStatus(error.message,true); }
}
async function deleteReport(id) {
  if (saving || !confirm("ต้องการลบเอกสาร #"+id+" หรือไม่?")) return;
  if (currentReportId === Number(id) && !mayDiscard()) return;
  try {
    const response = await fetch("/api/reports/"+id,{method:"DELETE"});
    if (!response.ok) throw new Error(await responseError(response));
    if (currentReportId === Number(id)) {resetForm(); baseline = snapshot(); schedulePreview();}
    await loadHistory(); showStatus("ลบเอกสาร #"+id+" แล้ว");
  } catch(error) {showStatus(error.message,true);}
}
function renderHistory() {
  const query = document.getElementById("historySearch").value.trim().toLocaleLowerCase();
  const month = document.getElementById("historyMonth").value, year = document.getElementById("historyYear").value;
  const items = historyItems.filter(item => (!month || item.reportMonth === month) && (!year || item.reportYear === year) && [item.projectTitle,item.workLocation,item.reportDay,item.reportMonth,item.reportYear].join(" ").toLocaleLowerCase().includes(query));
  historyBody.replaceChildren(); historyEmpty.hidden = !!items.length;
  for (const item of items) {
    const row = document.createElement("tr");
    for (const [label,value] of [["วันที่รายงาน",[item.reportDay,item.reportMonth,item.reportYear].filter(Boolean).join(" ")],["โครงการ / สถานที่",[item.projectTitle,item.workLocation].filter(Boolean).join(" / ")],["แก้ไขล่าสุด",item.updated_at || item.created_at]]) {
      const cell = document.createElement("td"); cell.dataset.label = label; cell.textContent = value || "-"; row.appendChild(cell);
    }
    const actions = document.createElement("td"); actions.className = "history-actions";
    for (const [label,action] of [["แก้ไข / ดูข้อมูล",()=>viewReport(item.id)],["PDF",()=>{window.location.href="/api/reports/"+item.id+"/pdf";}],["ลบ",()=>deleteReport(item.id)]]) {
      const button = document.createElement("button"); button.type="button"; button.textContent=label; button.addEventListener("click",action); actions.appendChild(button);
    }
    row.appendChild(actions); historyBody.appendChild(row);
  }
}
async function loadHistory() {
  try {
    const response = await fetch("/api/reports"); if (!response.ok) throw new Error(await responseError(response));
    historyItems = await response.json(); renderHistory();
  } catch(error) {showStatus(error.message,true);}
}
async function refreshPreview() {
  const sequence = ++previewSequence, current = snapshot();
  if (previewController) previewController.abort(); previewController = new AbortController();
  previewStatus.textContent = "กำลังอัปเดต...";
  let pdf;
  try {
    const response = await fetch("/api/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:current,signal:previewController.signal});
    if (!response.ok) throw new Error(await responseError(response));
    const blob = await response.blob();
    if (sequence !== previewSequence) return false;
    if (!window.pdfjsLib) throw new Error("โหลดโปรแกรมแสดง PDF ไม่สำเร็จ กรุณาโหลดหน้าใหม่");
    pdf = await pdfjsLib.getDocument({data:await blob.arrayBuffer()}).promise;
    const fragment = document.createDocumentFragment();
    for (let n=1;n<=pdf.numPages;n++) {
      if (sequence !== previewSequence) return false;
      const page = await pdf.getPage(n), viewport = page.getViewport({scale:1.3});
      const canvas = document.createElement("canvas"); canvas.width=viewport.width; canvas.height=viewport.height;
      canvas.setAttribute("aria-label","ตัวอย่างเอกสารหน้า "+n);
      await page.render({canvasContext:canvas.getContext("2d"),viewport}).promise; fragment.appendChild(canvas);
    }
    if (sequence !== previewSequence) return false;
    const nextUrl = URL.createObjectURL(blob);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl=nextUrl; previewSnapshot=current; previewBody.replaceChildren(fragment); previewStatus.textContent=pdf.numPages+" หน้า"; return true;
  } catch(error) {
    if (sequence === previewSequence && error.name !== "AbortError") previewStatus.textContent=error.message;
    return false;
  } finally {if (pdf) await pdf.destroy();}
}
function schedulePreview() {
  clearTimeout(previewTimer);
  // Invalidate work already rendering as soon as the form changes.
  previewSequence++; if (previewController) previewController.abort();
  previewStatus.textContent="ข้อมูลเปลี่ยนแปลง กำลังเตรียมตัวอย่าง...";
  previewTimer=setTimeout(refreshPreview,700);
}
function setView(view) {
  document.body.dataset.view=view;
  document.getElementById("tabForm").setAttribute("aria-pressed",String(view === "form"));
  document.getElementById("tabPreview").setAttribute("aria-pressed",String(view === "preview"));
}
form.addEventListener("submit",event=>{event.preventDefault();saveReport();});
form.addEventListener("input",()=>{calcRemainingDays();updateCaptions();schedulePreview();});
form.addEventListener("change",()=>{calcRemainingDays();updateCaptions();schedulePreview();});
form.querySelectorAll(".add-row").forEach(button=>button.addEventListener("click",()=>{if(form.querySelectorAll(".extra-row").length>=100){showStatus("เพิ่มได้สูงสุด 100 รายการ",true);return;}addExtra(Number(button.dataset.group));schedulePreview();}));
form.querySelectorAll(".clear-row").forEach(button=>button.addEventListener("click",()=>{button.closest(".base-row").querySelectorAll("input").forEach(input=>input.value="");schedulePreview();}));
document.getElementById("btnSave").addEventListener("click",()=>saveReport());
document.getElementById("btnSaveAsNew").addEventListener("click",()=>saveReport(true));
document.getElementById("btnClear").addEventListener("click",()=>{if(saving||!mayDiscard())return;resetForm();baseline=snapshot();schedulePreview();});
document.getElementById("btnToggleHistory").addEventListener("click",async()=>{historyPanel.hidden=!historyPanel.hidden;if(!historyPanel.hidden){await loadHistory();historyPanel.scrollIntoView({behavior:"smooth"});}});
for(const id of ["historySearch","historyMonth","historyYear"])document.getElementById(id).addEventListener("input",renderHistory);
document.getElementById("btnRefreshPreview").addEventListener("click",()=>{clearTimeout(previewTimer);refreshPreview();});
btnDownloadPreview.addEventListener("click",async()=>{
  btnDownloadPreview.disabled=true;clearTimeout(previewTimer);
  try {
    if(previewSnapshot!==snapshot() && !await refreshPreview())return;
    if(!previewUrl || previewSnapshot!==snapshot())return;
    const link=document.createElement("a");link.href=previewUrl;link.download="daily-report-preview.pdf";link.click();
  } finally {btnDownloadPreview.disabled=false;}
});
document.getElementById("tabForm").addEventListener("click",()=>setView("form"));
document.getElementById("tabPreview").addEventListener("click",()=>setView("preview"));
window.addEventListener("beforeunload",event=>{if(dirty()||saving){event.preventDefault();event.returnValue="";}});
calcRemainingDays(); updateCaptions(); baseline=snapshot(); refreshPreview();
