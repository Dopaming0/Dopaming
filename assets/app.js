/* 여덟글자 — 명식 계산과 화면 동작
 *
 * 계산 근거
 * - 일주: 1949-10-01(甲子日)을 앵커로, 율리우스 적일(JDN)의 차를 60으로 나눈 나머지가 일진.
 * - 년주: 사주에서 해의 경계는 설날이 아니라 입춘. 정밀 절입 시각 대신 2월 4일로 근사한다.
 * - 시주: 오서둔시법 — 일간이 갑/기이면 갑자시부터, 을/경 병자시, 병/신 무자시,
 *         정/임 경자시, 무/계 임자시. 자시부터 지지 순서대로 천간이 이어진다.
 * - 월주: 절기력(월 단위 절입일)이 필요해 여기서는 계산하지 않는다. 화면에서는 봉인 처리.
 */
'use strict';

(() => {
  /* ── 명리 데이터 ── */
  const STEMS = Object.freeze([
    { han: '甲', ko: '갑', elem: '목', color: 'linear-gradient(160deg,#3d6b4a,#28492f)', epithet: '곧게 뻗은 큰 나무', desc: '굽히기보다 부러지기를 택하는 기상. 시작하는 힘이 남다릅니다.' },
    { han: '乙', ko: '을', elem: '목', color: 'linear-gradient(160deg,#5a8a5f,#3d6b4a)', epithet: '바람에 눕는 들풀', desc: '휘어질지언정 꺾이지 않는, 덩굴의 끈기가 있습니다.' },
    { han: '丙', ko: '병', elem: '화', color: 'linear-gradient(160deg,#a8332e,#7c1f1c)', epithet: '한낮의 태양', desc: '숨겨지지 않는 밝음. 곁의 사람을 데우는 화통함입니다.' },
    { han: '丁', ko: '정', elem: '화', color: 'linear-gradient(160deg,#b95744,#8a3428)', epithet: '어둠 속 등불', desc: '요란하지 않되 꺼지지 않는 불. 남이 못 보는 것을 비춥니다.' },
    { han: '戊', ko: '무', elem: '토', color: 'linear-gradient(160deg,#8c6d2c,#5f4a1e)', epithet: '우뚝 선 큰 산', desc: '풍파에도 자리를 지키는 묵직함. 사람들이 기대는 산의 품입니다.' },
    { han: '己', ko: '기', elem: '토', color: 'linear-gradient(160deg,#a08544,#6e5a2c)', epithet: '기름진 밭', desc: '드러내지 않고 거두는 실속. 품에 든 것을 길러내는 힘입니다.' },
    { han: '庚', ko: '경', elem: '금', color: 'linear-gradient(160deg,#6e6a5f,#47443c)', epithet: '벼려지기 전의 무쇠', desc: '끊을 때 끊는 결단과 의리. 다듬을수록 보검이 되는 그릇입니다.' },
    { han: '辛', ko: '신', elem: '금', color: 'linear-gradient(160deg,#8a847a,#5c584e)', epithet: '세공을 마친 보석', desc: '예리한 감각과 단단한 자존심. 그 빛이 쉬이 바래지 않습니다.' },
    { han: '壬', ko: '임', elem: '수', color: 'linear-gradient(160deg,#33566e,#22394a)', epithet: '넓고 깊은 바다', desc: '가늠하기 어려운 포부. 막히면 돌아가되 끝내 바다에 이릅니다.' },
    { han: '癸', ko: '계', elem: '수', color: 'linear-gradient(160deg,#4a708c,#33566e)', epithet: '만물을 적시는 봄비', desc: '소리 없이 스며드는 총명함. 어느새 싹을 틔우는 힘입니다.' },
  ]);
  const BRANCHES = Object.freeze(['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']);
  const BRANCHES_KO = Object.freeze(['자', '축', '인', '묘', '진', '사', '오', '미', '신', '유', '술', '해']);
  const BRANCH_ELEM = Object.freeze(['수', '토', '목', '목', '토', '화', '화', '토', '금', '금', '토', '수']);
  const ZODIAC = Object.freeze(['쥐', '소', '호랑이', '토끼', '용', '뱀', '말', '양', '원숭이', '닭', '개', '돼지']);
  const ELEM_HAN = Object.freeze({ 목: '木', 화: '火', 토: '土', 금: '金', 수: '水' });
  const ELEM_CLASS = Object.freeze({ 목: 'e-wood', 화: 'e-fire', 토: 'e-earth', 금: 'e-metal', 수: 'e-water' });

  const ANCHOR_JDN = 2433191; // 1949-10-01 = 갑자일(甲子日)

  /* ── 계산 (순수 함수) ── */

  /** 그레고리력 날짜의 율리우스 적일. */
  function julianDayNumber(y, m, d) {
    return Math.floor(Date.UTC(y, m - 1, d) / 86400000) + 2440588;
  }

  /**
   * 명식을 세운다.
   * @param {number} y 양력 연도
   * @param {number} m 월(1-12)
   * @param {number} d 일
   * @param {number} hourBranch 시지(0=자 ~ 11=해), 모르면 -1
   * @returns {{ day: {stem,branch}, year: {stem,branch}, hour: {stem,branch}|null }}
   */
  function buildMyeongsik(y, m, d, hourBranch) {
    const dayIdx = ((julianDayNumber(y, m, d) - ANCHOR_JDN) % 60 + 60) % 60;
    const dayStem = dayIdx % 10;

    let zy = y;
    if (m < 2 || (m === 2 && d < 4)) zy -= 1; // 입춘 전은 전년도

    return {
      day: { stem: dayStem, branch: dayIdx % 12 },
      year: { stem: ((zy - 4) % 10 + 10) % 10, branch: ((zy - 4) % 12 + 12) % 12 },
      hour: hourBranch >= 0
        ? { stem: ((dayStem % 5) * 2 + hourBranch) % 10, branch: hourBranch }
        : null,
    };
  }

  /* ── 화면 ── */
  const $ = (id) => document.getElementById(id);
  const el = {
    form: $('tasteForm'), birth: $('birth'), btime: $('btime'),
    result: $('result'), msHour: $('msHour'), msDay: $('msDay'), msYear: $('msYear'),
    rSeal: $('rSeal'), rName: $('rName'), rEpithet: $('rEpithet'),
    rDesc: $('rDesc'), rNote: $('rNote'), ctaBtn: $('ctaBtn'),
  };

  // 스크롤 등장
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add('on'); io.unobserve(e.target); }
    }
  }, { threshold: 0.15 });
  document.querySelectorAll('.reveal').forEach((node) => io.observe(node));

  /** 명식표 한 칸: 천간·지지를 오행 색으로 칠한다. */
  function pillarHTML(pillar) {
    const s = STEMS[pillar.stem];
    const branchElem = BRANCH_ELEM[pillar.branch];
    return `<div>
      <span class="${ELEM_CLASS[s.elem]}">${s.han}</span><br/>
      <span class="${ELEM_CLASS[branchElem]}">${BRANCHES[pillar.branch]}</span>
      <small>${s.ko}${BRANCHES_KO[pillar.branch]}</small>
    </div>`;
  }

  function showNote(text) {
    el.rNote.textContent = text;
    el.rNote.style.display = text ? 'block' : 'none';
  }

  el.form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const [y, m, d] = el.birth.value.split('-').map(Number);
    if (!y || !m || !d) { el.birth.focus(); return; }

    const ms = buildMyeongsik(y, m, d, Number(el.btime.value));
    const dayStem = STEMS[ms.day.stem];

    el.msDay.innerHTML = pillarHTML(ms.day);
    el.msYear.innerHTML = pillarHTML(ms.year);

    if (ms.hour) {
      el.msHour.innerHTML = pillarHTML(ms.hour);
      showNote(ms.hour.branch === 0
        ? '* 자시(子時) 출생은 날짜를 두고 논쟁이 있어, 상담에서 정밀히 다시 세웁니다.'
        : '');
    } else {
      el.msHour.innerHTML = '<div class="ms-unknown">?<br/>?<small>시각 미상</small></div>';
      showNote('* 시각은 출생증명서가 기억하는 경우가 많습니다. 확인되면 마저 세워드립니다.');
    }

    el.rSeal.textContent = dayStem.han + ELEM_HAN[dayStem.elem];
    el.rSeal.style.background = dayStem.color;
    el.rName.textContent =
      `일간 ${dayStem.ko}${dayStem.elem}(${dayStem.han}${ELEM_HAN[dayStem.elem]}) · ` +
      `${STEMS[ms.year.stem].han}${BRANCHES[ms.year.branch]}년 ${ZODIAC[ms.year.branch]}띠`;
    el.rEpithet.textContent = `"${dayStem.epithet}"`;
    el.rDesc.textContent = dayStem.desc;

    // 등장 연출을 처음부터 다시 재생
    el.result.classList.remove('show');
    void el.result.offsetWidth;
    el.result.classList.add('show');
  });

  // 맨 아래 CTA → 맛보기 입력으로 유도
  el.ctaBtn.addEventListener('click', () => {
    setTimeout(() => el.birth.focus({ preventScroll: true }), 700);
  });
})();
