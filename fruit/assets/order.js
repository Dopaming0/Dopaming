/* ─────────────────────────────────────────────
   발주서 변환 — 쿠팡 발주서를 도매처별 발주서로
   모든 처리는 브라우저 안에서만 이루어진다 (서버 전송 없음)
   ───────────────────────────────────────────── */
"use strict";

const LS_VENDOR_MAP = "gwail.vendorMap"; // { 옵션명: 도매처명 }

/* 열 자동 인식 규칙 — 위에서부터 먼저 맞는 열을 쓴다 */
const FIELDS = [
  { key: "name",    label: "받는 분",    required: true,
    rules: [
      { re: /수취인이?름/ }, { re: /수취인명/ },
      { re: /받는(분|사람).*(성명|이름)?/ }, { re: /수령인/ },
    ] },
  { key: "phone",   label: "전화번호",   required: false,
    rules: [
      { re: /수취인.*(전화|연락|휴대)/ },
      { re: /받는분.*(전화|연락)/ },
      { re: /전화번호|연락처|휴대폰/, not: /구매자|주문자/ },
    ] },
  { key: "zip",     label: "우편번호",   required: false,
    rules: [{ re: /우편번호/ }] },
  { key: "addr",    label: "주소",       required: true,
    rules: [
      { re: /수취인.*주소/ }, { re: /받는(분|사람).*주소/ },
      { re: /주소/, not: /구매자|주문자|이메일/ },
    ] },
  { key: "option",  label: "옵션명",     required: false,
    rules: [
      { re: /^등록옵션명$/ }, { re: /노출상품명.*옵션/ },
      { re: /옵션명/, not: /옵션id/i },
    ] },
  { key: "qty",     label: "수량",       required: false,
    rules: [{ re: /구매수/ }, { re: /^수량$/ }, { re: /수량/, not: /취소/ }] },
  { key: "msg",     label: "배송메세지", required: false,
    rules: [{ re: /배송메[세시]지/ }, { re: /(배송)?요청사항/ }] },
  { key: "orderNo", label: "주문번호",   required: false,
    rules: [{ re: /^주문번호$/ }, { re: /주문번호/, not: /묶음/ }] },
];

const OUT_HEADER = [
  "받는분 성명", "받는분 전화번호", "우편번호", "받는분 주소",
  "상품명(옵션)", "수량", "배송메세지", "쿠팡 주문번호",
];
const UNASSIGNED = "__미지정__";

const state = {
  fileName: "",
  headers: [],      // 헤더 행의 셀 텍스트
  dataRows: [],     // 헤더 아래의 원본 행(aoa)
  mapping: {},      // field key → 열 index (-1 = 없음)
  records: [],      // 정리된 주문 레코드
  options: [],      // [{ name, qty, count }]
  assignments: {},  // 옵션명 → 도매처명
  downloaded: new Set(),
};

const $ = (id) => document.getElementById(id);
const norm = (s) => String(s ?? "").replace(/\s+/g, "").toLowerCase();

/* ══ 1단계: 파일 읽기 ══ */

function initUpload() {
  const dz = $("dropzone");
  const input = $("fileInput");

  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  ["dragover", "dragenter"].forEach((t) =>
    dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((t) =>
    dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("dragover"); }));
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) readFile(f);
  });
  input.addEventListener("change", () => {
    if (input.files[0]) readFile(input.files[0]);
    input.value = "";
  });
  $("resetBtn").addEventListener("click", resetAll);
  $("sampleBtn").addEventListener("click", loadSample);
}

function readFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const wb = XLSX.read(new Uint8Array(e.target.result), { type: "array" });
      loadWorkbook(wb, file.name);
    } catch (err) {
      showParseError("파일을 읽지 못했어요. 쿠팡윙에서 받은 엑셀 파일이 맞는지 확인해주세요.");
    }
  };
  reader.readAsArrayBuffer(file);
}

function loadWorkbook(wb, fileName) {
  const ws = wb.Sheets[wb.SheetNames[0]];
  const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });

  const headerIdx = findHeaderRow(aoa);
  if (headerIdx < 0) {
    showParseError("주문 목록을 찾지 못했어요. 수취인·주소 열이 있는 발주서인지 확인해주세요.");
    return;
  }

  state.fileName = fileName;
  state.headers = aoa[headerIdx].map((c) => String(c ?? "").trim());
  state.dataRows = aoa.slice(headerIdx + 1);
  state.mapping = autoMap(state.headers);
  state.downloaded = new Set();
  state.assignments = loadVendorMap();

  $("parseError").classList.add("hidden");
  $("dropzone").classList.add("hidden");
  $("fileDone").classList.remove("hidden");
  $("fileName").textContent = fileName;
  $("sampleBtn").parentElement.classList.add("hidden");

  rebuild();
}

/* 헤더 행 탐색: 앞쪽 15행 중 인식되는 열이 가장 많은 행 */
function findHeaderRow(aoa) {
  let best = -1, bestScore = 0;
  const limit = Math.min(aoa.length, 15);
  for (let i = 0; i < limit; i++) {
    const score = FIELDS.filter((f) => pickColumn(aoa[i], f) >= 0).length;
    if (score > bestScore) { bestScore = score; best = i; }
  }
  return bestScore >= 3 ? best : -1;
}

function pickColumn(row, field) {
  for (const rule of field.rules) {
    for (let c = 0; c < row.length; c++) {
      const h = norm(row[c]);
      if (!h) continue;
      if (rule.re.test(h) && !(rule.not && rule.not.test(h))) return c;
    }
  }
  return -1;
}

function autoMap(headers) {
  const map = {};
  for (const f of FIELDS) map[f.key] = pickColumn(headers, f);
  return map;
}

/* ══ 매핑 → 레코드 ══ */

function rebuild() {
  buildRecords();
  renderSummary();
  renderMapping();

  const missing = FIELDS.filter((f) => f.required && state.mapping[f.key] < 0);
  if (missing.length || !state.records.length) {
    $("mappingBox").open = true;
    if (!missing.length && !state.records.length) {
      showParseError("주문이 한 건도 없어요. 파일을 다시 확인해주세요.");
    }
    $("step2").classList.add("hidden");
    $("step3").classList.add("hidden");
    return;
  }
  $("parseError").classList.add("hidden");
  renderOptions();
  renderDownloads();
  $("step2").classList.remove("hidden");
  $("step3").classList.remove("hidden");
}

function buildRecords() {
  const m = state.mapping;
  const cell = (row, key) => (m[key] >= 0 ? String(row[m[key]] ?? "").trim() : "");
  state.records = state.dataRows
    .map((row) => ({
      name: cell(row, "name"),
      phone: cell(row, "phone"),
      zip: cell(row, "zip"),
      addr: cell(row, "addr"),
      option: cell(row, "option") || "(옵션 없음)",
      qty: Math.max(1, parseInt(cell(row, "qty"), 10) || 1),
      msg: cell(row, "msg"),
      orderNo: cell(row, "orderNo"),
    }))
    .filter((r) => r.name && r.addr);

  const byOption = new Map();
  for (const r of state.records) {
    const o = byOption.get(r.option) || { name: r.option, qty: 0, count: 0 };
    o.qty += r.qty;
    o.count += 1;
    byOption.set(r.option, o);
  }
  state.options = [...byOption.values()].sort((a, b) => b.qty - a.qty);
}

/* ══ 화면 그리기 ══ */

function renderSummary() {
  const totalQty = state.records.reduce((s, r) => s + r.qty, 0);
  $("chips").innerHTML = `
    <span class="chip">주문 <b>${state.records.length}건</b></span>
    <span class="chip">옵션 <b>${state.options.length}종</b></span>
    <span class="chip">총 <b>${totalQty}개</b></span>`;
  $("summaryArea").classList.remove("hidden");
}

function renderMapping() {
  const grid = $("mapGrid");
  grid.innerHTML = "";
  for (const f of FIELDS) {
    const label = document.createElement("label");
    label.textContent = f.label + (f.required ? " *" : "");

    const sel = document.createElement("select");
    sel.appendChild(new Option("(없음)", "-1"));
    state.headers.forEach((h, i) => {
      if (String(h).trim()) sel.appendChild(new Option(h, String(i)));
    });
    sel.value = String(state.mapping[f.key]);
    if (f.required && state.mapping[f.key] < 0) sel.classList.add("missing");
    sel.addEventListener("change", () => {
      state.mapping[f.key] = parseInt(sel.value, 10);
      rebuild();
    });
    grid.appendChild(label);
    grid.appendChild(sel);
  }
}

function renderOptions() {
  const list = $("optionList");
  list.innerHTML = "";

  const dl = document.createElement("datalist");
  dl.id = "vendorNames";
  [...new Set(Object.values(state.assignments).filter(Boolean))]
    .forEach((v) => dl.appendChild(new Option(v)));
  list.appendChild(dl);

  for (const o of state.options) {
    const row = document.createElement("div");
    row.className = "option-row";

    const body = document.createElement("div");
    body.className = "o-body";
    body.innerHTML = `<div class="o-name"></div>
      <div class="o-count">${o.count}건 · ${o.qty}개</div>`;
    body.querySelector(".o-name").textContent = o.name;

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "도매처 이름";
    input.setAttribute("list", "vendorNames");
    input.value = state.assignments[o.name] || "";
    input.classList.toggle("filled", !!input.value);
    input.addEventListener("input", () => {
      const v = input.value.trim();
      if (v) state.assignments[o.name] = v;
      else delete state.assignments[o.name];
      input.classList.toggle("filled", !!v);
      saveVendorMap();
      renderDownloads();
    });

    row.appendChild(body);
    row.appendChild(input);
    list.appendChild(row);
  }
}

function groupByVendor() {
  const groups = new Map(); // 도매처명 → records
  for (const r of state.records) {
    const vendor = state.assignments[r.option] || UNASSIGNED;
    if (!groups.has(vendor)) groups.set(vendor, []);
    groups.get(vendor).push(r);
  }
  return groups;
}

function renderDownloads() {
  const groups = groupByVendor();
  const list = $("dlList");
  list.innerHTML = "";

  const vendors = [...groups.keys()].sort((a, b) =>
    a === UNASSIGNED ? 1 : b === UNASSIGNED ? -1 : a.localeCompare(b, "ko"));
  const onlyUnassigned = vendors.length === 1 && vendors[0] === UNASSIGNED;

  const notice = $("unassignedNotice");
  if (!onlyUnassigned && groups.has(UNASSIGNED)) {
    const n = groups.get(UNASSIGNED).length;
    notice.innerHTML = `<span>⚠️</span><span>도매처를 안 적은 주문이 <b>${n}건</b> 있어요. 2단계에서 마저 적어주세요.</span>`;
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }

  for (const vendor of vendors) {
    const records = groups.get(vendor);
    const qty = records.reduce((s, r) => s + r.qty, 0);
    const isUn = vendor === UNASSIGNED;

    const btn = document.createElement("button");
    btn.className = "dl-row";
    btn.innerHTML = `
      <span class="d-icon">${isUn ? "❓" : "🏬"}</span>
      <span class="d-body">
        <span class="d-name"></span>
        <div class="d-sub">${records.length}건 · ${qty}개</div>
      </span>
      <span class="d-action">받기</span>`;
    btn.querySelector(".d-name").textContent =
      isUn ? (onlyUnassigned ? "발주서 전체" : "도매처 미지정 주문") : vendor;
    if (state.downloaded.has(vendor)) {
      btn.classList.add("done");
      btn.querySelector(".d-action").textContent = "받았어요 ✓";
    }
    btn.addEventListener("click", () => {
      downloadVendor(vendor, records);
      state.downloaded.add(vendor);
      renderDownloads();
    });
    list.appendChild(btn);
  }

  const allBtn = $("dlAllBtn");
  if (vendors.length >= 2) {
    allBtn.classList.remove("hidden");
    allBtn.onclick = () => downloadAll(groups, vendors);
  } else {
    allBtn.classList.add("hidden");
  }
}

/* ══ 엑셀 만들기 ══ */

function recordsToSheet(records) {
  const aoa = [OUT_HEADER, ...records.map((r) => [
    r.name, r.phone, r.zip, r.addr, r.option, r.qty, r.msg, r.orderNo,
  ])];
  const ws = XLSX.utils.aoa_to_sheet(aoa);
  ws["!cols"] = [
    { wch: 10 }, { wch: 15 }, { wch: 8 }, { wch: 44 },
    { wch: 34 }, { wch: 6 }, { wch: 22 }, { wch: 18 },
  ];
  return ws;
}

function todayTag() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

function safeName(s) {
  return s.replace(/[\\/:*?"<>|\[\]]/g, "_").slice(0, 30) || "발주서";
}

function downloadVendor(vendor, records) {
  const label = vendor === UNASSIGNED ? "발주서" : vendor;
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, recordsToSheet(records), "발주서");
  XLSX.writeFile(wb, `${safeName(label)}_발주서_${todayTag()}.xlsx`);
}

function downloadAll(groups, vendors) {
  const wb = XLSX.utils.book_new();
  for (const vendor of vendors) {
    const label = vendor === UNASSIGNED ? "미지정" : safeName(vendor);
    XLSX.utils.book_append_sheet(wb, recordsToSheet(groups.get(vendor)), label.slice(0, 31));
  }
  XLSX.writeFile(wb, `도매처별_발주서_${todayTag()}.xlsx`);
  vendors.forEach((v) => state.downloaded.add(v));
  renderDownloads();
}

/* ══ 기타 ══ */

function showParseError(msg) {
  const el = $("parseError");
  el.innerHTML = `<span>😢</span><span></span>`;
  el.lastElementChild.textContent = msg;
  el.classList.remove("hidden");
}

function resetAll() {
  state.fileName = "";
  state.headers = [];
  state.dataRows = [];
  state.records = [];
  state.options = [];
  state.downloaded = new Set();
  $("dropzone").classList.remove("hidden");
  $("fileDone").classList.add("hidden");
  $("sampleBtn").parentElement.classList.remove("hidden");
  $("summaryArea").classList.add("hidden");
  $("parseError").classList.add("hidden");
  $("step2").classList.add("hidden");
  $("step3").classList.add("hidden");
}

function loadVendorMap() {
  try { return JSON.parse(localStorage.getItem(LS_VENDOR_MAP)) || {}; }
  catch { return {}; }
}

function saveVendorMap() {
  try { localStorage.setItem(LS_VENDOR_MAP, JSON.stringify(state.assignments)); }
  catch { /* 시크릿 모드 등 저장 불가 환경은 조용히 무시 */ }
}

/* ══ 샘플 데이터 ══ */

function loadSample() {
  const header = [
    "번호", "묶음배송번호", "주문번호", "택배사", "운송장번호",
    "주문일", "등록상품명", "등록옵션명", "노출상품명(옵션명)",
    "구매수(수량)", "옵션판매가(판매단가)", "구매자", "구매자전화번호",
    "수취인이름", "수취인전화번호", "우편번호", "수취인 주소", "배송메세지",
  ];
  const opts = [
    ["성주 꿀참외", "성주 꿀참외 4.5kg 가정용(11~14과)", 19900],
    ["성주 꿀참외", "성주 꿀참외 2kg 선물세트(5~7과)", 15900],
    ["하우스 수박", "하우스 수박 7kg 내외 1통", 23900],
    ["머스크 멜론", "머스크 멜론 2과 선물세트(3.2kg 내외)", 29900],
  ];
  const people = [
    ["김민준", "010-1234-5678", "06236", "서울 강남구 테헤란로 123, 101동 1203호", "부재 시 문 앞에 놓아주세요"],
    ["이서연", "010-2345-6789", "48058", "부산 해운대구 마린시티2로 33, 2504호", ""],
    ["박지훈", "010-3456-7890", "16509", "경기 수원시 영통구 광교중앙로 145", "경비실에 맡겨주세요"],
    ["최수아", "010-4567-8901", "61949", "광주 서구 상무중앙로 84, 5층", ""],
    ["정도윤", "010-5678-9012", "34126", "대전 유성구 대학로 99", "배송 전 연락주세요"],
    ["강하은", "010-6789-0123", "41911", "대구 중구 동성로 12-1", ""],
    ["조은우", "010-7890-1234", "63122", "제주 제주시 연동 273-1, 302호", "문 앞에 부탁드려요"],
    ["윤채원", "010-8901-2345", "24414", "강원 춘천시 중앙로 67", ""],
    ["장시우", "010-9012-3456", "54999", "전북 전주시 완산구 전주객사3길 22", ""],
    ["임아린", "010-0123-4567", "44776", "울산 남구 삼산로 282, 1104호", "빠른 배송 부탁드려요"],
  ];
  const rows = people.map((p, i) => {
    const [prod, opt, price] = opts[i % opts.length];
    const qty = (i % 3) + 1;
    return [
      i + 1, `86000${1000 + i}`, `2600000${7000 + i}`, "", "",
      "2026-07-10", prod, opt, `${prod} (${opt})`,
      qty, price, p[0], p[1],
      p[0], p[1], p[2], p[3], p[4],
    ];
  });
  const ws = XLSX.utils.aoa_to_sheet([header, ...rows]);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "발주서");
  loadWorkbook(wb, "샘플_쿠팡_발주서.xlsx");
}

document.addEventListener("DOMContentLoaded", initUpload);
