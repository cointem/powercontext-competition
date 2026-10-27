'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const STORAGE = new URLSearchParams(location.search).get('playtest') === '1' ? 'grudge-inn-playtest-v1' : 'grudge-inn-journeys-v1';
const uuid = () => crypto.randomUUID().replaceAll('-', '');
const actionInfo = {
  '冒充食评家': ['掌柜', '评', '借个名头，讨一碗面'],
  '借二十文': ['跑堂', '借', '借来盘缠，也欠下承诺'],
  '帮跑堂': ['跑堂', '助', '接住那摞快掉的碗'],
  '帮掌柜': ['掌柜', '账', '替他核对一页账簿'],
  '归还借款': ['跑堂', '还', '把欠下的二十文还清'],
  '坦白道歉': ['掌柜', '诚', '放下名头，说句实话'],
  '找神秘客': ['神秘客', '帖', '循着人情，求一封推荐'],
  '申请入住': ['掌柜', '宿', '请掌柜决定今夜的归处'],
};
const eventInfo = {
  critic: ['掌柜', '一句谎言', '名头记下了'], borrow: ['跑堂', '二十文借款', '有借，须有还'],
  help_clerk: ['跑堂', '一场援手', '人情记下了'], help_owner: ['掌柜', '核对账簿', '认真做事的人'],
  repay: ['跑堂', '借款还清', '承诺已兑现'], apology: ['掌柜', '坦白往事', '诚意记下了'],
  recommend: ['神秘客', '一封推荐', '有人为你担保'], checkout: ['掌柜', '今夜的归处', '此夜落笔'],
};
let book, game, busy = false, filter = 'all', mode = '规则试玩', lastRun = '', storageOK = true, externalChange = false;
function makeSave(number) { return { id: uuid().slice(0,24), name: `第 ${number} 场雨夜`, created: Date.now(), round:0, ending:'', pending:null }; }
function writeBook() {
  try { localStorage.setItem(STORAGE, JSON.stringify(book)); }
  catch { storageOK = false; throw new Error('浏览器无法保存续玩凭据。请允许此网站使用本地存储，再重新打开。'); }
}
function readBook() {
  const saved = localStorage.getItem(STORAGE);
  if (saved) {
    const parsed = JSON.parse(saved);
    if (!/^[a-f0-9]{32}$/.test(parsed.player) || !Array.isArray(parsed.saves) || !parsed.saves.length || !parsed.saves.some(s => s.id === parsed.active)) throw new Error('本地旅程记录无法读取。请保留网站数据，不要清空；需要检查存档。');
    return parsed;
  }
  const first = makeSave(1);
  return { player: uuid(), active:first.id, saves:[first] };
}
function activeSave() { return book.saves.find(s => s.id === book.active); }
function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
function showError(message) { $('#error-text').textContent = message; $('#error-banner').hidden = false; $('#save-status').textContent = '等待核对本轮结果'; }
function setBusy(value, label = '正在向客栈打听…') {
  busy = value; document.body.classList.toggle('busy', value);
  $$('.action, #observe, #recall, #new-game, #ending-new, #saves-button, .save-option').forEach(el => { el.disabled = value || !storageOK; });
  $('#retry').disabled = value || !storageOK;
  $('#save-status').textContent = value ? label : '进度已记入账簿';
  if(value) $('#story-status').textContent = '正在核对账簿，请稍候…';
  $('#actions').setAttribute('aria-busy', String(value));
}
function relationLabel(value) { return value > 1 ? '亲近' : value > 0 ? '友善' : value < 0 ? '存疑' : '初见'; }
function render() {
  if (!game) return;
  const s = game.state;
  $('#coins').textContent = s.coins; $('#debt').textContent = s.debt;
  $('#reputation').textContent = s.reputation > 0 ? '+' + s.reputation : s.reputation;
  $('#reputation-label').textContent = s.reputation >= 2 ? '渐得人心' : s.reputation < 0 ? '惹人生疑' : '尚无盛名';
  $('#turns').textContent = s.ending ? 0 : Math.max(0,6-s.round);
  $('#turn-dots').replaceChildren(...Array.from({length:6},(_,i)=>node('i',undefined,i<s.round?'used':'')));
  for (const [name,id] of [['掌柜','owner'],['跑堂','clerk'],['神秘客','guest']]) {
    $('#rel-'+id).textContent = relationLabel(s.relationships[name]);
    $('#rel-'+id).title = `关系 ${s.relationships[name]}`;
  }
  const actor = eventInfo[game.event]?.[0] || '客栈见闻';
  $('#speaker').textContent = actor;
  $('#speaker-note').textContent = actor === '掌柜' ? '算盘声停了片刻' : actor === '跑堂' ? '小事，也会放在心上' : actor === '神秘客' ? '角落里的人抬起了头' : '门外风雨，门内人情';
  $('#story').textContent = game.story;
  $('#narration').hidden = !game.narration;
  $('#narration').textContent = game.narration || '';
  $('#scene-chapter').textContent = s.ending ? '今夜 / 已落幕' : `今夜 / 第 ${s.round} 次行动`;
  $('.scene-copy h2').textContent = s.ending ? (s.ending==='被赶出门'?'这一夜，未能留下。':'今夜，有了归处。') : '今夜，留得下吗？';
  $('#round-label').textContent = s.ending ? '终 · 今夜落笔' : s.round ? `第 ${s.round} 回 / 共六回` : '序 · 推门入店';
  $('#story-status').textContent = game.replayed ? '已核对保存记录，本轮没有重复记账' : s.ending ? '故事已存好，旧账仍可翻阅' : game.event === 'help_clerk' ? '人情已记下。翻一翻旧账，看看他想起了什么。' : '每一次选择，都会留下痕迹';
  $('#ending').hidden = !s.ending;
  $('#ending-title').textContent = s.ending;
  $('#ending-detail').textContent = {'贵宾入住':'天字一号房已为你留灯。你赢来的，是一份信任。','打工抵债':'后院有一张床。明早开门，还能续上这份人情。','被赶出门':'雨还在下。下一次进门，换一种选择试试。'}[s.ending] || '';
  renderActions(); renderMemories();
  $('#observe').disabled = busy; $('#recall').disabled = busy;
}
function renderActions() {
  $('#actions').replaceChildren();
  const list = (game?.available || []).filter(name => actionInfo[name] && (filter === 'all' || actionInfo[name][0] === filter));
  for (const name of list) {
    const [,icon,description] = actionInfo[name];
    const button = node('button', undefined, 'action'); button.type = 'button'; button.dataset.action = name;
    const text = node('span',undefined,'action-text'); text.append(node('b',name),node('small',description));
    button.append(node('span',icon,'action-icon'),text,node('span','↗','action-arrow'));
    button.disabled = busy || !!activeSave()?.pending;
    button.addEventListener('click',()=>turn(name)); $('#actions').append(button);
  }
  $('#empty-actions').hidden = !!list.length;
  $('#empty-actions').textContent = game?.state.ending ? '此夜已经落幕。可以翻翻旧账，或开启新的旅程。' : '此刻没有可做的事。先与其他人打个交道吧。';
  $$('.filters button').forEach(button=>{ const selected = button.dataset.filter===filter; button.classList.toggle('selected',selected); button.setAttribute('aria-pressed',String(selected)); });
  $$('.person').forEach(button=>{ const person = button.dataset.person === 'all' ? '掌柜' : button.dataset.person; const selected = person===filter; button.classList.toggle('selected',selected); button.setAttribute('aria-pressed',String(selected)); });
}
function renderMemories() {
  const memories = game.recalled || [];
  $('#memory-count').textContent = memories.length; $('#ledger-total').textContent = memories.length ? memories.length + ' 笔' : '零笔';
  if (!memories.length) {
    const empty=node('div',undefined,'memory-empty'); empty.append(node('span','〰'),node('p','纸上还是空白。'),node('small','借一笔钱，帮一个忙。你的故事，就从这里落笔。'));
    $('#memories').replaceChildren(empty); return;
  }
  $('#memories').replaceChildren(...memories.map((memory,index)=>{
    const [person,title,tag] = eventInfo[memory.event] || ['客栈','一段往事','已记住'];
    const item=node('article',undefined,'memory-item');
    const meta=node('div',undefined,'memory-meta');meta.append(node('b',person+' · '+title),node('span',String(index+1).padStart(2,'0')));
    item.append(meta,node('p',memory.text),node('span',tag,'memory-tag'));return item;
  }));
}
async function requestTurn(input) {
  const response=await fetch('/api/turn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input),signal:AbortSignal.timeout(130000)});
  let data;try { data=await response.json(); } catch { throw new Error('未收到完整回应，请重试本轮确认进度。'); }
  if(!response.ok) throw new Error(data.error || '客栈暂时没有回应，请稍候重试。');
  if(!data.game?.state || data.request_id!==input.request_id) throw new Error('账本回应不完整，请重试本轮确认进度。');
  lastRun=data.run_id; return data.game;
}
async function turn(action, retry=false) {
  if(busy || !storageOK || !book) return;
  if(externalChange) {
    if(!retry) {showError('另一个窗口更新了旅程。请先同步账簿，再选择行动。');return;}
    try {book=readBook();externalChange=false;} catch(error){showError(error.message);return;}
    action='进店看看';
  }
  const save=activeSave();
  if(save.pending && !retry) { showError('上一轮结果尚未确认。请先重试本轮，核对账簿后再行动。'); return; }
  setBusy(true);$('#error-banner').hidden=true;
  try {
    const input=retry && save.pending ? save.pending : {player_id:book.player,save_id:save.id,request_id:uuid(),action,line:mode==='AI演出'?$('#player-line').value:''};
    save.pending=input;writeBook();
    const result=await requestTurn(input);
    game=result;save.pending=null;save.round=result.state.round;save.ending=result.state.ending;writeBook();
    $('#player-line').value='';
    // Read after a saved action: the ledger displays genuine recalled events,
    // and newly recorded help can unlock the next action through Dify's rules.
    let recallFailed=false;
    if(result.consumed) {
      $('#save-status').textContent='已记账，正在翻阅旧事…';
      try {
        const recalled=await requestTurn({player_id:book.player,save_id:save.id,request_id:uuid(),action:'回忆旧账',line:''});
        game={...result,state:recalled.state,recalled:recalled.recalled,available:recalled.available};
      } catch { recallFailed=true; }
    }
    render();setBusy(false);
    if(recallFailed)$('#story-status').textContent='行动已保存；可稍后翻账，查看最新记忆。';
    if(action==='回忆旧账') openLedger();
  } catch(error) {
    setBusy(false);
    // Pending requests keep the same identifier across retry and page reload.
    $$('.action, #observe, #recall, #new-game, #ending-new, #saves-button').forEach(el=>{el.disabled=true;});
    showError(error.name==='TimeoutError'?'回应比平时慢。请重试本轮，核对是否已经保存。':error.message);
  }
}
function renderSaves() {
  $('#save-list').replaceChildren(...book.saves.slice().reverse().map(save=>{
    const button=node('button',undefined,'save-option'+(save.id===book.active?' current':''));
    const description=node('span',save.name);description.append(node('small',save.ending || `已行动 ${save.round}/6 · ${new Date(save.created).toLocaleDateString('zh-CN')}`));
    button.append(description,node('span',save.id===book.active?'当前':'续玩 ↗'));
    button.disabled=busy;
    button.addEventListener('click',async()=>{
      if(busy)return;book.active=save.id;filter='all';game=null;
      try { writeBook(); } catch(error) {showError(error.message);return;}
      $('#saves-dialog').close();await turn('进店看看',!!save.pending);
    });return button;
  }));
}
async function newGame() {
  if(busy||activeSave()?.pending)return;
  const save=makeSave(book.saves.length+1);book.saves.push(save);book.active=save.id;game=null;filter='all';
  try{writeBook();}catch(error){showError(error.message);return;}
  $('#saves-dialog').close();$('#actions').replaceChildren();await turn('进店看看');

}
$$('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;renderActions();}));
$$('.person').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.person==='all'?'掌柜':button.dataset.person;renderActions();}));
$('#help-button').addEventListener('click',()=>$('#help-dialog').showModal());
$('#saves-button').addEventListener('click',()=>{renderSaves();$('#saves-dialog').showModal();});
$$('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('#new-game').addEventListener('click',newGame);$('#ending-new').addEventListener('click',newGame);
$('#observe').addEventListener('click',()=>turn('进店看看'));$('#recall').addEventListener('click',()=>turn('回忆旧账'));
$('#retry').addEventListener('click',()=>book?turn('进店看看',true):location.reload());
window.addEventListener('storage',event=>{
  if(event.key===STORAGE&&!busy) {
    externalChange=true;
    showError('另一个窗口更新了旅程。请点击重试，同步最新账簿后继续。');
  }
});
async function start() {
  try {
    book=readBook();writeBook();
    const response=await fetch('/api/config');if(!response.ok)throw new Error('无法读取客栈配置');
    const config=await response.json();mode=config.mode;
    $('#mode-label').textContent=`${mode==='AI演出'?'AI 即兴对白':'本地规则试玩'} · Dify × PowerContext`;
    $('#line-wrap').hidden=mode!=='AI演出';
    if(mode==='AI演出') $('.dialog-note').textContent='进度保存在记忆服务中，浏览器保留续玩凭据。请使用同一浏览器；清除网站数据会丢失续玩入口。AI 负责即兴对白，Dify 规则决定账本与结局。';
    if(!config.configured)throw new Error('客栈还没有接通 Dify。请先配置服务器的应用 API 密钥。');
    await turn('进店看看',!!activeSave().pending);
  }catch(error){showError(error.message);$('#story').textContent='账簿暂时没能打开。连接恢复后，可以继续这场雨夜。';}
}
// The game stage keeps narration and characters inside the scene, above actions.
$('.scene').append($('.people'),$('.dialogue'));
function openLedger(){
  document.body.classList.add('ledger-open');$('#ledger-backdrop').hidden=false;
  $('#ledger').setAttribute('role','dialog');$('#ledger').setAttribute('aria-modal','true');$('#ledger').setAttribute('aria-label','旧账簿');
  $('#ledger-button').setAttribute('aria-expanded','true');
  $$('.header,.welcome,.stats,.main-column,footer').forEach(el=>{el.inert=true;});
  $('#close-ledger').focus();
}
function closeLedger(){
  if(!document.body.classList.contains('ledger-open'))return;
  document.body.classList.remove('ledger-open');$('#ledger-backdrop').hidden=true;
  $('#ledger').removeAttribute('role');$('#ledger').removeAttribute('aria-modal');
  $('#ledger-button').setAttribute('aria-expanded','false');
  $$('.header,.welcome,.stats,.main-column,footer').forEach(el=>{el.inert=false;});
  $('#ledger-button').focus();
}
$('#ledger-button').addEventListener('click',openLedger);
$('#close-ledger').addEventListener('click',closeLedger);
$('#ledger-backdrop').addEventListener('click',closeLedger);
document.addEventListener('keydown',event=>{
  if(!document.body.classList.contains('ledger-open'))return;
  if(event.key==='Escape')closeLedger();
  if(event.key==='Tab'){
    const targets=$$('#ledger button:not(:disabled),#ledger [tabindex="0"]');
    const first=targets[0],last=targets.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
});
start();
