import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, actionRequest } from './services/api';
import { parseCommand } from './commands';
import GameMap from './components/GameMap';
import StatusPanel from './components/StatusPanel';
import DiceLog, { describeEvent } from './components/DiceLog';
import ChatPanel from './components/ChatPanel';
import './App.css';

// The endpoint contracts are known; service return-envelope shapes still need confirmation.
function getState(payload) {
  if (payload?.session_id && payload?.map && payload?.party) return payload;
  for (const item of [payload?.state, payload?.game_state, payload?.new_state, payload?.updated_state, payload?.game]) {
    if (item?.session_id && item?.map && item?.party) return item;
  }
  return null;
}
function extractResults(payload) {
  const candidates = [payload?.turn_results, payload?.results, payload?.turn_result, payload?.result];
  const results = candidates.find(x => x != null);
  if (results) return Array.isArray(results) ? results : [results];
  if (payload?.events && payload?.status) return [payload];
  return [];
}
function toEntries(value) {
  if (Array.isArray(value)) return value.map(v => typeof v === 'string' ? {id:v,label:v} : {
    id: v.id ?? v.ref ?? v.character_id ?? v.room_id ?? v.key ?? v.name,
    label: v.name ?? v.title ?? v.label ?? v.id ?? v.ref ?? v.room_id ?? v.character_id
  }).filter(x=>x.id);
  if (value && typeof value==='object') return Object.entries(value).map(([id,v])=>({id,label:typeof v==='string'?v:(v?.name ?? v?.title ?? id)}));
  return [];
}
function pointsFrom(data) {
  const candidates = [data, data?.reachable, data?.cells, data?.positions, data?.tiles];
  for (const source of candidates) {
    if (!Array.isArray(source)) continue;
    const p=source.map(v => Array.isArray(v) ? {x:Number(v[0]),y:Number(v[1])} : v?.position ? v.position : v).filter(v=>Number.isInteger(v?.x)&&Number.isInteger(v?.y));
    if (p.length || source.length===0) return p;
  }
  return [];
}

export default function App() {
  const [characters,setCharacters]=useState([]);
  const [rooms,setRooms]=useState([]);
  const [character,setCharacter]=useState('barbarian_orc_berserker');
  const [room,setRoom]=useState('demo_cellar');
  const [state,setState]=useState(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [lines,setLines]=useState([{role:'system',text:'Welcome to DND Solo Play. Start a game to load the dungeon.'}]);
  const [events,setEvents]=useState([]);
  const [reachable,setReachable]=useState([]);
  const [saves,setSaves]=useState([]);
  const [selectedSave,setSelectedSave]=useState('');

  const player=state?.party?.find(p=>p.controller==='player') ?? state?.party?.[0] ?? null;
  const activeId=state?.combat?.initiative_order?.[state.combat.active_index]?.id;
  const activeActor=[...(state?.party || []),...(state?.enemies || [])].find(c=>c.id===activeId);
  const isPlayersTurn=state?.mode!=='combat' || activeId===player?.id;
  const canAct=!!state && !!player?.alive && isPlayersTurn && !['game_over','victory'].includes(state.mode);
  const sessionId=state?.session_id;

  useEffect(()=>{
    Promise.allSettled([api.characters(),api.rooms(),api.saves()]).then(([c,r,s])=>{
      if(c.status==='fulfilled'){const entries=toEntries(c.value);setCharacters(entries);if(entries.length && !entries.some(e=>e.id==='barbarian_orc_berserker'))setCharacter(entries[0].id);}
      if(r.status==='fulfilled'){const entries=toEntries(r.value);setRooms(entries);if(entries.length && !entries.some(e=>e.id==='demo_cellar'))setRoom(entries[0].id);}
      if(s.status==='fulfilled')setSaves(toEntries(s.value));
      if(c.status==='rejected')setError(`Could not fetch characters: ${c.reason.message}`);
    });
  },[]);

  useEffect(()=>{
    if (!sessionId || !player || !canAct) {setReachable([]);return;}
    let disposed=false;
    api.reachable(sessionId,player.id).then(data=>{if(!disposed)setReachable(pointsFrom(data));}).catch(()=>{if(!disposed)setReachable([]);});
    return ()=>{disposed=true};
  },[sessionId,player?.id,state?.turn_counter,state?.mode,state?.combat?.active_index,canAct]);

  const pushLine=useCallback((role,text)=>setLines(old=>[...old,{role,text}].slice(-100)),[]);

  async function newGame() {
    setBusy(true);setError('');
    try {
      const result=await api.start(character,room);
      const next=getState(result);
      if(!next)throw new Error('POST /games returned a different JSON envelope than expected. Please share its response from /docs.');
      setState(next);setEvents(next.recent_events || []);
      setLines([{role:'system',text:next.summary?.text || `You enter ${next.location?.room_id || room}. The adventure begins.`}]);
    } catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function loadGame() {
    if(!selectedSave)return;
    setBusy(true);setError('');
    try {
      const payload=await api.load(selectedSave);
      const next=getState(payload) || getState(await api.state(selectedSave));
      if(!next)throw new Error('Could not read a game state from the load response.');
      setState(next);setEvents(next.recent_events || []);setLines([{role:'system',text:next.summary?.text || 'Saved game loaded.'}]);
    } catch(e){setError(e.message)}finally{setBusy(false)}
  }
  async function saveGame() {
    if(!sessionId)return;
    setBusy(true);setError('');
    try {await api.save(sessionId);pushLine('system','Game saved.');setSaves(toEntries(await api.saves()));}
    catch(e){setError(e.message)}finally{setBusy(false)}
  }
  async function sendActions(actions,rawText='') {
    if(!canAct || !sessionId || !player)return;
    setBusy(true);setError('');
    try {
      const payload=await api.action(sessionId,actionRequest(player.id,actions,rawText));
      const results=extractResults(payload);
      const newEvents=results.flatMap(r=>r.events || []);
      const next=getState(payload) || getState(await api.state(sessionId));
      if(!next)throw new Error('The backend did not return a recognizable game state.');
      setState(next);
      setEvents(old=>[...old,...newEvents].slice(-200));
      const rejected=results.find(r=>r.status==='rejected'||r.status==='needs_clarification');
      if (rejected) pushLine('system',newEvents.find(e=>e.type==='rejected')?.message || rejected.narration_hint || rejected.status);
      else if(newEvents.length)pushLine('system',newEvents.map(describeEvent).join(' · '));
      else pushLine('system','Action submitted; state refreshed.');
      // Do not fabricate LLM narration: integrate Josh's narrator API here later.
    }catch(e){setError(e.message);pushLine('system',`Action failed: ${e.message}`)}finally{setBusy(false)}
  }
  function onMapClick(pos) {
    if(!canAct || busy)return;
    const enemy=state.enemies.find(c=>c.alive&&c.position?.x===pos.x&&c.position?.y===pos.y);
    if(enemy){pushLine('player',`Attack ${enemy.name}`);sendActions([{type:'attack',target_id:enemy.id}],`Attack ${enemy.name}`);return;}
    const feature=state.map.features?.find(f=>f.state!=='hidden'&&f.position?.x===pos.x&&f.position?.y===pos.y);
    if(feature && !reachable.some(p=>p.x===pos.x&&p.y===pos.y)){
      pushLine('system',`Feature: ${feature.kind} (${feature.id}). To interact, type "open ${feature.id}" or another supported verb.`);return;
    }
    const possible=reachable.some(p=>p.x===pos.x&&p.y===pos.y);
    if(state.mode==='combat' && !possible){pushLine('system','That tile is not marked reachable. Choose a green square.');return;}
    pushLine('player',`Move to ${pos.x}, ${pos.y}`);
    sendActions([{type:'move',to:pos}],`Move to ${pos.x}, ${pos.y}`);
  }
  function onSend(text) {
    pushLine('player',text);
    try {sendActions(parseCommand(text,state),text);}catch(e){pushLine('system',e.message);}
  }

  const heading=useMemo(()=>state?.location?.room_id?.replaceAll('_',' ') || 'The Adventure Awaits',[state?.location?.room_id]);
  return <div className="app-shell">
    <header className="topbar"><div><div className="eyebrow">2024 SRD 5.2.1 • SOLO CAMPAIGN</div><h1>⚔ DND Solo Play</h1></div><div className="top-actions"><span className="pill">{state?.mode || 'Not started'}</span><button onClick={saveGame} disabled={!state || busy}>Save Game</button></div></header>
    <section className="setup-bar"><label>Character <select value={character} onChange={e=>setCharacter(e.target.value)}>{(characters.length?characters:[{id:character,label:character}]).map(c=><option key={c.id} value={c.id}>{c.label}</option>)}</select></label><label>Room <select value={room} onChange={e=>setRoom(e.target.value)}>{(rooms.length?rooms:[{id:room,label:room}]).map(r=><option key={r.id} value={r.id}>{r.label}</option>)}</select></label><button className="primary" onClick={newGame} disabled={busy}>New Game</button><label>Saved Games <select value={selectedSave} onChange={e=>setSelectedSave(e.target.value)}><option value="">Choose save</option>{saves.map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></label><button onClick={loadGame} disabled={busy||!selectedSave}>Load</button></section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <main className="main-layout"><section className="panel map-panel"><div className="panel-title"><div><div className="eyebrow">DUNGEON MAP</div><h2>{heading}</h2></div><span className="dim">{state?.map ? `${state.map.width}×${state.map.height} · ${state.map.cell_ft} ft/cell`:''}</span></div><GameMap state={state} reachable={reachable} activePlayerId={player?.id} busy={busy||!canAct} onCell={onMapClick}/><div className="legend"><span><b className="dot blue"/>Player</span><span><b className="dot red"/>Enemy</span><span><b className="dot green"/>Ally</span><span><b className="dot reach"/>Reachable</span><span><b className="dot purple"/>Area origin</span></div><div className="map-help">Click a green square to move or a red enemy token to attack. The engine validates all actions. Spell area markers show origins only, not exact shapes.</div><div className="action-row"><button onClick={()=>sendActions([{type:'end_turn'}],'End turn')} disabled={!canAct||busy}>End Turn</button><button onClick={()=>sendActions([{type:'dodge'}],'Dodge')} disabled={!canAct||busy}>Dodge</button><button onClick={()=>sendActions([{type:'dash'}],'Dash')} disabled={!canAct||busy}>Dash</button></div></section><aside className="panel status-panel"><div className="panel-title"><div className="eyebrow">HERO STATUS</div></div><StatusPanel state={state} player={player} activeActor={activeActor}/></aside><section className="panel chat-panel"><div className="panel-title"><div className="eyebrow">COMMANDS & STORY</div></div><ChatPanel lines={lines} onSend={onSend} disabled={!canAct} busy={busy}/></section><section className="panel dice-panel"><div className="panel-title"><div className="eyebrow">ENGINE EVENT & DICE LOG</div></div><DiceLog events={events}/></section></main>
    <footer>All dice, HP, rules, and movement decisions come from the Python engine. The frontend displays the returned facts.</footer>
  </div>;
}
