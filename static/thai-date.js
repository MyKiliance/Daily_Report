/* Thai Buddhist calendar; the submitted value remains a Gregorian ISO date. */
(() => {
  const form = document.getElementById('reportForm');
  const stored = form.elements.namedItem('contractStartDate');
  const input = document.getElementById('contractDateText');
  const trigger = document.getElementById('openContractCalendar');
  const dialog = document.getElementById('contractCalendar');
  const monthSelect = document.getElementById('calendarMonth');
  const yearSelect = document.getElementById('calendarYear');
  const days = document.getElementById('calendarDays');
  const months = ['มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน','กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม'];
  let cursor = new Date(), invalidText = false;
  const pad = value => String(value).padStart(2, '0');
  const iso = date => `${String(date.getFullYear()).padStart(4,'0')}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
  function parseStored() {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(stored.value);
    return match ? new Date(Number(match[1]), Number(match[2])-1, Number(match[3]), 12) : null;
  }
  function sync() {
    input.disabled = stored.disabled;
    trigger.disabled = stored.disabled;
    if(stored.disabled && dialog.open)dialog.close();
    if(invalidText && !stored.disabled)return;
    const date = parseStored();
    input.value = date ? `${pad(date.getDate())}/${pad(date.getMonth()+1)}/${date.getFullYear()+543}` : '';
    input.setCustomValidity('');
  }
  function notify() {
    stored.dispatchEvent(new Event('input', {bubbles:true}));
    stored.dispatchEvent(new Event('change', {bubbles:true}));
  }
  function selectDate(date) {
    if(stored.disabled)return;
    stored.value = date ? iso(date) : '';
    invalidText = false;
    sync(); notify(); dialog.close();
  }
  function render(focusDay = null) {
    const year = cursor.getFullYear(), month = cursor.getMonth();
    monthSelect.value = String(month);
    yearSelect.replaceChildren();
    for(let y=Math.max(100,Math.min(1900,year-50));y<=Math.min(9999,Math.max(2100,year+50));y++) {
      const option = document.createElement('option'); option.value=String(y); option.textContent=String(y+543);yearSelect.appendChild(option);
    }
    yearSelect.value=String(year);
    document.getElementById('calendarMonthLabel').textContent=months[month]+' '+(year+543);
    days.replaceChildren();
    const start = new Date(year,month,1).getDay();
    const count = new Date(year,month+1,0).getDate();
    for(let i=0;i<start;i++){const blank=document.createElement('span');blank.setAttribute('aria-hidden','true');days.appendChild(blank);}
    for(let day=1;day<=count;day++) {
      const date=new Date(year,month,day,12), button=document.createElement('button');
      button.type='button';button.textContent=String(day);button.dataset.day=String(day);
      button.setAttribute('aria-label',`${day} ${months[month]} ${year+543}`);
      button.setAttribute('aria-pressed',String(stored.value===iso(date)));
      if(iso(date)===iso(new Date()))button.classList.add('calendar-today');
      button.addEventListener('click',()=>selectDate(date));days.appendChild(button);
    }
    if(focusDay)days.querySelector(`[data-day="${Math.min(focusDay,count)}"]`)?.focus();
  }
  for(const [index,month] of months.entries()) {
    const option=document.createElement('option');option.value=String(index);option.textContent=month;monthSelect.appendChild(option);
  }
  trigger.addEventListener('click',()=>{
    if(stored.disabled)return;
    cursor=parseStored()||new Date();cursor=new Date(cursor.getFullYear(),cursor.getMonth(),1,12);
    render();dialog.showModal();
    const selected=parseStored();days.querySelector(`[data-day="${selected ? selected.getDate() : 1}"]`)?.focus();
  });
  document.getElementById('closeContractCalendar').addEventListener('click',()=>dialog.close());
  document.getElementById('calendarPrev').addEventListener('click',()=>{cursor.setMonth(cursor.getMonth()-1);render();});
  document.getElementById('calendarNext').addEventListener('click',()=>{cursor.setMonth(cursor.getMonth()+1);render();});
  monthSelect.addEventListener('change',()=>{cursor.setMonth(Number(monthSelect.value));render();});
  yearSelect.addEventListener('change',()=>{cursor.setFullYear(Number(yearSelect.value));render();});
  document.getElementById('calendarToday').addEventListener('click',()=>selectDate(new Date()));
  document.getElementById('calendarClear').addEventListener('click',()=>selectDate(null));
  days.addEventListener('keydown',event=>{
    if(!event.target.dataset.day)return;
    const move={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];
    if(move===undefined)return;
    event.preventDefault();const next=new Date(cursor.getFullYear(),cursor.getMonth(),Number(event.target.dataset.day)+move,12);
    cursor=new Date(next.getFullYear(),next.getMonth(),1,12);render(next.getDate());
  });
  dialog.addEventListener('close',()=>trigger.focus());
  input.addEventListener('input',()=>{
    if(stored.disabled)return;
    const value=input.value.trim().replace(/[๐-๙]/g,ch=>String(ch.charCodeAt(0)-0x0e50));
    if(!value){invalidText=false;stored.value='';input.setCustomValidity('');notify();return;}
    const match=/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/.exec(value);
    if(match){
      let year=Number(match[3]);year=match[3].length===2 ? 2500+year : year;
      const ce=year-543, month=Number(match[2])-1, day=Number(match[1]), date=new Date(ce,month,day,12);
      if(ce>=100 && ce<=9999 && date.getFullYear()===ce && date.getMonth()===month && date.getDate()===day){
        invalidText=false;input.setCustomValidity('');stored.value=iso(date);notify();return;
      }
    }
    invalidText=true;input.setCustomValidity('กรุณาระบุวันที่จริงในรูปแบบ วัน/เดือน/ปี พ.ศ. เช่น 14/05/2569');
  });
  input.addEventListener('blur',()=>{if(!invalidText)sync();});
  // Hydration/reset can change the ISO value without a DOM change event.
  form.addEventListener('reset',()=>{invalidText=false;setTimeout(sync,0);});
  window.contractDatePicker={sync,reset:()=>{invalidText=false;sync();}};
  sync();
})();
