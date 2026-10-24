"use strict";
const form = document.getElementById("reportForm");
const statusMsg = document.getElementById("statusMsg");
const historyPanel = document.getElementById("historyPanel");
const historyBody = document.getElementById("historyBody");
const historyEmpty = document.getElementById("historyEmpty");
const previewBody = document.getElementById("previewBody");
const previewStatus = document.getElementById("previewStatus");
const btnDownloadPreview = document.getElementById("btnDownloadPreview");
const workflowConfig = JSON.parse(document.getElementById("workflowConfig").textContent);
const role = document.body.dataset.role;
const targetId = Number(document.body.dataset.reportId) || null;
const credentialKey = role === "issuer" ? "issuer-capability" : "filler-capability-"+targetId;
const linkToken = location.hash.slice(1);
const tokenStore = role === "issuer" ? localStorage : sessionStorage;
let credential = linkToken || sessionStorage.getItem(credentialKey) || tokenStore.getItem(credentialKey) || "";
if(!credential && role === "issuer"){
  const bytes=crypto.getRandomValues(new Uint8Array(32));
  credential="issuer_"+btoa(String.fromCharCode(...bytes)).replaceAll("+","-").replaceAll("/","_").replace(/=+$/,"");
}
if(credential){tokenStore.setItem(credentialKey,credential);sessionStorage.setItem(credentialKey,credential);}
if(linkToken)history.replaceState(null,"",location.pathname);
window.addEventListener("hashchange",()=>{
  if(location.hash.length>1){tokenStore.setItem(credentialKey,location.hash.slice(1));sessionStorage.setItem(credentialKey,location.hash.slice(1));history.replaceState(null,"",location.pathname);location.reload();}
});
let isHydrating=false;
let hasAccess=false, workflowVersion=null, workflowStatus="draft", allowedFields=[...workflowConfig.defaultDelegate];
const statusLabels={draft:"ร่าง",open:"เปิดให้กรอก",review:"ส่งกลับตรวจ",completed:"เสร็จสิ้น"};
async function apiFetch(url,options={}) {
  const headers=new Headers(options.headers || {});headers.set("Authorization","Bearer "+credential);
  return fetch(url,{...options,headers});
}
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
  if(isHydrating || (role === "issuer" && ["open","completed"].includes(workflowStatus)) || (role === "filler" && workflowStatus !== "open")) return;
  if(role === "filler" && !allowedFields.includes("remainingDays")) return;
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
  const data=Object.fromEntries([...form.querySelectorAll("[name]")].map(input=>[input.name,input.value]));
  if(role === "issuer")data.editable_fields=selectedPermissions();
  return data;
}
const snapshot = () => JSON.stringify(collectFormData());
const dirty = () => baseline !== snapshot();
function mayDiscard() { return !dirty() || confirm("มีข้อมูลที่ยังไม่บันทึก ต้องการดำเนินการต่อและทิ้งการแก้ไขหรือไม่?"); }
function setEditingState(id) {
  currentReportId = id;
  document.getElementById("btnSave").textContent = id ? "บันทึกการแก้ไข (#" + id + ")" : "บันทึกข้อมูล";
  document.getElementById("btnSaveAsNew").hidden = !id || role !== "issuer";
  applyWorkflow();
}
async function responseError(response) {
  try { const data = await response.json(); return data.error || "ดำเนินการไม่สำเร็จ"; } catch { return "ดำเนินการไม่สำเร็จ ("+response.status+")"; }
}
async function saveReport(forceNew = false) {
  if (saving || !hasAccess || !form.reportValidity()) return false;
  calcRemainingDays();
  const all = collectFormData(), submitted = JSON.stringify(all), id = forceNew ? null : currentReportId;
  const data = role === "issuer" ? {...all, editable_fields: selectedPermissions()} : Object.fromEntries(Object.entries(all).filter(([key])=>(allowedFields.includes(key)||key === "extraRows")));
  if(id)data.version=workflowVersion;
  saving = true;
  ["btnSave","btnSaveAsNew","btnClear"].forEach(key => document.getElementById(key).disabled = true);
  try {
    const response = await apiFetch(id ? "/api/reports/"+id : "/api/reports", {method:id ? "PUT" : "POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
    if (!response.ok) throw new Error(await responseError(response));
    const result = await response.json();
    workflowVersion=result.version;workflowStatus=result.status;allowedFields=role === "issuer" ? selectedPermissions() : allowedFields;
    setEditingState(result.id); baseline = submitted;
    showStatus("บันทึกเอกสาร #"+result.id+" เรียบร้อยแล้ว");
    if (!historyPanel.hidden && role === "issuer") await loadHistory();
    return true;
  } catch (error) { showStatus(error.message,true); return false; }
  finally { saving = false; ["btnSave","btnSaveAsNew","btnClear"].forEach(key => document.getElementById(key).disabled = false); applyWorkflow(); }
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
  button.addEventListener("click", () => { row.remove(); refreshExtraPermissions(); schedulePreview(); }); row.appendChild(button); container.appendChild(row);refreshExtraPermissions();
}
function resetForm() {
  form.reset(); window.contractDatePicker.reset();form.querySelectorAll(".extra-row").forEach(row => row.remove());
  workflowVersion=null;workflowStatus="draft";allowedFields=[...workflowConfig.defaultDelegate];
  setPermissions(allowedFields);
  field("reportDay").setCustomValidity(""); setEditingState(null); calcRemainingDays(); updateCaptions();
  document.getElementById("sharePanel").hidden=true;document.getElementById("eventPanel").hidden=true;
}
async function viewReport(id, initial=false) {
  if (saving || (!initial && !mayDiscard())) return;
  try {
    const response = await apiFetch("/api/reports/"+id);
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json(); isHydrating=true;resetForm();
    for (const [key,value] of Object.entries(data)) {
      const input = field(key); if (input && value !== null && key !== "extraRows") input.value = value;
    }
    for (const row of JSON.parse(data.extraRows || "[]")) addExtra(row.group,row.label,row.count);
    hasAccess=true;workflowVersion=data.version;workflowStatus=data.status;allowedFields=data.editable_fields;setPermissions(allowedFields);refreshExtraPermissions();
    isHydrating=false;setEditingState(Number(id)); calcRemainingDays(); updateCaptions(); baseline = snapshot();
    setView("form"); window.scrollTo({top:0,behavior:"smooth"}); schedulePreview();
    showStatus("เปิดเอกสาร #"+id+" แล้ว แก้ไขและกดบันทึกการแก้ไขได้");
  } catch(error) { showStatus(error.message,true); } finally {isHydrating=false;}
}
async function deleteReport(id) {
  if (saving || !confirm("ต้องการลบเอกสาร #"+id+" หรือไม่?")) return;
  if (currentReportId === Number(id) && !mayDiscard()) return;
  try {
    const item=historyItems.find(item=>item.id===Number(id));
    const response = await apiFetch("/api/reports/"+id,{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({version:currentReportId===Number(id)?workflowVersion:item.version})});
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
    for (const [label,value] of [["วันที่รายงาน",[item.reportDay,item.reportMonth,item.reportYear].filter(Boolean).join(" ")],["โครงการ / สถานที่",[item.projectTitle,item.workLocation,statusLabels[item.status]].filter(Boolean).join(" / ")],["แก้ไขล่าสุด",item.updated_at || item.created_at]]) {
      const cell = document.createElement("td"); cell.dataset.label = label; cell.textContent = value || "-"; row.appendChild(cell);
    }
    const actions = document.createElement("td"); actions.className = "history-actions";
    for (const [label,action] of [["แก้ไข / ดูข้อมูล",()=>viewReport(item.id)],["PDF",()=>downloadSaved(item.id)],["ลบ",()=>deleteReport(item.id)]]) {
      const button = document.createElement("button"); button.type="button"; button.textContent=label; button.addEventListener("click",action); actions.appendChild(button);
    }
    row.appendChild(actions); historyBody.appendChild(row);
  }
}
async function loadHistory() {
  try {
    const response = await apiFetch("/api/reports"); if (!response.ok) throw new Error(await responseError(response));
    historyItems = await response.json(); renderHistory();
  } catch(error) {showStatus(error.message,true);}
}
async function refreshPreview() {
  if(!hasAccess)return false;
  const sequence = ++previewSequence, current = snapshot();
  if (previewController) previewController.abort(); previewController = new AbortController();
  previewStatus.textContent = "กำลังอัปเดต...";
  let pdf;
  try {
    let data=role === "issuer" ? collectFormData() : Object.fromEntries(Object.entries(collectFormData()).filter(([key])=>(allowedFields.includes(key)||key === "extraRows")));
    if(role === "filler" && workflowStatus !== "open")data={};
    if(currentReportId){data.report_id=currentReportId;data.version=workflowVersion;}
    const response = await apiFetch("/api/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data),signal:previewController.signal});
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
installPermissions();calcRemainingDays();updateCaptions();baseline=snapshot();boot();

function selectedPermissions(){return [...form.querySelectorAll("[data-permission]:checked")].map(input=>input.dataset.permission);}
function setPermissions(keys){form.querySelectorAll("[data-permission]").forEach(input=>input.checked=keys.includes(input.dataset.permission));}
function refreshExtraPermissions(){
  form.querySelectorAll(".extra-row").forEach((row,index)=>{
    const key="extraCount:"+index;let toggle=row.querySelector("[data-permission]");
    if(!toggle){
      const countField=row.querySelector("[data-count-input]").closest(".field");
      const wrap=document.createElement("div");wrap.className="permission-wrap";countField.before(wrap);wrap.appendChild(countField);
      const label=document.createElement("label");label.className="permission-choice";toggle=document.createElement("input");toggle.type="checkbox";toggle.checked=allowedFields.includes(key);label.append(toggle,document.createTextNode(" อนุญาตให้กรอก"));wrap.appendChild(label);
    }
    toggle.dataset.permission=key;row.querySelector("[data-count-input]").dataset.extraKey=key;
    row.querySelector(".permission-choice").hidden=role!=="issuer";
  });
  applyWorkflow();
}
function installPermissions(){
  for(const key of workflowConfig.delegate){
    const input=field(key);if(!input || input.type === "hidden")continue;
    const original=input.closest(".field");if(!original)continue;
    const wrap=document.createElement("div");wrap.className="permission-wrap";original.before(wrap);wrap.appendChild(original);
    const label=document.createElement("label");label.className="permission-choice";
    const toggle=document.createElement("input");toggle.type="checkbox";toggle.dataset.permission=key;toggle.checked=allowedFields.includes(key);
    label.append(toggle,document.createTextNode(" อนุญาตให้กรอก"));wrap.appendChild(label);
  }
}
function applyWorkflow(){
  const editable=hasAccess && (role === "issuer" ? ["draft","review"].includes(workflowStatus) : workflowStatus === "open");
  document.querySelector(".app-layout").classList.toggle("authorized",hasAccess);
  document.getElementById("roleLabel").textContent=role === "issuer" ? "ผู้ออกเอกสาร" : "ผู้กรอกข้อมูล (ผู้ว่าจ้าง)";
  document.getElementById("workflowStatus").textContent=hasAccess ? "เอกสาร "+(currentReportId?"#"+currentReportId+" · ":"")+statusLabels[workflowStatus]+(workflowVersion?" · รุ่น "+workflowVersion:"") : "ลิงก์ไม่ถูกต้องหรือถูกยกเลิก กรุณาขอลิงก์ใหม่จากผู้ออกเอกสาร";
  for(const input of form.querySelectorAll("[name]"))input.disabled=!editable || (role === "filler"&&!allowedFields.includes(input.name));
  for(const key of ["reportDay","reportMonth","reportYear"])field(key).required=false;
  form.querySelectorAll("[data-permission]").forEach(input=>input.disabled=role!=="issuer"||!editable);
  form.querySelectorAll(".permission-choice,.add-row,.clear-row").forEach(el=>el.hidden=role!=="issuer");
  form.querySelectorAll(".extra-row button").forEach(el=>el.hidden=role!=="issuer");
  form.querySelectorAll(".add-row,.clear-row,.extra-row button").forEach(el=>el.disabled=!editable);
  form.querySelectorAll(".extra-row [data-label-input]").forEach(input=>input.disabled=!editable || role!=="issuer");
  form.querySelectorAll(".extra-row [data-count-input]").forEach(input=>input.disabled=!editable || (role!=="issuer"&&!allowedFields.includes(input.dataset.extraKey)));
  document.getElementById("btnSave").disabled=!editable||saving;
  document.getElementById("btnSaveAsNew").hidden=role!=="issuer"||!currentReportId||!editable;
  document.getElementById("btnClear").hidden=role!=="issuer";document.getElementById("btnToggleHistory").hidden=role!=="issuer";
  const visible={btnIssuerLink:role==="issuer",btnIssue:role==="issuer"&&["draft","review"].includes(workflowStatus),btnSubmit:role==="filler"&&workflowStatus==="open",btnComplete:role==="issuer"&&workflowStatus==="review",btnReopen:role==="issuer"&&["review","completed"].includes(workflowStatus),btnEditDraft:role==="issuer"&&["open","completed"].includes(workflowStatus),btnRotate:role==="issuer"&&!!currentReportId&&workflowStatus!=="draft",btnRevoke:role==="issuer"&&!!currentReportId,btnReloadDocument:!!currentReportId,btnEvents:role==="issuer"&&!!currentReportId};
  for(const [id,show] of Object.entries(visible))document.getElementById(id).hidden=!show;
  if(role==="filler"){form.querySelector("details").hidden=true;document.getElementById("formHelp").textContent=workflowStatus==="open" ? "กรอกเฉพาะช่องที่เปิดไว้ บันทึกระหว่างทำได้ เมื่อพร้อมให้กดส่งกลับตรวจ" : "เอกสารปิดรับการกรอกแล้ว สามารถดูตัวอย่างและดาวน์โหลดได้";}
  window.contractDatePicker.sync();
  calcRemainingDays();
}
async function workflowAction(action){
  if(saving)return;
  if(action==="submit"){
    for(const key of ["reportDay","reportMonth","reportYear"])field(key).required=true;
    const valid=form.reportValidity();
    for(const key of ["reportDay","reportMonth","reportYear"])field(key).required=false;
    if(!valid)return;
  }
  if((dirty()||!currentReportId)&&!await saveReport())return;
  saving=true;applyWorkflow();
  try{
    const response=await apiFetch("/api/reports/"+currentReportId+"/workflow",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,version:workflowVersion})});
    if(!response.ok)throw new Error(await responseError(response));
    const result=await response.json();workflowVersion=result.version;workflowStatus=result.status;
    document.getElementById("sharePanel").hidden=!result.filler_path;
    if(result.filler_path)document.getElementById("fillerLink").value=new URL(result.filler_path,location.origin).href;
    showStatus("ดำเนินการแล้ว: "+statusLabels[workflowStatus]);schedulePreview();
    if(role==="issuer"&&!historyPanel.hidden)await loadHistory();
  }catch(error){showStatus(error.message,true);}
  finally{saving=false;applyWorkflow();}
}
async function downloadSaved(id){
  try{const response=await apiFetch("/api/reports/"+id+"/pdf");if(!response.ok)throw new Error(await responseError(response));const url=URL.createObjectURL(await response.blob());const link=document.createElement("a");link.href=url;link.download="daily-report-"+id+".pdf";link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(error){showStatus(error.message,true);}
}
async function boot(){
  if(!credential){applyWorkflow();document.getElementById("accessNotice").hidden=false;return;}
  if(role==="filler"){await viewReport(targetId,true);if(!hasAccess)document.getElementById("accessNotice").hidden=false;}
  else{try{const response=await apiFetch("/api/session");if(!response.ok)throw new Error(await responseError(response));hasAccess=true;applyWorkflow();baseline=snapshot();refreshPreview();}catch(error){applyWorkflow();document.getElementById("accessNotice").hidden=false;}}
}
for(const [id,action] of Object.entries({btnIssue:"issue",btnSubmit:"submit",btnComplete:"complete",btnReopen:"reopen",btnEditDraft:"edit",btnRotate:"rotate",btnRevoke:"revoke"}))document.getElementById(id).addEventListener("click",()=>workflowAction(action));
document.getElementById("btnCopyLink").addEventListener("click",async()=>{const input=document.getElementById("fillerLink");try{await navigator.clipboard.writeText(input.value);showStatus("คัดลอกลิงก์ผู้กรอกแล้ว");}catch{input.select();showStatus("เลือกลิงก์แล้ว กดคัดลอกได้เลย");}});
document.getElementById("btnEvents").addEventListener("click",async()=>{
  const panel=document.getElementById("eventPanel");panel.replaceChildren();
  try{const response=await apiFetch("/api/reports/"+currentReportId+"/events");if(!response.ok)throw new Error(await responseError(response));for(const event of await response.json()){const entry=document.createElement("p");entry.textContent=event.created_at+" · "+(event.actor==="issuer"?"ผู้ออกเอกสาร":"ผู้กรอก")+" · "+({created:"สร้างเอกสาร",saved:"บันทึกข้อมูล",issue:"ออกเอกสาร",submit:"ส่งกลับตรวจ",complete:"ยืนยันเสร็จสิ้น",reopen:"เปิดให้กรอกใหม่",edit:"กลับไปแก้รายละเอียด",rotate:"สร้างลิงก์ใหม่",revoke:"ยกเลิกลิงก์"}[event.action]||event.action);panel.appendChild(entry);}panel.hidden=false;}catch(error){showStatus(error.message,true);}
});

document.getElementById("btnReloadDocument").addEventListener("click",()=>viewReport(currentReportId));

document.getElementById("btnIssuerLink").addEventListener("click",async()=>{
  const url=new URL("/manage",location.origin);url.hash=credential;
  const input=document.getElementById("issuerLinkValue");input.value=url.href;document.getElementById("issuerLinkPanel").hidden=false;
  try{await navigator.clipboard.writeText(url.href);showStatus("คัดลอกลิงก์จัดการส่วนตัวแล้ว เก็บไว้ใช้เอง ไม่ส่งให้ผู้กรอก");}catch{input.select();showStatus("เลือกลิงก์ส่วนตัวแล้ว กดคัดลอกเพื่อเก็บไว้ได้");}
});
