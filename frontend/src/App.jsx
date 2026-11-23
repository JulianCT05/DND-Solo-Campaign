import { useEffect, useRef, useState } from 'react';
import { api, actionRequest } from './services/api';
import { parseCommand } from './commands';
import StoryPanel from './components/StoryPanel';
import CharacterBoard from './components/CharacterBoard';
import TurnTimeline from './components/TurnTimeline';
import ActionComposer from './components/ActionComposer';
import './App.css';

function getState(payload) {
  if (payload?.session_id && payload?.party && payload?.map) return payload;
  for (const value of [payload?.state, payload?.game_state, payload?.updated_state, payload?.new_state, payload?.game]) {
    if (value?.session_id && value?.party && value?.map) return value;
  }
  return null;
}
function getResults(payload) {
  const found = [payload?.turn_results, payload?.results, payload?.turn_result, payload?.result].find(x => x != null);
  if (found) return Array.isArray(found) ? found : [found];
  return payload?.events ? [payload] : [];
}
// Preserve event order while splitting reactions / automatic turns by the actor
// that actually performed each event, not only the submitted player action.
function splitTurnsByActor(results) {
  const cards = [];
  for (const result of results) {
    let card = null;
    for (const event of result.events || []) {
      const actor = event.actor_id || card?.actor_id || result.actor_id;
      if (!card || card.actor_id !== actor) {
        card = { actor_id: actor, turn: result.turn, status: result.status, events: [] };
        cards.push(card);
      }
      card.events.push(event);
    }
    if (!(result.events || []).length) cards.push({ ...result, events: [] });
  }
  return cards;
}

function toOptions(payload, keys) {
  const value = Array.isArray(payload) ? payload : keys.map(k => payload?.[k]).find(Array.isArray) || payload;
  if (Array.isArray(value)) return value.map(item => typeof item === 'string' ? { id: item, label: item } : {
    id: item.id ?? item.ref ?? item.character_id ?? item.room_id ?? item.session_id ?? item.key ?? item.name,
    label: item.name ?? item.title ?? item.label ?? item.id ?? item.ref ?? item.room_id ?? item.session_id,
  }).filter(item=>item.id);
  if (value && typeof value === 'object') return Object.entries(value).map(([id, v])=>({id, label:typeof v === 'string' ? v : v?.name ?? v?.title ?? id}));
  return [];
}

export default function App() {
  const [characters, setCharacters] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [characterId, setCharacterId] = useState('barbarian_orc_berserker');
  const [roomId, setRoomId] = useState('demo_cellar');
  const [game, setGame] = useState(null);
  const [turns, setTurns] = useState([]);
  const [latestEvents, setLatestEvents] = useState([]);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [savedGames, setSavedGames] = useState([]);
  const [selectedSave, setSelectedSave] = useState('');
  const idCounter = useRef(0);

  const player = game?.party?.find(c => c.controller === 'player') || game?.party?.[0];
  const activeId = game?.combat?.initiative_order?.[game.combat.active_index]?.id;
  const yourTurn = game?.mode !== 'combat' || activeId === player?.id;
  const canAct = !!game && !!player?.alive && yourTurn && !['victory', 'game_over'].includes(game.mode);

  useEffect(() => {
    Promise.allSettled([api.characters(),api.rooms(),api.saves()]).then(([c,r,s]) => {
      if (c.status === 'fulfilled') {
        const options = toOptions(c.value, ['characters', 'pregens']);
        setCharacters(options);
        if (options.length && !options.some(x=>x.id==='barbarian_orc_berserker')) setCharacterId(options[0].id);
      }
      if (r.status === 'fulfilled') {
        const options = toOptions(r.value, ['rooms']);
        setRooms(options);
        if (options.length && !options.some(x=>x.id==='demo_cellar')) setRoomId(options[0].id);
      }
      if (s.status === 'fulfilled') setSavedGames(toOptions(s.value,['games','saves']));
      if (c.status === 'rejected') setError(`Cannot load characters: ${c.reason.message}`);
    });
  }, []);

  async function newGame() {
    setBusy(true); setError(''); setFeedback('');
    try {
      const result = await api.start(characterId, roomId);
      const next = getState(result);
      if (!next) throw new Error('The server returned an unexpected game state. Check POST /games in /docs.');
      setGame(next); setSelectedTarget(''); setLatestEvents(next.recent_events || []); setTurns([]);
      setFeedback('Your adventure has started. The story comes from the engine game state.');
    } catch(e) { setError(e.message); } finally { setBusy(false); }
  }

  async function submitActions(actions, rawText = '') {
    if (!canAct || busy || !player || !game?.session_id) return;
    setBusy(true); setError(''); setFeedback('');
    try {
      const payload = await api.action(game.session_id, actionRequest(player.id, actions, rawText));
      const results = getResults(payload);
      const events = results.flatMap(r=>r.events || []);
      const state = getState(payload) || getState(await api.state(game.session_id));
      if (!state) throw new Error('The engine response did not contain a valid game state.');
      setGame(state);
      setLatestEvents(events);
      if (results.length) {
        setTurns(old => [...old, ...splitTurnsByActor(results).map(r => ({...r,localId:++idCounter.current}))].slice(-120));
      } else if (events.length) {
        setTurns(old => [...old, { localId:++idCounter.current, actor_id: player.id, events, status:'accepted' }].slice(-120));
      }
      const problem = events.find(e=>e.type==='rejected');
      if (problem) setFeedback(problem.message);
      else if (results.some(r=>r.status==='needs_clarification')) setFeedback('The parser needs clarification.');
      else if (!events.length) setFeedback('Action received; the game state was refreshed.');
      else setFeedback(`${results.length || 1} character turn result(s) displayed below.`);
    } catch(e) { setError(e.message); } finally { setBusy(false); }
  }
  function typedCommand(text) {
    try { submitActions(parseCommand(text, game), text); }
    catch(e) { setFeedback(e.message); }
  }
  async function saveGame() {
    if (!game?.session_id) return;
    setBusy(true); setError('');
    try { await api.save(game.session_id); setFeedback('Game saved.'); setSavedGames(toOptions(await api.saves(),['games','saves'])); }
    catch(e) { setError(e.message); } finally { setBusy(false); }
  }
  async function loadGame() {
    if (!selectedSave) return;
    setBusy(true); setError(''); setFeedback('');
    try {
      const payload = await api.load(selectedSave);
      const next = getState(payload) || getState(await api.state(selectedSave));
      if (!next) throw new Error('The server did not return a game state.');
      setGame(next); setTurns([]); setSelectedTarget(''); setLatestEvents(next.recent_events || []);
      setFeedback('Saved game loaded. The timeline starts fresh; it does not invent previous events.');
    } catch(e) { setError(e.message); } finally { setBusy(false); }
  }

  return <div className="app-shell">
    <header className="topbar"><div><div className="eyebrow">2024 SRD 5.2.1 · TEXT-BASED ADVENTURE</div><h1>DND <span>Solo Play</span></h1><p className="tagline">Your story. One move at a time.</p></div><div className="header-right"><span className="pill">{game?.mode?.replaceAll('_',' ') || 'Not started'}</span>{game?.combat && <span className="round-pill">Round {game.combat.round}</span>}</div></header>
    <div className="setup-bar"><label>Character<select value={characterId} onChange={e=>setCharacterId(e.target.value)}>{(characters.length ? characters : [{id:characterId,label:characterId}]).map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></label><label>Adventure room<select value={roomId} onChange={e=>setRoomId(e.target.value)}>{(rooms.length ? rooms : [{id:roomId,label:roomId}]).map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></label><button className="primary" onClick={newGame} disabled={busy}>New Game</button><label>Saved game<select value={selectedSave} onChange={e=>setSelectedSave(e.target.value)}><option value="">Select save</option>{savedGames.map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></label><button onClick={loadGame} disabled={busy || !selectedSave}>Load</button><button onClick={saveGame} disabled={busy || !game}>Save</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {feedback && <div className="feedback" role="status">{feedback}</div>}
    <div className="dashboard"><div className="column-main"><StoryPanel state={game} latestEvents={latestEvents}/><TurnTimeline state={game} turns={turns}/></div><div className="column-side"><CharacterBoard state={game} selectedTarget={selectedTarget} onSelectTarget={c=>setSelectedTarget(c.id)}/><ActionComposer state={game} selectedTarget={selectedTarget} busy={busy} disabled={!canAct} onSubmit={submitActions} onCommand={typedCommand}/></div></div>
    <footer>Game outcomes, moves, HP and rolls are produced by the Python engine; the GUI only presents them. Josh's AI narration can be connected once its API is available.</footer>
  </div>;
}